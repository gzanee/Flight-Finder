"""
Catalogo aeroporti, completamente offline.

fli incorpora 7.835 aeroporti con il nome esteso: basta a sostituire l'endpoint
di autocomplete di Amadeus senza chiave API e senza chiamate di rete.
"""

from __future__ import annotations

import functools
import unicodedata

# Aeroporti di partenza del portale.
HOME_AIRPORTS = {
    "VCE": "Venezia Marco Polo",
    "TSF": "Treviso Antonio Canova",
}

# Rose di destinazioni per la ricerca "ovunque". Google non espone piu' un
# endpoint di esplorazione funzionante, quindi la scansione parte da elenchi
# curati: sono le mete effettivamente collegate da VCE/TSF, non tutto il mondo.
DESTINATION_SETS: dict[str, tuple[str, list[str]]] = {
    "europe-short": (
        "Europa, voli brevi",
        ["BCN", "MAD", "CDG", "ORY", "AMS", "BRU", "LHR", "STN", "LGW", "DUB",
         "BER", "MUC", "FRA", "VIE", "ZRH", "PRG", "BUD", "WAW", "KRK", "CPH"],
    ),
    "europe-wide": (
        "Europa, elenco esteso",
        ["BCN", "MAD", "AGP", "VLC", "SVQ", "LIS", "OPO", "CDG", "ORY", "NCE",
         "MRS", "LYS", "AMS", "EIN", "BRU", "CRL", "LHR", "STN", "LGW", "LTN",
         "MAN", "EDI", "DUB", "BER", "MUC", "FRA", "HAM", "CGN", "DUS", "VIE",
         "ZRH", "GVA", "PRG", "BUD", "WAW", "KRK", "GDN", "CPH", "ARN", "OSL",
         "HEL", "TLL", "RIX", "VNO", "ATH", "SKG", "OTP", "SOF", "BEG", "ZAG"],
    ),
    "sun": (
        "Mare e sole",
        ["AGP", "PMI", "IBZ", "ALC", "FAO", "CTA", "PMO", "CAG", "OLB", "BRI",
         "NAP", "ATH", "HER", "RHO", "CFU", "JMK", "JTR", "SPU", "DBV", "TIA",
         "AYT", "IST", "SSH", "HRG", "RAK", "TFS", "LPA", "ACE", "FUE", "MLA"],
    ),
    "city-break": (
        "Weekend in citta'",
        ["BCN", "MAD", "LIS", "CDG", "AMS", "BRU", "LHR", "DUB", "BER", "MUC",
         "VIE", "PRG", "BUD", "KRK", "CPH", "ARN", "OSL", "HEL", "ATH", "IST",
         "LIS", "OPO", "SVQ", "VLC", "EDI", "MAN", "WAW", "BEG", "ZAG", "SOF"],
    ),
    "long-haul": (
        "Lungo raggio",
        ["JFK", "EWR", "BOS", "ORD", "LAX", "SFO", "MIA", "YYZ", "GRU", "EZE",
         "DXB", "DOH", "AUH", "IST", "CAI", "JNB", "BKK", "SIN", "HKG", "NRT",
         "ICN", "DEL", "BOM", "PEK", "PVG", "SYD", "MEX", "CUN", "HAV", "PUJ"],
    ),
}


