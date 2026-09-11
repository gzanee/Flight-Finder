"""
Modello dati normalizzato.

Ogni provider traduce la propria risposta in queste strutture, cosi' il resto
dell'app (e il frontend) non sa e non deve sapere da dove arrivino i dati.
Tutti i prezzi sono il totale per l'intera prenotazione, non per passeggero.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class Segment:
    """Una singola tratta operata, senza scali interni."""

    from_code: str
    to_code: str
    departure: datetime
    arrival: datetime
    duration_min: int
    airline_code: str | None = None
    airline_name: str | None = None
    flight_number: str | None = None
    from_name: str | None = None
    to_name: str | None = None
    aircraft: str | None = None
    legroom: str | None = None
    overnight: bool = False


@dataclass
class Leg:
    """Un senso di marcia (andata oppure ritorno), con i suoi scali."""

    segments: list[Segment]

    @property
    def departure(self) -> datetime:
        return self.segments[0].departure

    @property
    def arrival(self) -> datetime:
        return self.segments[-1].arrival

    @property
    def stops(self) -> int:
        return len(self.segments) - 1

    @property
    def duration_min(self) -> int:
        # Somma delle tratte piu' il tempo a terra: la differenza fra gli
        # estremi include gia' gli scali ed e' cio' che interessa all'utente.
        return int((self.arrival - self.departure).total_seconds() // 60)

    @property
    def airlines(self) -> list[str]:
        seen: list[str] = []
        for seg in self.segments:
            name = seg.airline_name or seg.airline_code
            if name and name not in seen:
                seen.append(name)
        return seen

    @property
    def layover_codes(self) -> list[str]:
        return [seg.to_code for seg in self.segments[:-1]]


@dataclass
class Offer:
    """Un'offerta completa: andata, eventuale ritorno e prezzo totale."""

    price: float
    currency: str
    legs: list[Leg]
    origin: str
    destination: str
    provider: str
    co2_kg: float | None = None
    co2_delta_pct: int | None = None
    self_transfer: bool = False
    booking_url: str | None = None
    tags: list[str] = field(default_factory=list)
    #: True se la ricerca era andata e ritorno. Con un solo leg presente
    #: significa che il prezzo e' gia' quello A/R ma il ritorno va ancora scelto,
    #: esattamente come nella pagina di Google.
    round_trip: bool = False

    @property
    def stops(self) -> int:
        """Numero di scali del tratto peggiore: e' cio' che determina il disagio."""
        return max((leg.stops for leg in self.legs), default=0)

    @property
    def duration_min(self) -> int:
        return sum(leg.duration_min for leg in self.legs)

    @property
    def airlines(self) -> list[str]:
        seen: list[str] = []
        for leg in self.legs:
            for name in leg.airlines:
                if name not in seen:
                    seen.append(name)
        return seen

    def to_dict(self) -> dict:
        data = asdict(self)
        data.update(
            inbound_pending=self.round_trip and len(self.legs) < 2,
            stops=self.stops,
            duration_min=self.duration_min,
            airlines=self.airlines,
            outbound=_leg_dict(self.legs[0]) if self.legs else None,
            inbound=_leg_dict(self.legs[1]) if len(self.legs) > 1 else None,
        )
        data.pop("legs", None)
        return data


def _leg_dict(leg: Leg) -> dict:
    return {
        "departure": leg.departure.isoformat(),
        "arrival": leg.arrival.isoformat(),
        "duration_min": leg.duration_min,
        "stops": leg.stops,
        "airlines": leg.airlines,
        "layovers": leg.layover_codes,
        "from_code": leg.segments[0].from_code,
        "to_code": leg.segments[-1].to_code,
        "segments": [
            {
                **asdict(seg),
                "departure": seg.departure.isoformat(),
                "arrival": seg.arrival.isoformat(),
            }
            for seg in leg.segments
        ],
    }


@dataclass
class DatePrice:
    """Prezzo minimo per una data, usato dal calendario prezzi."""

    date: str
    price: float
    currency: str = "EUR"
    return_date: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchQuery:
    """Parametri di ricerca normalizzati, indipendenti dal provider."""

    origin: str
    destination: str
    departure_date: str
    return_date: str | None = None
    adults: int = 1
    children: int = 0
    infants: int = 0
    seat: str = "economy"
    max_stops: int | None = None
    max_price: int | None = None
    currency: str = "EUR"
    # Ore intere 0-23: il filtro viene applicato da Google, non a posteriori.
    earliest_departure: int | None = None
    latest_departure: int | None = None
    earliest_arrival: int | None = None
    latest_arrival: int | None = None
    return_earliest_departure: int | None = None
    return_latest_departure: int | None = None
    airlines: list[str] | None = None
    exclude_basic_economy: bool = False
    sort_by: str = "cheapest"

    @property
    def round_trip(self) -> bool:
        return bool(self.return_date)

    @property
    def passengers(self) -> int:
        return self.adults + self.children + self.infants
