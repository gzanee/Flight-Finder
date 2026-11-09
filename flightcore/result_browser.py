"""Browse saved quotes without making any provider calls."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .airports import details as airport_details
from .flexible import SearchValidationError

PAGE_SIZE = 50
MAX_EXTRA_EUROS = 20
MAX_EXTRA_SHARE = 0.5
MAX_EUROS_PER_HOUR = 3


def number(value, label):
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SearchValidationError(f"{label}: indica un numero positivo.") from exc
    if not math.isfinite(result) or result < 0:
        raise SearchValidationError(f"{label}: indica un numero positivo.")
    return result


def code(value, length):
    value = (value or "").upper()
    if value and not re.fullmatch(rf"[A-Z0-9]{{{length}}}", value):
        raise SearchValidationError("Codice aeroporto o compagnia non valido.")
    return value


def codes(value):
    return tuple(code(c.strip(), 3) for c in (value or "").split(",") if c.strip())


@dataclass(frozen=True)
class ResultFilters:
    max_price: float | None = None
    min_hours: float | None = None
    origin: str = ""
    destination: str = ""
    airline: str = ""
    home_before: str = ""
    excluded: tuple[str, ...] = ()

    @classmethod
    def parse(cls, args):
        home_before = args.get("home_before", "")
        if home_before and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", home_before):
            raise SearchValidationError("Orario di rientro non valido.")
        excluded = codes(args.get("exclude"))
        return cls(
            max_price=number(args.get("max_price"), "Prezzo massimo"),
            min_hours=number(args.get("min_hours"), "Ore minime"),
            origin=code(args.get("origin"), 3),
            # Comma-separated: a city group covers all its airports (STN,LTN).
            destination=",".join(codes(args.get("destination"))),
            airline=code(args.get("airline"), 2),
            home_before=home_before,
            excluded=excluded,
        )

    def matches(self, trip):
        out = trip["outbound"]["segments"]
        destination = out[-1]["to_code"]
        checks = (
            self.max_price is None or trip["price"] <= self.max_price,
            not self.min_hours or (trip["useful_minutes"] or 0) >= self.min_hours * 60,
            not self.origin or out[0]["from_code"] == self.origin,
            not self.destination or destination in self.destination.split(","),
            destination not in self.excluded,
            not self.airline or all(s["airline_code"] == self.airline for s in segments(trip)),
        )
        return all(checks) and self.matches_return(trip)

    def matches_return(self, trip):
        if not self.home_before:
            return True
        if not trip["inbound"]:
            return False
        back = trip["inbound"]["segments"]
        # Compare local calendar dates as well as the clock: 01:00 next day
        # must not pass a 23:00 deadline on the day of the return departure.
        deadline = back[0]["departure"][:10] + "T" + self.home_before
        return back[-1]["arrival"][:16] <= deadline


def segments(trip):
    return [
        segment for leg in (trip["outbound"], trip["inbound"]) if leg for segment in leg["segments"]
    ]


def sort_key(trip, sort):
    if sort == "useful":
        return (-(trip["useful_minutes"] or 0), trip["price"], trip["id"])
    if sort == "duration":
        total = trip["outbound"]["duration_min"]
        total += trip["inbound"]["duration_min"] if trip["inbound"] else 0
        return (total, trip["price"], trip["id"])
    if sort == "departure":
        return (trip["outbound"]["segments"][0]["departure"], trip["price"], trip["id"])
    return (trip["price"], -(trip["useful_minutes"] or 0), trip["id"])


def summary(trip):
    out = trip["outbound"]["segments"]
    back = trip["inbound"]
    return {
        "id": trip["id"],
        "origin": out[0]["from_code"],
        "destination": out[-1]["to_code"],
        "date": out[0]["departure"][:10],
        "return_date": back["segments"][0]["departure"][:10] if back else None,
        "price": trip["price"],
        "useful_minutes": trip["useful_minutes"],
        "destination_airport_change": trip.get("destination_airport_change", False),
        "needs_recheck": trip["needs_recheck"],
    }


def facets(trips):
    carriers = {
        s["airline_code"]: s.get("airline_name") or s["airline_code"]
        for t in trips
        for s in segments(t)
    }
    return {
        "origins": sorted({t["outbound"]["segments"][0]["from_code"] for t in trips}),
        "destinations": sorted({t["outbound"]["segments"][-1]["to_code"] for t in trips}),
        "airlines": [
            {"code": c, "name": n} for c, n in sorted(carriers.items(), key=lambda x: x[1])
        ],
    }


def recommendations(trips):
    if not trips:
        return {}
    cheapest = min(trips, key=lambda t: sort_key(t, "price"))
    output = {"cheapest": summary(cheapest)}
    comparable = [
        t
        for t in trips
        if t["inbound"] and t["useful_minutes"] and not t.get("destination_airport_change")
    ]
    if not comparable:
        return output
    longest = min(comparable, key=lambda t: sort_key(t, "useful"))
    output["longest"] = summary(longest)
    if cheapest not in comparable:
        return output
    allowance = max(MAX_EXTRA_EUROS, cheapest["price"] * MAX_EXTRA_SHARE)
    better = [
        t
        for t in comparable
        if t["useful_minutes"] > cheapest["useful_minutes"]
        and t["price"] <= cheapest["price"] + allowance
        and (t["price"] - cheapest["price"])
        <= (t["useful_minutes"] - cheapest["useful_minutes"]) / 60 * MAX_EUROS_PER_HOUR
    ]
    if better:
        best = min(better, key=lambda t: (t["price"] / t["useful_minutes"], t["price"], t["id"]))
        output["balanced"] = {
            **summary(best),
            "extra_price": round(best["price"] - cheapest["price"], 2),
            "extra_minutes": best["useful_minutes"] - cheapest["useful_minutes"],
        }
    return output


def groups(trips):
    """One entry per destination city: London groups STN, LTN, LGW and LHR."""
    grouped = {}
    for trip in trips:
        destination = trip["outbound"]["segments"][-1]["to_code"]
        city = airport_details(destination)["city"]
        if city not in grouped:
            grouped[city] = {
                "city": city,
                "destinations": set(),
                "count": 0,
                "cheapest": trip,
                "featured": summary(trip),
            }
        group = grouped[city]
        group["destinations"].add(destination)
        group["count"] += 1
        if sort_key(trip, "price") < sort_key(group["cheapest"], "price"):
            group["cheapest"] = trip
    output = []
    for group in grouped.values():
        destinations = sorted(group["destinations"])
        output.append(
            {
                **group,
                "destinations": destinations,
                # Ready to use as the `destination` filter of this group.
                "destination": ",".join(destinations),
                "cheapest": summary(group["cheapest"]),
            }
        )
    return output


def calendar_prices(trips):
    calendar = {}
    for trip in trips:
        item = summary(trip)
        key = (item["date"], item["return_date"])
        if key not in calendar or item["price"] < calendar[key]["price"]:
            calendar[key] = {k: item[k] for k in ("date", "return_date", "price")}
    return sorted(calendar.values(), key=lambda x: (x["date"], x["return_date"] or ""))


def browse_results(trips, sort="price", offset=0, filters=None, focus=""):
    filters = filters or ResultFilters()
    filtered = sorted((t for t in trips if filters.matches(t)), key=lambda t: sort_key(t, sort))
    focused_index = next((i for i, t in enumerate(filtered) if t["id"] == focus), None)
    if focused_index is not None:
        offset = focused_index // PAGE_SIZE * PAGE_SIZE
    offset = min(offset, max(0, (len(filtered) - 1) // PAGE_SIZE * PAGE_SIZE))
    return {
        "result_count": len(trips),
        "filtered_count": len(filtered),
        "facets": facets(trips),
        "groups": groups(filtered),
        "recommendations": recommendations(filtered),
        "calendar": calendar_prices(filtered),
        "results": filtered[offset : offset + PAGE_SIZE],
        "offset": offset,
        "has_more": offset + PAGE_SIZE < len(filtered),
        "focused_id": focus if focused_index is not None else None,
    }