# Il catalogo riporta i nomi in lingua locale o inglese: chi cerca "londra" o
# "monaco" non troverebbe nulla. Questi alias coprono le mete piu' cercate.
CITY_ALIASES: dict[str, list[str]] = {
    "londra": ["LHR", "LGW", "STN", "LTN"],
    "parigi": ["CDG", "ORY", "BVA"],
    "monaco di baviera": ["MUC"],
    "monaco": ["MUC"],
    "colonia": ["CGN"],
    "francoforte": ["FRA"],
    "amburgo": ["HAM"],
    "berlino": ["BER"],
    "vienna": ["VIE"],
    "zurigo": ["ZRH"],
    "ginevra": ["GVA"],
    "bruxelles": ["BRU", "CRL"],
    "anversa": ["ANR"],
    "amsterdam": ["AMS"],
    "copenaghen": ["CPH"],
    "stoccolma": ["ARN", "BMA"],
    "oslo": ["OSL"],
    "helsinki": ["HEL"],
    "praga": ["PRG"],
    "varsavia": ["WAW", "WMI"],
    "cracovia": ["KRK"],
    "budapest": ["BUD"],
    "bucarest": ["OTP"],
    "atene": ["ATH"],
    "salonicco": ["SKG"],
    "istanbul": ["IST", "SAW"],
    "lisbona": ["LIS"],
    "oporto": ["OPO"],
    "porto": ["OPO"],
    "siviglia": ["SVQ"],
    "malaga": ["AGP"],
    "maiorca": ["PMI"],
    "palma di maiorca": ["PMI"],
    "ibiza": ["IBZ"],
    "tenerife": ["TFS", "TFN"],
    "gran canaria": ["LPA"],
    "lanzarote": ["ACE"],
    "fuerteventura": ["FUE"],
    "valencia": ["VLC"],
    "barcellona": ["BCN"],
    "madrid": ["MAD"],
    "dublino": ["DUB"],
    "edimburgo": ["EDI"],
    "manchester": ["MAN"],
    "belgrado": ["BEG"],
    "zagabria": ["ZAG"],
    "spalato": ["SPU"],
    "ragusa": ["DBV"],
    "dubrovnik": ["DBV"],
    "tirana": ["TIA"],
    "sofia": ["SOF"],
    "malta": ["MLA"],
    "creta": ["HER", "CHQ"],
    "rodi": ["RHO"],
    "corfu": ["CFU"],
    "mykonos": ["JMK"],
    "santorini": ["JTR"],
    "marrakech": ["RAK"],
    "il cairo": ["CAI"],
    "sharm el sheikh": ["SSH"],
    "hurghada": ["HRG"],
    "dubai": ["DXB"],
    "doha": ["DOH"],
    "abu dhabi": ["AUH"],
    "new york": ["JFK", "EWR", "LGA"],
    "boston": ["BOS"],
    "chicago": ["ORD"],
    "los angeles": ["LAX"],
    "san francisco": ["SFO"],
    "miami": ["MIA"],
    "toronto": ["YYZ"],
    "san paolo": ["GRU"],
    "buenos aires": ["EZE"],
    "citta del messico": ["MEX"],
    "l avana": ["HAV"],
    "avana": ["HAV"],
    "bangkok": ["BKK"],
    "singapore": ["SIN"],
    "hong kong": ["HKG"],
    "tokyo": ["NRT", "HND"],
    "seul": ["ICN"],
    "pechino": ["PEK"],
    "shanghai": ["PVG"],
    "delhi": ["DEL"],
    "mumbai": ["BOM"],
    "sydney": ["SYD"],
    "johannesburg": ["JNB"],
    "roma": ["FCO", "CIA"],
    "milano": ["MXP", "LIN", "BGY"],
    "napoli": ["NAP"],
    "palermo": ["PMO"],
    "catania": ["CTA"],
    "bari": ["BRI"],
    "cagliari": ["CAG"],
    "olbia": ["OLB"],
    "venezia": ["VCE", "TSF"],
    "treviso": ["TSF"],
    "bologna": ["BLQ"],
    "verona": ["VRN"],
    "firenze": ["FLR"],
    "pisa": ["PSA"],
    "torino": ["TRN"],
    "genova": ["GOA"],
    "trieste": ["TRS"],
}

#: Chiave speciale: non e' un elenco curato ma le destinazioni realmente
#: servite da ciascun aeroporto, ricostruite da Wikipedia + Wikidata.
REAL_ROUTES_KEY = "real"

DEFAULT_DESTINATION_SET = "europe-short"


