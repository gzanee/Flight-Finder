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

SUPPORTED_ORIGINS = ("VCE", "TSF", "VRN", "BLQ", "BGY")
# Explicit, conservative selection; editable without claiming a universal classification.
MAX_PERIOD_DAYS = 366
MAX_TRAVELERS = 9
MAX_BUDGET = 100000
MAX_DESTINATIONS = 200
MAX_TASKS = 50000

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

    @classmethod
    def parse(cls, data, today=None):  # noqa: PLR0912, PLR0915 - validate the request at one boundary
        if not isinstance(data, dict):
            raise SearchValidationError("Invia un oggetto JSON.")
        today = today or date.today()
        try:
            start, end = (
                date.fromisoformat(data["start_date"]),
                date.fromisoformat(data["end_date"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise SearchValidationError("Indica inizio e fine del periodo di ricerca.") from exc
        if start < today or end < start or (end - start).days > MAX_PERIOD_DAYS:
            raise SearchValidationError("Periodo non valido: date future, massimo un anno.")
        origins = data.get("origins", ["VCE", "TSF"])
        if not isinstance(origins, list) or not origins:
            raise SearchValidationError("Seleziona almeno un aeroporto di partenza.")
        if any(o not in SUPPORTED_ORIGINS for o in origins):
            raise SearchValidationError("Origini disponibili: VCE, TSF, VRN, BLQ, BGY.")
        values = {
            "origins": tuple(dict.fromkeys(origins)),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        for key, default in [("departure_days", [4]), ("return_days", [6])]:
            values[key] = weekdays(data.get(key, default), key)
        for prefix in ("departure", "return"):
            for suffix, default in [("min", "18:00"), ("max", "23:59")]:
                key = f"{prefix}_{suffix}"
                values[key] = data.get(key, default)
                clock(values[key])
            if values[f"{prefix}_min"] > values[f"{prefix}_max"]:
                raise SearchValidationError("La fascia oraria deve stare nello stesso giorno.")
        for key, default, low, high in [
            ("min_nights", 2, 1, 30),
            ("max_nights", 2, 1, 30),
            ("max_stops", 0, 0, 1),
            ("adults", 1, 1, 9),
            ("children", 0, 0, 8),
            ("min_destination_hours", 0, 0, 720),
        ]:
            values[key] = integer(data.get(key, default), key, low, high)
        if values["min_nights"] > values["max_nights"]:
            raise SearchValidationError("Il minimo di notti supera il massimo.")
        if values["adults"] + values["children"] > MAX_TRAVELERS:
            raise SearchValidationError("Massimo 9 viaggiatori.")
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
        ]:
            values[key] = data.get(key, default)
            if values[key] not in allowed:
                raise SearchValidationError(f"Valore non valido per {key}.")
        values["allow_self_transfer"] = data.get("allow_self_transfer", False)
        if not isinstance(values["allow_self_transfer"], bool):
            raise SearchValidationError("Self-transfer deve essere vero o falso.")
        final = data.get("final_arrival_max") or None
        if final:
            clock(final)
        values["final_arrival_max"] = final
        raw = data.get("destinations", [])
        if isinstance(raw, str):
            raw = re.split(r"[\s,;]+", raw.strip()) if raw.strip() else []
        if not isinstance(raw, list) or len(raw) > MAX_DESTINATIONS:
            raise SearchValidationError("Elenco destinazioni non valido (massimo 200).")
        from .airports import exists

        if any(not isinstance(c, str) or not exists(c) for c in raw):
            raise SearchValidationError("Uno dei codici destinazione non esiste.")
        values["destinations"] = tuple(dict.fromkeys(c.upper() for c in raw))
        request = cls(**values)
        if not request.date_pairs():
            raise SearchValidationError("Nessuna coppia di date rispetta giorni e notti scelti.")
        return request

    def date_pairs(self):
        start, end = date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)
        pairs = []
        while start <= end:
            if start.weekday() in self.departure_days:
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
    if not request.allow_self_transfer and len(segments) > 1:
        if quote.get("self_transfer") is not False:
            return False  # unknown protection is not a protected connection
        if any(a["to_code"] != b["from_code"] for a, b in pairwise(segments)):
            return False
    if quote.get("currency") != request.currency or quote.get("baggage") != request.baggage:
        return False
    price = quote.get("price")
    if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        return False
    if quote.get("travelers") != request.adults + request.children:
        return False
    previous = None
    for seg in segments:
        if not seg.get("flight_number") or not seg.get("airline_code"):
            return False
        if request.airline_mode == "low_cost" and seg["airline_code"] not in LOW_COST:
            return False
        dep, arr = datetime.fromisoformat(seg["departure"]), datetime.fromisoformat(seg["arrival"])
        if dep.tzinfo is None or arr.tzinfo is None:
            return False
        if arr <= dep or (previous and dep < previous):
            return False
        previous = arr
    departure = datetime.fromisoformat(segments[0]["departure"])
    if departure.date().isoformat() != day:
        return False
    prefix = "return" if inbound else "departure"
    start, end = clock(getattr(request, prefix + "_min")), clock(getattr(request, prefix + "_max"))
    if not start <= departure.time().replace(tzinfo=None) <= end:
        return False
    if inbound and request.final_arrival_max:
        arrival = datetime.fromisoformat(segments[-1]["arrival"])
        deadline = local_instant(f"{day}T{request.final_arrival_max}:00", destination)
        if arrival > deadline:
            return False
    return True


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
    }


def build_plan(request, route_map):
    """Present work and coverage before starting; never truncate silently."""
    if request.destinations:
        candidates = list(request.destinations)
        source = "Destinazioni indicate da te"
    else:
        candidates = sorted({c for values in route_map.values() for c in values})
        source = "Destinazioni candidate dai cataloghi delle rotte; copertura non esaustiva"
    candidates = [c for c in candidates if c not in request.origins]
    if not candidates:
        raise SearchValidationError("Nessuna destinazione candidata disponibile.")
    pairs = request.date_pairs()
    task_count = len(pairs) * len(candidates) * len(request.origins)
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
    ]
    keys = set()
    for out, back, dest, origin in tasks:
        keys.add((origin, dest, out, False))
        for home in request.origins if request.return_airport == "group" else (origin,):
            keys.add((dest, home, back, True))
    return {
        "date_pairs": pairs,
        "destinations": candidates,
        "candidate_count": len(candidates),
        "tasks": tasks,
        "distinct_searches": len(keys),
        # Cache entries may expire during a long run. Count every possible call
        # for a full pass, not just the unique legs that could share a cache hit.
        "searches_upper_bound": task_count
        * (1 + (len(request.origins) if request.return_airport == "group" else 1)),
        "coverage": source,
        "origin_route_counts": {o: len(route_map.get(o, [])) for o in request.origins},
        "notice": (
            "Gli orari da catalogo non confermano un volo. "
            "Solo offerte live complete nei risultati."
        ),
    }
