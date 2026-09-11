"""
Flight Finder - voli economici da Venezia (VCE) e Treviso (TSF).

I dati arrivano da Google Flights tramite due librerie non ufficiali usate in
cascata (vedi flightcore/engine.py). Nessuna chiave API, nessuna registrazione.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from flightcore import airports, consent
from flightcore.cache import search_cache
from flightcore.engine import Engine
from flightcore.models import SearchQuery
from flightcore.ratelimit import limiter

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("flight-finder")

app = Flask(__name__)

COUNTRY = os.getenv("GOOGLE_COUNTRY", "IT")
LANGUAGE = os.getenv("GOOGLE_LANGUAGE", "it")
CURRENCY = os.getenv("CURRENCY", "EUR")

# Il consenso cookie va ottenuto prima della prima richiesta: da un IP europeo
# senza di esso Google risponde con la pagina di consenso e ogni ricerca torna
# vuota senza errori. La cache su disco evita di rifarlo a ogni avvio.
consent.install(country=COUNTRY, currency=CURRENCY)

engine = Engine(country=COUNTRY, language=LANGUAGE)


# ---------------------------------------------------------------------- #
# Pagine
# ---------------------------------------------------------------------- #


@app.route("/")
def index():
    return render_template(
        "index.html",
        airports=airports.HOME_AIRPORTS,
        destination_sets=airports.destination_sets_info(),
        default_set=airports.DEFAULT_DESTINATION_SET,
        currency=CURRENCY,
        # La pagina stima la durata di una scansione: deve usare il ritmo vero
        # del limitatore, non un numero scritto a mano che invecchia da solo.
        req_per_sec=limiter.rate,
    )


# ---------------------------------------------------------------------- #
# API
# ---------------------------------------------------------------------- #


class BadRequest(Exception):
    pass


@app.errorhandler(BadRequest)
def _bad_request(exc: BadRequest):
    return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/search", methods=["POST"])
def api_search():
    """
    Ricerca voli su una rotta, eventualmente da piu' aeroporti di partenza.

    Con destination assente o "anywhere" la richiesta viene dirottata su
    /api/explore, che scandisce una rosa di destinazioni.
    """
    data = request.get_json(silent=True) or {}
    origins = _origins(data)
    destination = (data.get("destination") or "").strip().upper()

    if not destination or destination == "ANYWHERE":
        return api_explore()

    if not airports.exists(destination):
        raise BadRequest(f"Destinazione sconosciuta: {destination}")

    query = _build_query(data, origin=origins[0], destination=destination)
    offers, providers = engine.search_many_origins(origins, query)

    return jsonify(
        {
            "success": True,
            "mode": "route",
            "notice": engine.diagnosis() if not offers else None,
            "count": len(offers),
            "origins": origins,
            "destination": destination,
            "destination_name": airports.name(destination),
            "providers": providers,
            "flights": [o.to_dict() for o in offers],
        }
    )


@app.route("/api/explore", methods=["POST"])
def api_explore():
    """Ricerca "ovunque": migliore offerta per ciascuna destinazione della rosa."""
    data = request.get_json(silent=True) or {}
    origins = _origins(data)

    by_origin = airports.resolve_destinations(
        origins,
        set_key=data.get("destination_set"),
        explicit=_destination_list(data.get("destinations")),
    )

    # Il tetto vale per aeroporto di partenza: e' quello che governa la durata
    # della scansione, visto che ogni coppia origine/meta e' una richiesta.
    limit = _int(data.get("max_destinations"), default=150, low=1, high=200)
    by_origin = {origin: codes[:limit] for origin, codes in by_origin.items()}

    prima = next((c for codes in by_origin.values() for c in codes), None)
    if prima is None:
        raise BadRequest("Nessuna destinazione da esaminare con questi criteri")

    query = _build_query(data, origin=origins[0], destination=prima)
    offers, providers = engine.explore(by_origin, query)

    esaminate = sorted({c for codes in by_origin.values() for c in codes})
    return jsonify(
        {
            "success": True,
            "mode": "explore",
            "notice": engine.diagnosis() if not offers else None,
            "count": len(offers),
            "origins": origins,
            "searched_destinations": esaminate,
            "destinations_by_origin": by_origin,
            "searches": sum(len(c) for c in by_origin.values()),
            "providers": providers,
            "flights": [
                {**o.to_dict(), "destination_name": airports.name(o.destination)} for o in offers
            ],
        }
    )


@app.route("/api/cheapest-dates", methods=["POST"])
def api_cheapest_dates():
    """Calendario prezzi: quanto costa la stessa rotta giorno per giorno."""
    data = request.get_json(silent=True) or {}
    origin = (data.get("origin") or "VCE").upper()
    destination = (data.get("destination") or "").upper()

    if not airports.exists(origin):
        raise BadRequest(f"Aeroporto di partenza sconosciuto: {origin}")
    if not destination or not airports.exists(destination):
        raise BadRequest("Serve una destinazione per il calendario prezzi")

    today = date.today()
    from_date = _date(data.get("from_date"), default=today + timedelta(days=7))
    to_date = _date(data.get("to_date"), default=today + timedelta(days=60))
    if from_date > to_date:
        raise BadRequest("La data iniziale e' successiva a quella finale")

    dates, provider = engine.cheapest_dates(
        origin,
        destination,
        from_date.isoformat(),
        to_date.isoformat(),
        trip_days=_int(data.get("trip_days"), default=None, low=1, high=30),
        adults=_int(data.get("adults"), default=1, low=1, high=9),
        max_stops=_max_stops(data),
        currency=CURRENCY,
    )

    return jsonify(
        {
            "success": True,
            "count": len(dates),
            "origin": origin,
            "destination": destination,
            "provider": provider,
            "cheapest": min((d.price for d in dates), default=None),
            "dates": [d.to_dict() for d in dates],
        }
    )


@app.route("/api/airports", methods=["GET"])
def api_airports():
    """Autocomplete aeroporti, servito da un catalogo locale di 7.800 voci."""
    keyword = request.args.get("q") or request.args.get("keyword") or ""
    return jsonify({"success": True, "airports": airports.search(keyword, limit=10)})


@app.route("/api/health", methods=["GET"])
def api_health():
    """Stato dei provider: utile per capire se Google sta rifiutando le richieste."""
    return jsonify(
        {
            "success": True,
            "providers": engine.provider_names,
            "breaker": engine.breaker.status(),
            "consent_cookies": sorted(consent.get_cookies(country=COUNTRY, currency=CURRENCY)),
            "country": COUNTRY,
            "currency": CURRENCY,
        }
    )


@app.route("/api/cache", methods=["DELETE"])
def api_clear_cache():
    search_cache.clear()
    engine.breaker.reset()
    return jsonify({"success": True, "message": "Cache e circuit breaker azzerati"})


# ---------------------------------------------------------------------- #
# Parsing della richiesta
# ---------------------------------------------------------------------- #


def _origins(data: dict) -> list[str]:
    raw = data.get("origins") or data.get("origin") or "VCE"
    codes = [raw] if isinstance(raw, str) else list(raw)
    origins: list[str] = []
    for raw_code in codes:
        code = str(raw_code).strip().upper()
        if not airports.exists(code):
            raise BadRequest(f"Aeroporto di partenza sconosciuto: {code}")
        if code not in origins:
            origins.append(code)
    if not origins:
        raise BadRequest("Serve almeno un aeroporto di partenza")
    return origins


def _destination_list(raw) -> list[str] | None:
    """Accetta una lista JSON oppure una stringa tipo "BCN, LIS ATH"."""
    if not raw:
        return None
    codes = re.split(r"[\s,;]+", raw) if isinstance(raw, str) else [str(x) for x in raw]
    validi, ignorati = [], []
    for raw_code in codes:
        code = raw_code.strip().upper()
        if not code:
            continue
        (validi if airports.exists(code) else ignorati).append(code)
    if ignorati and not validi:
        raise BadRequest(f"Nessun codice valido fra: {', '.join(ignorati)}")
    return validi or None


def _build_query(data: dict, origin: str, destination: str) -> SearchQuery:
    departure_date = _date(data.get("departure_date"), default=date.today() + timedelta(days=14))
    return_date = _date(data.get("return_date"), default=None)

    if departure_date < date.today():
        raise BadRequest("La data di andata e' nel passato")
    if return_date and return_date < departure_date:
        raise BadRequest("Il ritorno e' precedente all'andata")

    return SearchQuery(
        origin=origin,
        destination=destination,
        departure_date=departure_date.isoformat(),
        return_date=return_date.isoformat() if return_date else None,
        adults=_int(data.get("adults"), default=1, low=1, high=9),
        children=_int(data.get("children"), default=0, low=0, high=8),
        infants=_int(data.get("infants"), default=0, low=0, high=8),
        seat=data.get("seat") if data.get("seat") in
        ("economy", "premium-economy", "business", "first") else "economy",
        max_stops=_max_stops(data),
        max_price=_int(data.get("max_price"), default=None, low=1, high=100000),
        currency=CURRENCY,
        earliest_departure=_hour(data.get("departure_time_min")),
        latest_departure=_hour(data.get("departure_time_max")),
        earliest_arrival=_hour(data.get("arrival_time_min")),
        latest_arrival=_hour(data.get("arrival_time_max")),
        return_earliest_departure=_hour(data.get("return_time_min")),
        return_latest_departure=_hour(data.get("return_time_max")),
        airlines=[a.upper() for a in (data.get("airlines") or []) if a],
        exclude_basic_economy=bool(data.get("exclude_basic_economy")),
        sort_by=data.get("sort_by") if data.get("sort_by") in
        ("cheapest", "best", "duration", "departure", "emissions") else "cheapest",
    )


def _max_stops(data: dict) -> int | None:
    if data.get("direct_only"):
        return 0
    return _int(data.get("max_stops"), default=None, low=0, high=3)


def _int(value, default, low=None, high=None):
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise BadRequest(f"Valore numerico non valido: {value!r}") from None
    if low is not None and number < low:
        raise BadRequest(f"Valore troppo basso: {number} (minimo {low})")
    if high is not None and number > high:
        raise BadRequest(f"Valore troppo alto: {number} (massimo {high})")
    return number


def _hour(value) -> int | None:
    """Accetta "18:00" oppure 18: Google filtra per ora intera, non per minuto."""
    if value in (None, ""):
        return None
    if isinstance(value, int):
        hour = value
    else:
        text = str(value).strip()
        hour = int(text.split(":")[0]) if ":" in text else int(text)
    if not 0 <= hour <= 23:
        raise BadRequest(f"Ora fuori intervallo: {value!r}")
    return hour


def _date(value, default):
    if value in (None, ""):
        return default
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        raise BadRequest(f"Data non valida: {value!r} (formato atteso AAAA-MM-GG)") from None


if __name__ == "__main__":
    print("=" * 64)
    print("Flight Finder - dati da Google Flights, nessuna chiave API richiesta")
    print(f"Provider attivi : {', '.join(engine.provider_names)}")
    print(f"Paese / valuta  : {COUNTRY} / {CURRENCY}")
    print("Apri            : http://localhost:5001")
    print("=" * 64)
    app.run(debug=os.getenv("FLASK_DEBUG", "1") == "1", port=int(os.getenv("PORT", "5001")))
