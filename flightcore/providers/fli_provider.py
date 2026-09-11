"""
Provider primario: punitarani/fli.

Parla direttamente con l'endpoint interno di Google Flights invece di
raschiare l'HTML, quindi restituisce l'intero elenco dei risultati (nei nostri
test 138 offerte contro le 8 della pagina renderizzata) ed espone i filtri
nativi di Google: fascia oraria, tetto di prezzo, numero di scali, compagnie.
"""

from __future__ import annotations

import logging

from ..models import DatePrice, Leg, Offer, SearchQuery, Segment
from .base import Provider, ProviderError

logger = logging.getLogger(__name__)

_SEAT_TYPES = {
    "economy": "ECONOMY",
    "premium-economy": "PREMIUM_ECONOMY",
    "business": "BUSINESS",
    "first": "FIRST",
}

_SORTS = {
    "best": "BEST",
    "cheapest": "CHEAPEST",
    "duration": "DURATION",
    "departure": "DEPARTURE_TIME",
    "arrival": "ARRIVAL_TIME",
    "emissions": "EMISSIONS",
}

# fli enumera gli scali come "al massimo N", non come conteggio esatto.
_MAX_STOPS = {0: "NON_STOP", 1: "ONE_STOP_OR_FEWER", 2: "TWO_OR_FEWER_STOPS"}


class FliProvider(Provider):
    name = "fli"
    supports_dates = True

    def __init__(self, country: str = "IT", language: str = "it"):
        self.country = country
        self.language = language

    def available(self) -> bool:
        try:
            import fli.models  # noqa: F401
        except ImportError:
            return False
        return True

    # ------------------------------------------------------------------ #
    # Traduzione SearchQuery -> filtri fli
    # ------------------------------------------------------------------ #

    def _airport(self, code: str):
        from fli.models import Airport

        try:
            return getattr(Airport, code.upper())
        except AttributeError:
            raise ProviderError(f"Codice aeroporto sconosciuto: {code}") from None

    def _airlines(self, codes: list[str] | None):
        if not codes:
            return None
        from fli.models import Airline

        resolved = [getattr(Airline, c.upper(), None) for c in codes]
        return [a for a in resolved if a is not None] or None

    def _segments(self, query: SearchQuery):
        from fli.models import FlightSegment, TimeRestrictions

        outbound_times = _time_restrictions(
            TimeRestrictions,
            e_dep=query.earliest_departure,
            l_dep=query.latest_departure,
            e_arr=query.earliest_arrival,
            l_arr=query.latest_arrival,
        )
        segments = [
            FlightSegment(
                departure_airport=[[self._airport(query.origin), 0]],
                arrival_airport=[[self._airport(query.destination), 0]],
                travel_date=query.departure_date,
                time_restrictions=outbound_times,
            )
        ]
        if query.return_date:
            segments.append(
                FlightSegment(
                    departure_airport=[[self._airport(query.destination), 0]],
                    arrival_airport=[[self._airport(query.origin), 0]],
                    travel_date=query.return_date,
                    time_restrictions=_time_restrictions(
                        TimeRestrictions,
                        e_dep=query.return_earliest_departure,
                        l_dep=query.return_latest_departure,
                    ),
                )
            )
        return segments

    def _filters(self, query: SearchQuery):
        from fli.models import (
            Currency,
            FlightSearchFilters,
            MaxStops,
            PassengerInfo,
            PriceLimit,
            SeatType,
            SortBy,
            TripType,
        )

        price_limit = None
        if query.max_price:
            price_limit = PriceLimit(
                max_price=int(query.max_price),
                currency=getattr(Currency, query.currency.upper(), Currency.EUR),
            )

        return FlightSearchFilters(
            trip_type=TripType.ROUND_TRIP if query.round_trip else TripType.ONE_WAY,
            passenger_info=PassengerInfo(
                adults=query.adults,
                children=query.children,
                infants_in_seat=query.infants,
            ),
            flight_segments=self._segments(query),
            stops=getattr(MaxStops, _MAX_STOPS.get(query.max_stops, ""), MaxStops.ANY),
            seat_type=getattr(SeatType, _SEAT_TYPES.get(query.seat, "ECONOMY")),
            price_limit=price_limit,
            airlines=self._airlines(query.airlines),
            sort_by=getattr(SortBy, _SORTS.get(query.sort_by, "CHEAPEST")),
            exclude_basic_economy=query.exclude_basic_economy,
        )

    # ------------------------------------------------------------------ #
    # Ricerca
    # ------------------------------------------------------------------ #

    def search(self, query: SearchQuery) -> list[Offer]:
        from fli.search import SearchFlights

        try:
            results = SearchFlights().search(
                self._filters(query),
                top_n=6 if query.round_trip else 5,
                currency=query.currency,
                language=self.language,
                country=self.country,
            )
        except Exception as exc:
            raise ProviderError(f"fli: ricerca fallita ({exc})") from exc

        # fli restituisce None, non una lista vuota, quando non trova nulla.
        if not results:
            return []

        offers = []
        for item in results:
            offer = self._to_offer(item, query)
            if offer is not None:
                offers.append(offer)
        return offers

    def _to_offer(self, item, query: SearchQuery) -> Offer | None:
        # One-way: un FlightResult. Round-trip: una tupla (andata, ritorno).
        parts = list(item) if isinstance(item, tuple) else [item]
        if not parts:
            return None

        legs = [Leg(segments=[_segment(leg) for leg in part.legs]) for part in parts if part.legs]
        if not legs:
            return None

        # Sul round-trip il prezzo della tupla e' cumulativo: l'ultimo elemento
        # porta il totale del viaggio, non il costo del solo ritorno.
        last = parts[-1]
        price = last.price if last.price is not None else parts[0].price
        if price is None:
            return None

        co2 = last.co2_emissions_g or parts[0].co2_emissions_g
        tags = []
        if last.emissions_tag:
            tags.append(last.emissions_tag)
        if last.mixed_cabin:
            tags.append("cabina mista")

        return Offer(
            price=float(price),
            currency=last.currency or query.currency,
            legs=legs,
            origin=query.origin,
            destination=query.destination,
            provider=self.name,
            co2_kg=round(co2 / 1000, 1) if co2 else None,
            co2_delta_pct=last.co2_emissions_delta_pct,
            self_transfer=bool(last.self_transfer),
            round_trip=query.round_trip,
            booking_url=google_flights_url(query),
            tags=tags,
        )

    # ------------------------------------------------------------------ #
    # Calendario prezzi
    # ------------------------------------------------------------------ #

    def cheapest_dates(
        self,
        origin: str,
        destination: str,
        from_date: str,
        to_date: str,
        *,
        trip_days: int | None = None,
        adults: int = 1,
        max_stops: int | None = None,
        currency: str = "EUR",
        **_,
    ) -> list[DatePrice]:
        from fli.models import (
            DateSearchFilters,
            FlightSegment,
            MaxStops,
            PassengerInfo,
            SeatType,
            TripType,
        )
        from fli.search import SearchDates

        segments = [
            FlightSegment(
                departure_airport=[[self._airport(origin), 0]],
                arrival_airport=[[self._airport(destination), 0]],
                travel_date=from_date,
            )
        ]
        if trip_days:
            segments.append(
                FlightSegment(
                    departure_airport=[[self._airport(destination), 0]],
                    arrival_airport=[[self._airport(origin), 0]],
                    travel_date=to_date,
                )
            )

        filters = DateSearchFilters(
            trip_type=TripType.ROUND_TRIP if trip_days else TripType.ONE_WAY,
            passenger_info=PassengerInfo(adults=adults),
            flight_segments=segments,
            from_date=from_date,
            to_date=to_date,
            duration=trip_days,
            stops=getattr(MaxStops, _MAX_STOPS.get(max_stops, ""), MaxStops.ANY),
            seat_type=SeatType.ECONOMY,
        )

        try:
            results = SearchDates().search(
                filters, currency=currency, language=self.language, country=self.country
            )
        except Exception as exc:
            raise ProviderError(f"fli: calendario prezzi fallito ({exc})") from exc

        return [
            DatePrice(
                # fli restituisce la data come stringa sull'andata singola e come
                # lista [andata, ritorno] quando si cercano soggiorni di N giorni.
                date=str(r.date[0] if isinstance(r.date, list) else r.date),
                price=float(r.price),
                currency=currency,
            )
            for r in (results or [])
            if r.price is not None
        ]


