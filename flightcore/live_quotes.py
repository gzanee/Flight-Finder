"""Strict Google shopping quotes: fast-flights query + fli row decoder.

No library files are modified. A schema mismatch is an error, never an empty route.
Google shopping availability is not an airline ticketing guarantee.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone

from .flexible import fingerprint, local_instant

logger = logging.getLogger(__name__)

QUOTE_TTL = 300
CACHE_LIMIT = 2000
HTTP_OK = 200
HOURS_PER_DAY = 24
NETWORK_RETRIES = 2
# Google sometimes answers 200 with an ErrorResponse (status 13, INTERNAL) for
# a query that succeeds when repeated. Measured 2026-10-30 on one route: 6/15 at
# 0.3 s, 3/15 at 2 s, also with a fresh client. Errors come in bursts, so back
# off exponentially (2, 4, 8, 16, 30 s) rather than retrying within the burst.
GOOGLE_ERROR_RETRIES = 5
MAX_GOOGLE_ERROR_WAIT = 30
# Measured 2026-10-20 from a residential IT IP: 300 consecutive searches at
# 0.3 s showed no 429 and the same empty routes as at 1.5 s. Below that floor
# nothing was tested, so it is not allowed.
DEFAULT_REQUEST_INTERVAL = 0.3
MIN_REQUEST_INTERVAL = 0.3
MAX_REQUEST_INTERVAL = 10.0
# Response time plus parsing not hidden by the interval, per search (measured).
REQUEST_OVERHEAD = 0.2


def request_interval():
    """Return the serial Google request interval, with a conservative floor."""
    try:
        configured = float(os.getenv("FLIGHT_FINDER_GOOGLE_INTERVAL", DEFAULT_REQUEST_INTERVAL))
    except (TypeError, ValueError):
        configured = DEFAULT_REQUEST_INTERVAL
    if not math.isfinite(configured):
        configured = DEFAULT_REQUEST_INTERVAL
    return min(MAX_REQUEST_INTERVAL, max(MIN_REQUEST_INTERVAL, configured))


class LiveUnavailable(RuntimeError):
    pass


class LiveBlocked(LiveUnavailable):
    pass


class LiveTransient(LiveUnavailable):
    pass


class LiveQuotes:
    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()
        self._network_lock = threading.Lock()
        self._next_request = 0.0
        self._client = None
        self.request_interval = request_interval()
        self.blocked_until = 0.0

    def search(self, origin, destination, day, request, inbound=False, fresh=False):
        prefix = "return" if inbound else "departure"
        lo, hi = getattr(request, prefix + "_min"), getattr(request, prefix + "_max")
        key = (
            origin,
            destination,
            day,
            lo,
            hi,
            request.adults,
            request.children,
            request.max_stops,
            request.allow_self_transfer,
            request.baggage,
            request.currency,
            request.infants,
            request.seat,
            tuple(request.airlines),
            request.exclude_basic_economy,
        )
        with self._lock:
            hit = self._cache.get(key)
            if not fresh and hit and time.monotonic() - hit[0] < QUOTE_TTL:
                return hit[1]
        from fast_flights import FlightQuery, Passengers, create_query

        # Google accepts whole hours. Widen the upper bound before applying our
        # exact minute filter so 19:59 does not become a cutoff at 19:00.
        upper_hour = int(hi[:2]) + int(hi[3:] != "00")
        query = create_query(
            flights=[
                FlightQuery(
                    date=day,
                    from_airport=origin,
                    to_airport=destination,
                    earliest_departure_hour=int(lo[:2]),
                    latest_departure_hour=upper_hour if upper_hour < HOURS_PER_DAY else None,
                    airlines=list(request.airlines) or None,
                )
            ],
            passengers=Passengers(
                adults=request.adults, children=request.children, infants_on_lap=request.infants
            ),
            seat=request.seat,
            trip="one-way",
            currency=request.currency,
            language="it",
            max_stops=request.max_stops,
            carry_on_bags=(request.adults + request.children) if request.baggage == "cabin" else 0,
            checked_bags=(request.adults + request.children) if request.baggage == "checked" else 0,
            hide_separate_and_self_transfer=not request.allow_self_transfer,
            exclude_basic_economy=request.exclude_basic_economy,
        )
        for attempt in range(GOOGLE_ERROR_RETRIES + 1):
            html = self._fetch(query)
            try:
                quotes, rejected = decode(
                    html, request, query.url(), expected_route=(origin, destination)
                )
                break
            except LiveTransient:
                if attempt == GOOGLE_ERROR_RETRIES:
                    raise
                logger.info(
                    "Google error response for %s-%s %s; retry %d/%d",
                    origin,
                    destination,
                    day,
                    attempt + 1,
                    GOOGLE_ERROR_RETRIES,
                )
                time.sleep(min(MAX_GOOGLE_ERROR_WAIT, 2 ** (attempt + 1)))
        result = {"quotes": quotes, "unverified": rejected, "empty": not quotes and not rejected}
        with self._lock:
            if len(self._cache) >= CACHE_LIMIT:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (time.monotonic(), result)
        return result

    def _fetch(self, query):
        import primp

        from .consent import get_cookies

        # One request every configured interval, no parallel burst. Never retry a 429 automatically.
        with self._network_lock:
            if time.monotonic() < self.blocked_until:
                raise LiveBlocked("Google limita le richieste. Ricerca fermata; riprova più tardi.")
            wait = self._next_request - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            for attempt in range(NETWORK_RETRIES + 1):
                try:
                    # Reuse one connection: a new TLS handshake per search costs
                    # 0.1-0.2 s. Only this thread uses it, under the network lock.
                    if self._client is None:
                        self._client = primp.Client(
                            impersonate="chrome",
                            timeout=25,
                            cookie_store=True,
                            cookies=get_cookies(),
                        )
                    response = self._client.get(
                        "https://www.google.com/travel/flights", params=query.params()
                    )
                    break
                except (primp.RequestError, OSError) as exc:
                    self._client = None
                    if attempt == NETWORK_RETRIES:
                        logger.warning(
                            "Google network request failed after %d attempts (%s: %s)",
                            attempt + 1,
                            type(exc).__name__,
                            exc,
                        )
                        raise LiveUnavailable(
                            "Connessione a Google non riuscita dopo vari tentativi. "
                            "Puoi premere Riprendi."
                        ) from exc
                    # A reset is often a stale HTTP/2 connection. The client was
                    # dropped above; wait briefly. This does not retry a 403/429.
                    retry_wait = 1.5 * (attempt + 1)
                    logger.info(
                        "Google network request failed (%s); retry %d/%d in %.1fs",
                        type(exc).__name__,
                        attempt + 1,
                        NETWORK_RETRIES,
                        retry_wait,
                    )
                    time.sleep(retry_wait)
            text = response.text
            # Schedule the next request after the complete exchange, including
            # retries, so a slow/retried call cannot create a burst afterwards.
            self._next_request = time.monotonic() + self.request_interval
            if response.status_code in (403, 429) or "unusual traffic" in text[:6000].lower():
                self._client = None
                self.blocked_until = time.monotonic() + 900
                raise LiveBlocked("Google ha limitato le richieste. Nessun tentativo automatico.")
            if response.status_code != HTTP_OK:
                raise LiveUnavailable(f"Google ha risposto HTTP {response.status_code}.")
            return text


def _is_empty_shopping_response(payload, expected_route):
    """Recognize null result groups only inside the observed shopping envelope.

    Google emits both [null] groups and entirely null groups for no matches.
    In the latter case require the success header, resolved airport pair,
    filter metadata and search context. A bare/truncated list is not evidence
    of an empty search. Slot 6 can contain suggestions for OTHER travel dates;
    those must never be treated as offers for the requested date.
    """
    try:
        header, locations, filters, context = (payload[0], payload[1], payload[7], payload[12])
        # Each request in this adapter has exactly one leg and one airport at
        # either end. Check the resolved locations against that actual request.
        departure, arrival = locations[0]
        origin, destination = departure[0][0][0], arrival[0][0][0]
        return (
            len(locations) == 1
            and len(departure) == len(arrival) == 1
            and isinstance(origin, str)
            and isinstance(destination, str)
            and re.fullmatch(r"[A-Z0-9]{3}", origin)
            and re.fullmatch(r"[A-Z0-9]{3}", destination)
            and (expected_route is None or (origin, destination) == expected_route)
            and isinstance(header, list)
            and type(header[2]) is int
            and header[2] == 0
            and isinstance(header[1], list)
            and bool(header[1])
            and isinstance(filters, list)
            and bool(filters)
            and isinstance(context, list)
            and len(context) == 2  # noqa: PLR2004 - token/status pair
            and isinstance(context[0], str)
            and bool(context[0])
            and type(context[1]) is int
            and context[1] == 0
            and payload[23] == 1
        )
    except (IndexError, KeyError, TypeError, ValueError, AttributeError):
        return False


def decode(html, request, url, *, expected_route=None):  # noqa: PLR0912, PLR0915 - validate the observed wire format
    from fli.search._decoders import parse_flight_row
    from selectolax.lexbor import LexborHTMLParser

    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise LiveUnavailable("Pagina Google senza dati voli: consenso, blocco o formato cambiato.")
    if "travel.frontend.flights.ErrorResponse" in script.text():
        raise LiveTransient(
            "Google ha restituito un errore temporaneo anche dopo vari tentativi. "
            "Puoi premere Riprendi."
        )
    try:
        payload = json.loads(script.text().split("data:", 1)[1].rsplit(",", 1)[0])
        rows = []
        found = False
        for slot in (2, 3):
            block = payload[slot]
            if block is None:
                continue
            if (
                isinstance(block, list)
                and block
                and (block[0] is None or isinstance(block[0], list))
            ):
                found = True
                rows.extend(block[0] or [])
            else:
                raise ValueError("Malformed shopping group")
        if not found and not _is_empty_shopping_response(payload, expected_route):
            raise ValueError("No shopping rows")
    except (ValueError, TypeError, IndexError) as exc:
        raise LiveUnavailable(
            "Risposta Google non riconosciuta: non equivale a zero voli."
        ) from exc
    # IATA codes can be reassigned. Use the names in this live response rather
    # than fli's static enum (which still labels W4 as the former LC Peru).
    try:
        airline_names = {item[0]: item[1] for item in payload[7][1][1]}
    except (TypeError, IndexError):
        airline_names = {}
    quotes, rejected, seen = [], 0, set()
    checked = datetime.now(timezone.utc).isoformat()
    for raw_row in rows:
        try:
            row = deepcopy(raw_row)
            # Google omits zero minute components; normalize before fli's decoder.
            for raw_segment in row[0][2]:
                for slot in (8, 10):
                    raw_time = raw_segment[slot]
                    if not isinstance(raw_time, list):
                        raise ValueError("Missing local time")
                    raw_segment[slot] = [(x or 0) for x in [*raw_time, 0, 0][:2]]
            result = parse_flight_row(row)
            if result.price is None or result.price <= 0 or result.currency != request.currency:
                raise ValueError("Missing live price or currency")
            if not result.booking_token or not result.legs:
                raise ValueError("Missing shopping evidence")
            segments = []
            for leg in result.legs:
                if not leg.flight_number or not leg.airline.name:
                    raise ValueError("Missing flight identity")
                segments.append(
                    {
                        "from_code": leg.departure_airport.name,
                        "to_code": leg.arrival_airport.name,
                        "departure": local_instant(
                            leg.departure_datetime, leg.departure_airport.name
                        ).isoformat(),
                        "arrival": local_instant(
                            leg.arrival_datetime, leg.arrival_airport.name
                        ).isoformat(),
                        "airline_code": leg.airline.name,
                        "airline_name": airline_names.get(leg.airline.name)
                        or (
                            raw_row[0][1][0] if len(raw_row[0][1] or []) == 1 else leg.airline.name
                        ),
                        "flight_number": leg.flight_number,
                        "duration_min": leg.duration,
                    }
                )
            quote = {
                "segments": segments,
                "price": float(result.price),
                "currency": result.currency,
                "duration_min": result.duration,
                "self_transfer": result.self_transfer,
                "checked_at": checked,
                "verification": "google_live",
                "airline_verified": False,
                "baggage": request.baggage,
                "baggage_basis": "google_search_filter",
                "travelers": request.adults + request.children + request.infants,
                "seat": request.seat,
                "booking_url": url,
            }
            key = fingerprint(quote)
            if key not in seen:
                seen.add(key)
                quotes.append(quote)
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            rejected += 1
    return quotes, rejected


def round_trip_url(trip, request):
    """Google Flights page for the same airports and dates as one A/R search.

    Its fares may differ from two one-way tickets; an open-jaw trip has none.
    """
    back = trip.get("inbound")
    if not back:
        return None
    out = trip["outbound"]["segments"]
    origin, destination = out[0]["from_code"], out[-1]["to_code"]
    if (back["segments"][0]["from_code"], back["segments"][-1]["to_code"]) != (destination, origin):
        return None
    from fast_flights import FlightQuery, Passengers, create_query

    query = create_query(
        flights=[
            FlightQuery(date=out[0]["departure"][:10], from_airport=origin, to_airport=destination),
            FlightQuery(
                date=back["segments"][0]["departure"][:10],
                from_airport=destination,
                to_airport=origin,
            ),
        ],
        passengers=Passengers(
            adults=request.adults, children=request.children, infants_on_lap=request.infants
        ),
        seat=request.seat,
        trip="round-trip",
        currency=request.currency,
        language="it",
        max_stops=request.max_stops,
    )
    return query.url()

