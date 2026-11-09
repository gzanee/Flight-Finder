"""
Nomi italiani di aeroporti e citta', da Wikidata.

``airportsdata`` e ``fli`` danno nomi inglesi o locali: "Gdańsk Lech Wałęsa
Airport" invece di Danzica. Wikidata ha l'etichetta italiana dell'aeroporto e
della citta' servita (P931). Lo snapshot in ``data/airport_names_it.json`` copre
gli aeroporti del portale; gli altri restano col nome del catalogo offline.

Rigenera con ``python -m flightcore.italian_names``.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from . import airports

SPARQL = "https://query.wikidata.org/sparql"
USER_AGENT = "flight-finder/1.0 (personal project)"
PATH = Path(__file__).with_name("data") / "airport_names_it.json"
BATCH = 150
# P931 sometimes names a region or the wrong entity (LMP: a dog breed), and
# archaic exonyms (Coo, Candia, Ragusa for Dubrovnik) are hard to recognize.
# Secondary airports sold under a metro name are grouped with that city.
CITY_OVERRIDES = {
    "AAL": "Aalborg",
    "ACE": "Lanzarote",
    "AYT": "Antalya",
    "BSL": "Basilea",
    "BVA": "Parigi",
    "BWK": "Brač",
    "CHQ": "Chania",
    "CRL": "Bruxelles",
    "DBV": "Dubrovnik",
    "DFW": "Dallas",
    "DUS": "Düsseldorf",
    "EMA": "East Midlands",
    "EVE": "Narvik",
    "EWR": "New York",
    "HER": "Heraklion",
    "ICN": "Seul",
    "JSI": "Skiathos",
    "KGS": "Kos",
    "LMP": "Lampedusa",
    "LPA": "Gran Canaria",
    "MAH": "Minorca",
    "NCL": "Newcastle",
    "NOC": "Knock",
    "NRN": "Düsseldorf Weeze",
    "OSL": "Oslo",
    "POZ": "Poznań",
    "PVK": "Preveza",
    "RMU": "Murcia",
    "SID": "Sal",
    "TSR": "Timișoara",
}
QUERY = """SELECT ?iata ?airportLabel ?cityLabel WHERE {
 VALUES ?iata { %s }
 ?airport wdt:P238 ?iata .
 OPTIONAL { ?airport wdt:P931 ?city . }
 SERVICE wikibase:label { bd:serviceParam wikibase:language "it". }
}"""


def portal_codes() -> set[str]:
    """Origini, destinazioni delle rotte, rose curate e alias."""
    routes = json.loads((PATH.parent / "routes.json").read_text(encoding="utf-8"))
    codes = set(airports.HOME_AIRPORTS)
    for origin, destinations in routes.items():
        if origin != "_meta":
            codes.update([origin, *destinations])
    for _, listed in airports.DESTINATION_SETS.values():
        codes.update(listed)
    for listed in airports.CITY_ALIASES.values():
        codes.update(listed)
    return codes


def _label(value: str | None) -> str | None:
    # Without an Italian label Wikidata returns the bare entity id.
    if not value or re.fullmatch(r"Q\d+", value):
        return None
    return value[0].upper() + value[1:]


def pick_city(airport_name: str | None, cities: list[str]) -> str | None:
    """P931 can list several places (GDN: Danzica, Tripla Citta'): prefer the named one."""
    if not cities:
        return None
    named = [c for c in cities if airport_name and c.lower() in airport_name.lower()]
    # "Palma di Maiorca" over "Maiorca"; otherwise a stable alphabetical choice.
    return max(named, key=len) if named else sorted(cities)[0]


def fetch(codes: set[str]) -> dict[str, dict[str, str]]:
    found: dict[str, tuple[str | None, list[str]]] = {}
    ordered = sorted(codes)
    for start in range(0, len(ordered), BATCH):
        values = " ".join(f'"{code}"' for code in ordered[start : start + BATCH])
        url = SPARQL + "?format=json&query=" + urllib.parse.quote(QUERY % values)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=90) as response:
            rows = json.load(response)["results"]["bindings"]
        for row in rows:
            code = row["iata"]["value"]
            name, cities = found.setdefault(code, (None, []))
            name = name or _label(row.get("airportLabel", {}).get("value"))
            city = _label(row.get("cityLabel", {}).get("value"))
            if city and city not in cities:
                cities.append(city)
            found[code] = (name, cities)
    names = {}
    for code, (label, cities) in sorted(found.items()):
        name = re.sub(r"\s*\([A-Z]{3}\)$", "", label) if label else None
        entry = {"name": name, "city": CITY_OVERRIDES.get(code) or pick_city(name, cities)}
        entry = {k: v for k, v in entry.items() if v}
        if entry:
            names[code] = entry
    return names


def main():
    names = fetch(portal_codes())
    payload = {
        "_meta": {"generato": date.today().isoformat(), "fonte": "Wikidata (P238, P931)"},
        "airports": names,
    }
    PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(names)} aeroporti in {PATH}")


if __name__ == "__main__":
    main()