def _time_restrictions(cls, *, e_dep=None, l_dep=None, e_arr=None, l_arr=None):
    """None quando non c'e' nessun vincolo: fli tratta l'oggetto vuoto come un filtro."""
    if all(v is None for v in (e_dep, l_dep, e_arr, l_arr)):
        return None
    return cls(
        earliest_departure=e_dep,
        latest_departure=l_dep,
        earliest_arrival=e_arr,
        latest_arrival=l_arr,
    )


def _segment(leg) -> Segment:
    airline = leg.airline
    return Segment(
        from_code=leg.departure_airport.name,
        to_code=leg.arrival_airport.name,
        departure=leg.departure_datetime,
        arrival=leg.arrival_datetime,
        duration_min=leg.duration,
        airline_code=getattr(airline, "name", None),
        airline_name=getattr(airline, "value", None),
        flight_number=leg.flight_number,
        from_name=leg.departure_airport_name or leg.departure_airport.value,
        to_name=leg.arrival_airport_name or leg.arrival_airport.value,
        aircraft=leg.aircraft,
        legroom=leg.legroom_short or leg.legroom,
        overnight=bool(leg.overnight),
    )


def google_flights_url(query: SearchQuery) -> str:
    """Link alla stessa ricerca su Google Flights, per prenotare."""
    parts = [f"Flights from {query.origin} to {query.destination}", f"on {query.departure_date}"]
    if query.return_date:
        parts.append(f"through {query.return_date}")
    from urllib.parse import quote_plus

    query_text = quote_plus(" ".join(parts))
    return f"https://www.google.com/travel/flights?q={query_text}&curr={query.currency}"