@functools.lru_cache(maxsize=1)
def _catalog() -> dict[str, str]:
    """Mappa codice IATA -> nome esteso, letta una volta sola."""
    try:
        from fli.models import Airport
    except ImportError:
        return dict(HOME_AIRPORTS)
    return {airport.name: airport.value for airport in Airport}


def name(code: str) -> str | None:
    return _catalog().get(code.upper())


def exists(code: str) -> bool:
    return code.upper() in _catalog()


def _normalize(text: str) -> str:
    """Minuscolo e senza accenti, cosi' 'zurigo' trova 'Zürich'."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def search(keyword: str, limit: int = 10) -> list[dict]:
    """
    Autocomplete su codice e nome.

    Ordinato per qualita' della corrispondenza: prima il codice IATA esatto,
    poi i nomi che iniziano col termine cercato, infine il resto.
    """
    keyword = (keyword or "").strip()
    if len(keyword) < 2:
        return []

    needle = _normalize(keyword)
    scored: list[tuple[int, str, str]] = []

    # Gli alias hanno la precedenza: chi scrive "londra" vuole Londra, non un
    # aeroporto il cui nome inglese contiene per caso quelle lettere.
    alias_hits = [
        code
        for alias, codes in CITY_ALIASES.items()
        if alias.startswith(needle) or needle == alias
        for code in codes
    ]

    for code, airport_name in _catalog().items():
        haystack = _normalize(airport_name)
        if needle == code.lower():
            score = 0
        elif haystack.startswith(needle):
            score = 1
        elif needle in haystack:
            score = 2
        elif code.lower().startswith(needle):
            score = 3
        else:
            continue
        scored.append((score, airport_name, code))

    scored.sort(key=lambda row: (row[0], len(row[1]), row[1]))
    ordered = [code for _, _, code in scored]

    # Alias in testa, poi le corrispondenze testuali, senza duplicati.
    final: list[str] = []
    for code in alias_hits + ordered:
        if code not in final and exists(code):
            final.append(code)

    catalog = _catalog()
    return [
        {"code": code, "name": catalog[code], "home": code in HOME_AIRPORTS}
        for code in final[:limit]
    ]


def destination_set(key: str) -> list[str]:
    """Destinazioni di una rosa, scartando i codici che il catalogo non conosce."""
    _, codes = DESTINATION_SETS.get(key, DESTINATION_SETS[DEFAULT_DESTINATION_SET])
    seen: list[str] = []
    for code in codes:
        if code not in seen and exists(code):
            seen.append(code)
    return seen


def real_destinations(origins: list[str]) -> dict[str, list[str]]:
    """Destinazioni realmente servite da ciascun aeroporto di partenza."""
    from . import routes

    known = set(_catalog())
    return routes.destinations_for(origins, known=known)


def resolve_destinations(
    origins: list[str], set_key: str | None, explicit: list[str] | None = None
) -> dict[str, list[str]]:
    """
    Decide su quali mete cercare, per ciascun aeroporto di partenza.

    Un elenco esplicito vince su tutto; la chiave "real" usa le rotte vere;
    qualsiasi altra chiave usa una rosa curata, uguale per tutte le origini.
    """
    if explicit:
        codes = [c.upper() for c in explicit if exists(c)]
        return {origin: list(codes) for origin in origins}

    if set_key == REAL_ROUTES_KEY:
        return real_destinations(origins)

    codes = destination_set(set_key or DEFAULT_DESTINATION_SET)
    return {origin: list(codes) for origin in origins}


def destination_sets_info() -> list[dict]:
    """Rose disponibili, con la voce delle rotte reali in testa."""
    from . import routes

    reali = {
        "key": REAL_ROUTES_KEY,
        "label": "Tutte le destinazioni servite",
        "count": len({c for o in HOME_AIRPORTS for c in routes.destinations(o)}),
        "real": True,
    }
    curate = [
        {"key": key, "label": label, "count": len(destination_set(key)), "real": False}
        for key, (label, _) in DESTINATION_SETS.items()
    ]
    return [reali, *curate]
