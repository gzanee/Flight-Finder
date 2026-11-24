"""One flight search engine for fixed/flexible dates, one-way and return trips."""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import date, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

from flightcore import airports, flexible_api  # noqa: E402
from flightcore.flexible import FlexibleRequest, SearchValidationError, build_plan  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
app = Flask(__name__)
app.register_blueprint(flexible_api.blueprint)


@app.errorhandler(SearchValidationError)
def invalid(exc):
    return jsonify(success=False, error=str(exc)), 400


def legacy_request(data, *, calendar=False):
    """Translate old client fields; every route starts the same persistent worker."""
    if not isinstance(data, dict):
        raise SearchValidationError("Invia un oggetto JSON.")
    if "start_date" in data:
        return FlexibleRequest.parse(data)
    today = date.today()
    start = data.get("departure_date") or (today + timedelta(days=14)).isoformat()
    back = data.get("return_date")
    end = back or start
    nights = data.get("trip_days")
    if calendar:
        if not data.get("destination"):
            raise SearchValidationError("Serve una destinazione per il calendario prezzi.")
        start = data.get("from_date") or (today + timedelta(days=7)).isoformat()
        end = data.get("to_date") or (today + timedelta(days=60)).isoformat()
    destinations = data.get("destinations") or data.get("destination") or []
    if isinstance(destinations, str) and destinations.lower() == "anywhere":
        destinations = []
    if not destinations and data.get("destination_set") not in (None, "real"):
        destinations = airports.destination_set(data["destination_set"])
    spec = {
        "origins": data.get("origins") or data.get("origin") or ["VCE"],
        "destinations": destinations,
        "trip_type": "round_trip" if (nights if calendar else back) else "one_way",
        "date_mode": "flexible" if calendar else "fixed",
        "start_date": start,
        "end_date": end,
        "departure_days": list(range(7)),
        "return_days": list(range(7)),
        "min_nights": nights or 2,
        "max_nights": nights or 2,
        "budget": data.get("max_price") or 100000,
        "adults": data.get("adults", 1),
        "children": data.get("children", 0),
        "infants": data.get("infants", 0),
        "seat": data.get("seat", "economy"),
        "max_stops": 0 if data.get("direct_only") else data.get("max_stops", 3),
        "airlines": data.get("airlines") or [],
        "exclude_basic_economy": data.get("exclude_basic_economy", False),
    }
    for old, new in [
        ("departure_time", "departure"),
        ("return_time", "return"),
        ("arrival_time", "departure_arrival"),
    ]:
        for bound, default in (("min", "00:00"), ("max", "23:59")):
            value = data.get(old + "_" + bound)
            if isinstance(value, int):
                value = f"{value:02d}:00"
            spec[new + "_" + bound] = value if value not in (None, "") else default
    return FlexibleRequest.parse(spec)


@app.route("/api/search", methods=["POST"])
@app.route("/api/explore", methods=["POST"])
@app.route("/api/cheapest-dates", methods=["POST"])
def legacy_search():
    spec = legacy_request(
        request.get_json(silent=True), calendar=request.path.endswith("cheapest-dates")
    )
    plan = build_plan(spec, flexible_api.candidate_routes(spec))
    try:
        job_id = flexible_api.manager.create(spec, plan)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    return jsonify(success=True, id=job_id, status_url=f"/api/search/jobs/{job_id}"), 202


@app.route("/api/airports")
def api_airports():
    keyword = request.args.get("q") or request.args.get("keyword") or ""
    return jsonify(success=True, airports=airports.search(keyword, limit=10))


@app.route("/api/countries")
def api_countries():
    from flightcore.flexible import MAX_ORIGINS, airport_codes

    origins = airport_codes(request.args.get("origins", "VCE,TSF"), "Partenza", MAX_ORIGINS)
    return jsonify(success=True, countries=airports.countries(list(origins)))


@app.route("/api/health")
def api_health():
    import time

    live = flexible_api.manager.live
    return jsonify(
        success=True,
        engine="unified",
        # Nessun provider alternativo: live_quotes scarica la pagina di Google
        # Flights e usa le due librerie solo per costruire e decodificare.
        libraries={"fast-flights": "query", "fli": "decoder"},
        retry_in_s=max(0, round(live.blocked_until - time.monotonic())),
        country="IT",
        currency="EUR",
        request_interval=live.request_interval,
    )


def relaunch():
    """Replace this process with a fresh one: new code, templates and port."""
    # The server keeps its listening socket inheritable: close it to free the port.
    os.closerange(3, 4096)
    os.execv(sys.executable, [sys.executable, *sys.argv])


@app.route("/api/restart", methods=["POST"])
def api_restart():
    # After the reply is sent; a running search is saved and can be resumed.
    threading.Timer(0.5, relaunch).start()
    return jsonify(success=True, message="Riavvio in corso.")


@app.route("/api/cache", methods=["DELETE"])
def api_clear_cache():
    live = flexible_api.manager.live
    with live._lock:
        live._cache.clear()
    return jsonify(success=True, message="Cache prezzi svuotata.")


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "0") == "1", port=int(os.getenv("PORT", "5001")))
