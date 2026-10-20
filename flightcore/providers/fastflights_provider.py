"""
Provider di riserva: AWeirdDev/fast-flights.

Legge la pagina HTML di Google Flights invece dell'endpoint interno. Restituisce
meno offerte (solo quelle che Google mostra in prima battuta) e senza numeri di
volo, ma continua a rispondere quando l'endpoint RPC usato da fli rifiuta le
richieste: e' esattamente il caso in cui serve una rete di sicurezza.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..models import Leg, Offer, SearchQuery, Segment
from ..ratelimit import limiter
from .base import Provider, ProviderError

logger = logging.getLogger(__name__)

_TRIPS = {True: "round-trip", False: "one-way"}


class FastFlightsProvider(Provider):
    name = "fast-flights"
    supports_dates = False

    def __init__(self, language: str = "it"):
        self.language = language

    def available(self) -> bool:
        try:
            import fast_flights  # noqa: F401
        except ImportError:
            return False
        return True

    def _query(self, query: SearchQuery):
        from fast_flights import FlightQuery, Passengers, create_query

        legs = [
            FlightQuery(
                date=query.departure_date,
                from_airport=query.origin,
                to_airport=query.destination,
                max_stops=query.max_stops,
                airlines=query.airlines or None,
                earliest_departure_hour=query.earliest_departure,
                latest_departure_hour=query.latest_departure,
                earliest_arrival_hour=query.earliest_arrival,
                latest_arrival_hour=query.latest_arrival,
            )
        ]
        if query.return_date:
            legs.append(
                FlightQuery(
                    date=query.return_date,
                    from_airport=query.destination,
                    to_airport=query.origin,
                    max_stops=query.max_stops,
                    earliest_departure_hour=query.return_earliest_departure,
                    latest_departure_hour=query.return_latest_departure,
                )
            )

        return create_query(
            flights=legs,
            seat=query.seat,
            trip=_TRIPS[query.round_trip],
            passengers=Passengers(
                adults=query.adults, children=query.children, infants_in_seat=query.infants
            ),
            language=self.language,
            currency=query.currency,
            max_stops=query.max_stops,
            max_price=query.max_price,
            exclude_basic_economy=query.exclude_basic_economy,
        )

    def search(self, query: SearchQuery) -> list[Offer]:
        from fast_flights.fetcher import fetch_flights_html
        from fast_flights.parser import parse

        from ..consent import install_fast_flights

        gq = self._query(query)
        # Senza SOCS un IP europeo riceve la pagina di consenso al posto dei voli.
        install_fast_flights(currency=query.currency)
        limiter.acquire()
        try:
            html = fetch_flights_html(gq)
        except Exception as exc:
            raise ProviderError(f"fast-flights: richiesta fallita ({exc})") from exc

        # Distinguere "Google ci sta limitando" da "questa rotta non ha voli" e'
        # essenziale: senza questo controllo un utente strozzato vedrebbe
        # "nessun volo" e concluderebbe che la rotta non esiste.
        blocco = _rileva_blocco(html)
        if blocco:
            raise ProviderError(blocco)

        try:
            results = parse(html)
        except Exception as exc:
            # Il parser solleva IndexError/AttributeError quando la pagina non
            # contiene la tabella dei voli. Nella pratica significa quasi sempre
            # "su questa rotta non vola nessuno" (tipico da TSF, che ha poche
            # destinazioni), non "il provider e' rotto": segnalarlo come guasto
            # farebbe mettere in pausa l'unico provider funzionante.
            logger.debug("fast-flights: nessun volo per %s->%s (%s)",
                         query.origin, query.destination, exc)
            return []

        # Query.url e' un metodo, non una property: senza chiamarlo finirebbe
        # nel JSON come oggetto bound e la risposta non sarebbe serializzabile.
        booking_url = gq.url() if hasattr(gq, "url") else None
        offers = []
        for item in results or []:
            offer = self._to_offer(item, query, booking_url)
            if offer is not None:
                offers.append(offer)
        return offers

    def _to_offer(self, item, query: SearchQuery, booking_url: str | None) -> Offer | None:
        if not item.flights or item.price is None:
            return None

        segments = [_segment(f, item.airlines) for f in item.flights]
        legs = [Leg(segments=seg_group) for seg_group in _split_legs(segments, query.destination)]

        carbon = getattr(item, "carbon", None)
        co2_kg = round(carbon.emission / 1000, 1) if carbon and carbon.emission else None
        delta = None
        if carbon and carbon.emission and carbon.typical_on_route:
            scarto = carbon.emission - carbon.typical_on_route
            delta = round(scarto / carbon.typical_on_route * 100)

        return Offer(
            price=float(item.price),
            currency=query.currency,
            legs=legs,
            origin=query.origin,
            destination=query.destination,
            provider=self.name,
            co2_kg=co2_kg,
            co2_delta_pct=delta,
            round_trip=query.round_trip,
            booking_url=booking_url,
        )


def _split_legs(segments: list[Segment], destination: str) -> list[list[Segment]]:
    """
    Divide le tratte in andata e ritorno.

    Google restituisce un elenco piatto di tratte: l'andata finisce quando si
    tocca la destinazione per la prima volta, il resto (se c'e') e' il ritorno.
    """
    for i, seg in enumerate(segments):
        if seg.to_code.upper() == destination.upper() and i < len(segments) - 1:
            return [segments[: i + 1], segments[i + 1 :]]
    return [segments]


def _segment(flight, airlines: list[str]) -> Segment:
    return Segment(
        from_code=flight.from_airport.code,
        to_code=flight.to_airport.code,
        from_name=flight.from_airport.name,
        to_name=flight.to_airport.name,
        departure=_datetime(flight.departure),
        arrival=_datetime(flight.arrival),
        duration_min=flight.duration,
        # L'HTML elenca le compagnie a livello di offerta, non di tratta: con una
        # sola compagnia l'attribuzione e' certa, altrimenti meglio non indovinare.
        airline_name=airlines[0] if len(airlines) == 1 else None,
        aircraft=flight.plane_type,
    )


def _datetime(value) -> datetime:
    (year, month, day), (hour, minute) = value.date, value.time
    return datetime(year, month, day, hour, minute)


def _rileva_blocco(html: str) -> str | None:
    """Riconosce le pagine che Google serve al posto dei risultati."""
    testa = html[:4000].lower()
    if "consent.google.com" in testa:
        return (
            "Google chiede il consenso cookie: il cookie SOCS non e' stato "
            "applicato. Controlla /api/health."
        )
    if "/sorry/" in testa or "unusual traffic" in testa or "captcha" in testa:
        return (
            "Google ha temporaneamente limitato le richieste da questo indirizzo "
            "(troppe ricerche ravvicinate). Riprova fra qualche minuto."
        )
    return None
