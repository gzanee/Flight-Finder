"""Calendar planning and strict matching, independent of network and Flask.

Route lists are discovery seeds, never evidence of dated flight availability.
All windows are airport-local; elapsed times are measured in UTC.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from itertools import pairwise
from zoneinfo import ZoneInfo

MAX_ORIGINS = 10
MAX_STOPS = 3
MAX_DURATION_MINUTES = 4320
# Explicit, conservative selection; editable without claiming a universal classification.
MAX_PERIOD_DAYS = 366
MAX_TRAVELERS = 9
MAX_BUDGET = 100000
MAX_DESTINATIONS = 200
MAX_TASKS = 50000
# Council of the EU, Schengen area: Bulgaria/Romania included from 2025.
# https://www.consilium.europa.eu/en/policies/schengen-area/
SCHENGEN_COUNTRIES = frozenset(
    [
        "AT",
        "BE",
        "BG",
        "HR",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "HU",
        "IS",
        "IT",
        "LV",
        "LI",
        "LT",
        "LU",
        "MT",
        "NL",
        "NO",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
        "CH",
    ]
)

LOW_COST = frozenset(
    {"FR", "RK", "W4", "W6", "W9", "U2", "EC", "DS", "VY", "V7", "HV", "TO", "PC", "EW"}
)


class SearchValidationError(ValueError):
    pass


def integer(value, label, low, high):
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
        raise SearchValidationError(f"{label}: serve un numero intero.")
    n = int(value)
    if not low <= n <= high:
        raise SearchValidationError(f"{label}: scegli un valore fra {low} e {high}.")
    return n


def clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise SearchValidationError("Gli orari devono avere formato HH:MM.")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise SearchValidationError("Orario non valido.") from exc


def weekdays(value, label):
    if not isinstance(value, list) or not value:
        raise SearchValidationError(f"Seleziona almeno un giorno per {label}.")
    return tuple(sorted({integer(v, label, 0, 6) for v in value}))


def airport_codes(raw, label, maximum, *, required=False):
    from .airports import exists

    if isinstance(raw, str):
        raw = re.split(r"[\s,;]+", raw.strip()) if raw.strip() else []
    if not isinstance(raw, (list, tuple)) or len(raw) > maximum or (required and not raw):
        raise SearchValidationError(f"{label}: scegli da {int(required)} a {maximum} aeroporti.")
    if any(not isinstance(c, str) or not exists(c.strip()) for c in raw):
        raise SearchValidationError(f"{label}: uno dei codici aeroporto non esiste.")
    return tuple(dict.fromkeys(c.strip().upper() for c in raw))


@dataclass(frozen=True)
class FlexibleRequest:
    origins: tuple[str, ...]
    start_date: str
    end_date: str
    departure_days: tuple[int, ...] = (4,)
    return_days: tuple[int, ...] = (6,)
    departure_min: str = "18:00"
    departure_max: str = "23:59"
    return_min: str = "18:00"
    return_max: str = "23:59"
    min_nights: int = 2
    max_nights: int = 2
    max_stops: int = 0
    allow_self_transfer: bool = False
    return_airport: str = "same"
    budget: float = 100
    adults: int = 1
    children: int = 0
    airline_mode: str = "all"
    baggage: str = "base"
    final_arrival_max: str | None = None
    min_destination_hours: int = 0
    destinations: tuple[str, ...] = ()
    currency: str = "EUR"
    trip_type: str = "round_trip"
    destination_set: str = "real"
    date_mode: str = "flexible"
    return_departure: str = "same"
    seat: str = "economy"
    infants: int = 0
    airlines: tuple[str, ...] = ()
    exclude_basic_economy: bool = False
    allow_airport_change: bool = False
    schengen_transfers_only: bool = False
    departure_arrival_min: str = "00:00"
    departure_arrival_max: str = "23:59"
    return_arrival_min: str = "00:00"
    return_arrival_max: str = "23:59"
    min_layover_minutes: int = 0
    max_layover_minutes: int = MAX_DURATION_MINUTES
    departure_duration_min: int = 0
    departure_duration_max: int = MAX_DURATION_MINUTES
    return_duration_min: int = 0
    return_duration_max: int = MAX_DURATION_MINUTES

    @classmethod
    def parse(cls, data, today=None):  # noqa: PLR0912, PLR0915 - validate the request at one boundary
        if not isinstance(data, dict):
            raise SearchValidationError("Invia un oggetto JSON.")
        today = today or date.today()
        fixed_one_way = data.get("date_mode") == "fixed" and data.get("trip_type") == "one_way"
        try:
            start, end = (
                date.fromisoformat(data["start_date"]),
                date.fromisoformat(data["start_date"] if fixed_one_way else data["end_date"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise SearchValidationError("Indica inizio e fine del periodo di ricerca.") from exc
        if start < today or end < start or (end - start).days > MAX_PERIOD_DAYS:
            raise SearchValidationError("Periodo non valido: date future, massimo un anno.")
        origins = airport_codes(
            data.get("origins", ["VCE", "TSF"]), "Partenza", MAX_ORIGINS, required=True
        )
        values = {
            "origins": tuple(dict.fromkeys(origins)),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        for key, allowed, default in [
            ("trip_type", ("round_trip", "one_way"), "round_trip"),
            ("date_mode", ("fixed", "flexible"), "flexible"),
        ]:
            values[key] = data.get(key, default)
            if values[key] not in allowed:
                raise SearchValidationError(f"Valore non valido per {key}.")
        if values["trip_type"] == "one_way" and values["date_mode"] == "fixed":
            values["end_date"] = values["start_date"]
        for key, default in [("departure_days", [4]), ("return_days", [6])]:
            ignored = values["date_mode"] == "fixed" or (
                key == "return_days" and values["trip_type"] == "one_way"
            )
            values[key] = tuple(range(7)) if ignored else weekdays(data.get(key, default), key)
        for prefix in ("departure", "return", "departure_arrival", "return_arrival"):
            for suffix, default in [("min", "18:00"), ("max", "23:59")]:
                key = f"{prefix}_{suffix}"
                fallback = "00:00" if prefix.endswith("arrival") and suffix == "min" else default
                values[key] = data.get(key, fallback)
                clock(values[key])
            if values[f"{prefix}_min"] > values[f"{prefix}_max"]:
                raise SearchValidationError("La fascia oraria deve stare nello stesso giorno.")
        for key, default, low, high in [
            ("min_nights", 2, 0, 30),
            ("max_nights", 2, 0, 30),
            ("max_stops", 0, 0, MAX_STOPS),
            ("adults", 1, 1, 9),
            ("children", 0, 0, 8),
            ("infants", 0, 0, 8),
            ("min_destination_hours", 0, 0, 720),
        ]:
            ignored = key in ("min_nights", "max_nights") and (
                values["date_mode"] == "fixed" or values["trip_type"] == "one_way"
            )
            values[key] = default if ignored else integer(data.get(key, default), key, low, high)
        if values["min_nights"] > values["max_nights"]:
            raise SearchValidationError("Il minimo di notti supera il massimo.")
        if values["adults"] + values["children"] + values["infants"] > MAX_TRAVELERS:
            raise SearchValidationError("Massimo 9 viaggiatori.")
        if values["infants"] > values["adults"]:
            raise SearchValidationError("Serve un adulto per ogni neonato in braccio.")
        try:
            budget = float(data.get("budget", 100))
        except (ValueError, TypeError) as exc:
            raise SearchValidationError("Budget non valido.") from exc
        if (
            isinstance(data.get("budget"), bool)
            or not math.isfinite(budget)
            or not 0 < budget <= MAX_BUDGET
        ):
            raise SearchValidationError("Il budget totale deve essere positivo e finito.")
        values["budget"] = budget
        for key, allowed, default in [
            ("return_airport", ("same", "group"), "same"),
            ("airline_mode", ("all", "low_cost"), "all"),
            ("baggage", ("base", "cabin", "checked"), "base"),
            ("return_departure", ("same", "group"), "same"),
            ("seat", ("economy", "premium-economy", "business", "first"), "economy"),
        ]:
            values[key] = data.get(key, default)
            if values[key] not in allowed:
                raise SearchValidationError(f"Valore non valido per {key}.")
        for key in (
            "allow_self_transfer",
            "allow_airport_change",
            "exclude_basic_economy",
            "schengen_transfers_only",
        ):
            values[key] = data.get(key, False)
            if not isinstance(values[key], bool):
                raise SearchValidationError(f"{key} deve essere vero o falso.")
        for prefix in ("departure_duration", "return_duration"):
            values[prefix + "_min"] = integer(
                data.get(prefix + "_min", 0), prefix, 0, MAX_DURATION_MINUTES
            )
            values[prefix + "_max"] = integer(
                data.get(prefix + "_max", MAX_DURATION_MINUTES), prefix, 0, MAX_DURATION_MINUTES
            )
            if values[prefix + "_min"] > values[prefix + "_max"]:
                raise SearchValidationError("La durata minima supera quella massima.")
        for bound, default in (("min", 0), ("max", MAX_DURATION_MINUTES)):
            key = bound + "_layover_minutes"
            values[key] = integer(data.get(key, default), "Durata scalo", 0, MAX_DURATION_MINUTES)
        if values["min_layover_minutes"] > values["max_layover_minutes"]:
            raise SearchValidationError("Lo scalo minimo supera quello massimo.")
        airlines = data.get("airlines", [])
        if isinstance(airlines, str):
            airlines = re.split(r"[\s,;]+", airlines.strip()) if airlines.strip() else []
        if not isinstance(airlines, (tuple, list)) or any(
            not isinstance(c, str) or not re.fullmatch(r"[A-Z0-9]{2}", c.upper()) for c in airlines
        ):
            raise SearchValidationError("Compagnie: usa codici IATA di due caratteri, es. FR, V7.")
        values["airlines"] = tuple(dict.fromkeys(c.upper() for c in airlines))
        final = data.get("final_arrival_max") or None
        if final:
            clock(final)
        values["final_arrival_max"] = final
        values["destinations"] = airport_codes(
            data.get("destinations", []), "Destinazioni", MAX_DESTINATIONS
        )
        from .airports import DESTINATION_SETS

        values["destination_set"] = data.get("destination_set", "real")
        if values["destination_set"] not in ("real", *DESTINATION_SETS):
            raise SearchValidationError("Gruppo di destinazioni non riconosciuto.")
        if values["return_departure"] == "group" and not values["destinations"]:
            raise SearchValidationError(
                "Per ripartire da un altro aeroporto scegli un gruppo di destinazioni esplicito."
            )
        request = cls(**values)
        if not request.date_pairs():
            raise SearchValidationError("Nessuna coppia di date rispetta giorni e notti scelti.")
        return request

    def date_pairs(self):
        start, end = date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)
        if self.date_mode == "fixed":
            return [(self.start_date, self.end_date if self.trip_type == "round_trip" else None)]
        pairs = []
        while start <= end:
            if start.weekday() in self.departure_days:
                if self.trip_type == "one_way":
                    pairs.append((start.isoformat(), None))
                else:
                    for nights in range(self.min_nights, self.max_nights + 1):
                        back = start + timedelta(days=nights)
                        if back <= end and back.weekday() in self.return_days:
                            pairs.append((start.isoformat(), back.isoformat()))
            start += timedelta(days=1)
        return pairs

    def to_dict(self):
        return asdict(self)


@lru_cache(maxsize=1)
def airport_zones():
    import airportsdata

    return {code: info["tz"] for code, info in airportsdata.load("IATA").items()}


@lru_cache(maxsize=1)
def airport_countries():
    import airportsdata

    return {code: info["country"] for code, info in airportsdata.load("IATA").items()}


def local_instant(value, code):
    """Do not guess an offset when DST makes a local timestamp ambiguous."""
    zone = ZoneInfo(airport_zones()[code])
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        return dt.astimezone(zone)
    first, second = dt.replace(tzinfo=zone, fold=0), dt.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError("Orario ambiguo o inesistente al cambio di ora.")
    if first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != dt:
        raise ValueError("Orario locale inesistente.")
    return first


def fingerprint(quote):
    parts = [
        (
            s["from_code"],
            s["to_code"],
            s["departure"],
            s["arrival"],
            s["airline_code"],
            s["flight_number"],
        )
        for s in quote["segments"]
    ]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:24]


def matches(quote, request, origin, destination, day, inbound=False):  # noqa: PLR0911, PLR0912 - fail closed per constraint
    """Validate every constraint again against returned facts, down to the minute."""
    if quote.get("verification") != "google_live" or not quote.get("segments"):
        return False
    segments = quote["segments"]
    if segments[0]["from_code"] != origin or segments[-1]["to_code"] != destination:
        return False
    if len(segments) - 1 > request.max_stops:
        return False
    if (
        not request.allow_self_transfer
        and len(segments) > 1
        and quote.get("self_transfer") is not False
    ):
        return False  # unknown protection is not a protected connection
    if not request.allow_airport_change and any(
        a["to_code"] != b["from_code"] for a, b in pairwise(segments)
    ):
        return False
    if request.schengen_transfers_only and any(
        airport_countries().get(code) not in SCHENGEN_COUNTRIES
        for a, b in pairwise(segments)
        for code in (a["to_code"], b["from_code"])
    ):
        return False
    if quote.get("currency") != request.currency or quote.get("baggage") != request.baggage:
        return False
    price = quote.get("price")
    if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        return False
    if quote.get("travelers") != request.adults + request.children + request.infants:
        return False
    previous = None
    for seg in segments:
        if not seg.get("flight_number") or not seg.get("airline_code"):
            return False
        if request.airline_mode == "low_cost" and seg["airline_code"] not in LOW_COST:
            return False
        if request.airlines and seg["airline_code"] not in request.airlines:
            return False
        dep, arr = datetime.fromisoformat(seg["departure"]), datetime.fromisoformat(seg["arrival"])
        if dep.tzinfo is None or arr.tzinfo is None:
            return False
        if arr <= dep or (previous and dep < previous):
            return False
        if previous:
            layover = (
                dep.astimezone(timezone.utc) - previous.astimezone(timezone.utc)
            ).total_seconds() / 60
            if not request.min_layover_minutes <= layover <= request.max_layover_minutes:
                return False
        previous = arr
    departure = datetime.fromisoformat(segments[0]["departure"])
    if departure.date().isoformat() != day:
        return False
    prefix = "return" if inbound else "departure"
    start, end = clock(getattr(request, prefix + "_min")), clock(getattr(request, prefix + "_max"))
    if not start <= departure.time().replace(tzinfo=None) <= end:
        return False
    arrival = datetime.fromisoformat(segments[-1]["arrival"])
    elapsed = (
        arrival.astimezone(timezone.utc) - departure.astimezone(timezone.utc)
    ).total_seconds() / 60
    if (
        not getattr(request, prefix + "_duration_min")
        <= elapsed
        <= getattr(request, prefix + "_duration_max")
    ):
        return False
    arrival_time = arrival.time().replace(tzinfo=None)
    if (
        not clock(getattr(request, prefix + "_arrival_min"))
        <= arrival_time
        <= clock(getattr(request, prefix + "_arrival_max"))
    ):
        return False
    if (inbound or request.trip_type == "one_way") and request.final_arrival_max:
        deadline = local_instant(f"{day}T{request.final_arrival_max}:00", destination)
        if arrival > deadline:
            return False
    return True


def single_trip(outbound, request):
    if outbound["price"] > request.budget:
        return None
    return {
        "id": fingerprint(outbound),
        "trip_type": "one_way",
        "price": outbound["price"],
        "currency": request.currency,
        "outbound": outbound,
        "inbound": None,
        "useful_minutes": None,
        "nights": None,
        "ticketing": "one_way",
        "verification": "google_live",
        "airline_verified": False,
        "checked_at": outbound["checked_at"],
    }


def combine(outbound, inbound, request):
    """Two live one-way prices, never an estimated round-trip lead price."""
    total = round(outbound["price"] + inbound["price"], 2)
    if total > request.budget:
        return None
    arrival = datetime.fromisoformat(outbound["segments"][-1]["arrival"])
    departure = datetime.fromisoformat(inbound["segments"][0]["departure"])
    useful = int(
        (departure.astimezone(timezone.utc) - arrival.astimezone(timezone.utc)).total_seconds() / 60
    )
    if useful <= 0 or useful < request.min_destination_hours * 60:
        return None
    out_id, in_id = fingerprint(outbound), fingerprint(inbound)
    return {
        "trip_type": "round_trip",
        "id": out_id + in_id,
        "price": total,
        "currency": request.currency,
        "outbound": outbound,
        "inbound": inbound,
        "useful_minutes": useful,
        "nights": (
            datetime.fromisoformat(inbound["segments"][0]["departure"]).date()
            - datetime.fromisoformat(outbound["segments"][0]["departure"]).date()
        ).days,
        "ticketing": "separate_one_way",
        "verification": "google_live",
        "airline_verified": False,
        "checked_at": min(outbound["checked_at"], inbound["checked_at"]),
        "destination_airport_change": (
            outbound["segments"][-1]["to_code"] != inbound["segments"][0]["from_code"]
        ),
    }


def return_routes(request, origin, destination):
    if request.trip_type == "one_way":
        return []
    departures = request.destinations if request.return_departure == "group" else (destination,)
    homes = request.origins if request.return_airport == "group" else (origin,)
    return [(away, home) for away in departures for home in homes if away != home]


def build_plan(request, route_map):
    """Present work and coverage before starting; never truncate silently."""
    if request.destinations:
        candidates = list(request.destinations)
        source = "Destinazioni indicate da te"
    else:
        candidates = sorted({c for values in route_map.values() for c in values})
        source = "Destinazioni candidate dai cataloghi delle rotte; copertura non esaustiva"
        if request.destination_set != "real":
            from .airports import DESTINATION_SETS

            source = f"Gruppo di destinazioni: {DESTINATION_SETS[request.destination_set][0]}"
    candidates = [c for c in candidates if any(c != origin for origin in request.origins)]
    if not candidates:
        raise SearchValidationError("Nessuna destinazione candidata disponibile.")
    pairs = request.date_pairs()
    task_count = len(pairs) * sum(c != o for c in candidates for o in request.origins)
    if task_count > MAX_TASKS:
        raise SearchValidationError(
            f"La ricerca genera {task_count:,} combinazioni: massimo {MAX_TASKS:,}. "
            "Riduci il periodo, le destinazioni o l'intervallo di notti."
        )
    # Test the union from every selected origin: do not treat a missing route as absence of service.
    tasks = [
        (out, back, dest, origin)
        for out, back in pairs
        for dest in candidates
        for origin in request.origins
        if dest != origin
    ]
    keys = set()
    upper_bound = 0
    for out, back, dest, origin in tasks:
        keys.add((origin, dest, out, False))
        returns = return_routes(request, origin, dest)
        upper_bound += 1 + len(returns)
        if upper_bound > MAX_TASKS * 2:
            raise SearchValidationError(
                "Riduci gli aeroporti o le date: troppi controlli di ritorno."
            )
        for away, home in returns:
            keys.add((away, home, back, True))
    return {
        "date_pairs": pairs,
        "destinations": candidates,
        "candidate_count": len(candidates),
        "tasks": tasks,
        "distinct_searches": len(keys),
        # Cache entries may expire during a long run. Count every possible call
        # for a full pass, not just the unique legs that could share a cache hit.
        "searches_upper_bound": upper_bound,
        "coverage": source,
        "origin_route_counts": {o: len(route_map.get(o, [])) for o in request.origins},
        "notice": (
            "Gli orari da catalogo non confermano un volo. "
            "Solo offerte live complete nei risultati."
        ),
    }
