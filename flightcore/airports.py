"""
Catalogo aeroporti, completamente offline.

Il catalogo principale arriva da ``fli``. ``airportsdata`` aggiunge i codici
IATA correnti che possono mancare nell'enum statico di fli, mantenendo
l'autocomplete indipendente dalla rete.
"""

from __future__ import annotations

import functools
import json
import unicodedata
from pathlib import Path

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
    catalog: dict[str, str] = {}
    try:
        import airportsdata

        catalog.update(
            {
                code: info["name"]
                for code, info in airportsdata.load("IATA").items()
                if info.get("name")
            }
        )
    except ImportError:
        pass
    try:
        from fli.models import Airport
    except ImportError:
        return {**catalog, **HOME_AIRPORTS}
    catalog.update({airport.name: airport.value for airport in Airport})
    return {**catalog, **HOME_AIRPORTS}


def name(code: str) -> str | None:
    return _catalog().get(code.upper())


# Names for presentation, independent of route planning and saved quotes.
DISPLAY_NAMES = {
    "VCE": ("Venezia", "Venezia Marco Polo"),
    "TSF": ("Treviso", "Treviso Antonio Canova"),
    "BCN": ("Barcellona", "Barcellona El Prat"),
    "BGY": ("Bergamo", "Bergamo Orio al Serio"),
    "VRN": ("Verona", "Verona Valerio Catullo"),
    "BLQ": ("Bologna", "Bologna Guglielmo Marconi"),
    "FCO": ("Roma", "Roma Fiumicino Leonardo da Vinci"),
    "CIA": ("Roma", "Roma Ciampino"),
    "MXP": ("Milano", "Milano Malpensa"),
    "LIN": ("Milano", "Milano Linate"),
    "LHR": ("Londra", "Londra Heathrow"),
    "LGW": ("Londra", "Londra Gatwick"),
    "STN": ("Londra", "Londra Stansted"),
    "LTN": ("Londra", "Londra Luton"),
    "CDG": ("Parigi", "Parigi Charles de Gaulle"),
    "ORY": ("Parigi", "Parigi Orly"),
    "BVA": ("Parigi", "Parigi Beauvais-Tillé"),
    "MAD": ("Madrid", "Madrid Barajas Adolfo Suárez"),
    "LIS": ("Lisbona", "Lisbona Humberto Delgado"),
    "OPO": ("Porto", "Porto Francisco Sá Carneiro"),
}


@functools.lru_cache(maxsize=1)
def _italian_names() -> dict[str, dict[str, str]]:
    """Snapshot da Wikidata, vedi ``italian_names``."""
    path = Path(__file__).with_name("data") / "airport_names_it.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))["airports"]
    except (OSError, ValueError, KeyError):
        return {}


@functools.lru_cache(maxsize=1)
def _airport_metadata():
    import airportsdata

    return airportsdata.load("IATA")


def details(code: str) -> dict[str, str]:
    """Readable labels, also for quotes saved before names were introduced."""
    code = code.upper()
    metadata = _airport_metadata().get(code, {})
    italian = _italian_names().get(code, {})
    full_name = (
        italian.get("name") or name(code) or metadata.get("name") or "Aeroporto non identificato"
    )
    city = italian.get("city") or metadata.get("city") or full_name
    city, full_name = DISPLAY_NAMES.get(code, (city, full_name))
    # ISO 3166-1 alpha-2, for the flag next to the city name ("" if unknown).
    country = (metadata.get("country") or "").lower()
    return {"code": code, "city": city, "name": full_name, "country": country}


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
        {
            "code": code,
            "name": catalog[code],
            "city": details(code)["city"],
            "home": code in HOME_AIRPORTS,
        }
        for code in final[:limit]
    ]


# Main international airports by ISO country, to offer every country as a
# destination even when no catalog lists it. Hand-picked gateways, not a
# complete list; codes unknown to the catalog are dropped.
MAIN_AIRPORTS = {
    "ae": "DXB AUH SHJ", "af": "KBL", "ag": "ANU", "al": "TIA", "am": "EVN", "ao": "LAD",
    "ar": "EZE AEP COR MDZ", "at": "VIE SZG INN GRZ", "au": "SYD MEL BNE PER ADL", "aw": "AUA",
    "az": "GYD", "ba": "SJJ", "bb": "BGI", "bd": "DAC", "be": "BRU CRL", "bg": "SOF VAR BOJ",
    "bh": "BAH", "bn": "BWN", "bo": "VVI LPB", "br": "GRU GIG BSB SSA REC FOR", "bs": "NAS",
    "bt": "PBH", "bw": "GBE", "by": "MSQ", "bz": "BZE", "ca": "YYZ YVR YUL YYC", "ch": "ZRH GVA",
    "ci": "ABJ", "cl": "SCL", "cm": "DLA", "cn": "PEK PKX PVG CAN SZX CTU", "co": "BOG MDE CTG",
    "cr": "SJO LIR", "cu": "HAV VRA", "cv": "SID BVC RAI", "cy": "LCA PFO", "cz": "PRG",
    "de": "FRA MUC BER DUS HAM", "dk": "CPH BLL", "do": "PUJ SDQ", "dz": "ALG", "ec": "UIO GYE",
    "ee": "TLL", "eg": "CAI HRG SSH RMF", "es": "MAD BCN AGP PMI ALC", "et": "ADD",
    "fi": "HEL RVN", "fj": "NAN", "fr": "CDG ORY NCE LYS MRS", "gb": "LHR LGW MAN EDI STN",
    "ge": "TBS KUT BUS", "gh": "ACC", "gr": "ATH SKG HER RHO CFU", "gt": "GUA", "hk": "HKG",
    "hn": "SAP", "hr": "ZAG SPU DBV ZAD PUY", "hu": "BUD", "id": "CGK DPS SUB",
    "ie": "DUB ORK SNN", "il": "TLV", "in": "DEL BOM BLR MAA HYD CCU COK GOI", "iq": "BGW EBL",
    "ir": "IKA", "is": "KEF", "it": "FCO MXP NAP CTA PMO", "jm": "MBJ KIN", "jo": "AMM AQJ",
    "jp": "NRT HND KIX NGO FUK", "ke": "NBO MBA", "kg": "FRU BSZ", "kh": "PNH KTI REP SAI",
    "kr": "ICN GMP PUS CJU", "kw": "KWI", "kz": "ALA NQZ", "la": "VTE LPQ", "lb": "BEY",
    "lk": "CMB", "lt": "VNO KUN", "lu": "LUX", "lv": "RIX", "ma": "CMN RAK AGA FEZ TNG",
    "md": "KIV RMO", "me": "TGD TIV", "mg": "TNR", "mk": "SKP", "mm": "RGN", "mn": "UBN",
    "mo": "MFM", "mt": "MLA", "mu": "MRU", "mv": "MLE", "mx": "MEX CUN GDL", "my": "KUL PEN LGK",
    "mz": "MPM", "na": "WDH", "ng": "LOS ABV", "nl": "AMS EIN RTM", "no": "OSL BGO TOS",
    "np": "KTM", "nz": "AKL CHC WLG", "om": "MCT SLL", "pa": "PTY", "pe": "LIM CUZ",
    "ph": "MNL CEB", "pk": "KHI LHE ISB", "pl": "WAW KRK GDN WRO", "pt": "LIS OPO FAO FNC PDL",
    "py": "ASU", "qa": "DOH", "ro": "OTP CLJ IAS", "rs": "BEG", "ru": "SVO DME LED", "rw": "KGL",
    "sa": "JED RUH DMM", "sc": "SEZ", "se": "ARN GOT", "sg": "SIN", "si": "LJU", "sk": "BTS KSC",
    "sn": "DSS", "sv": "SAL", "th": "BKK DMK HKT CNX USM", "tn": "TUN DJE MIR",
    "tr": "IST SAW AYT ADB ESB", "tt": "POS", "tw": "TPE", "tz": "DAR ZNZ JRO", "ua": "KBP",
    "ug": "EBB", "us": "JFK EWR LAX SFO MIA ORD BOS", "uy": "MVD", "uz": "TAS SKD", "ve": "CCS",
    "vn": "SGN HAN DAD", "xk": "PRN", "za": "JNB CPT DUR", "zm": "LUN", "zw": "HRE VFA",
}  # fmt: skip


def countries(origins: list[str]) -> dict[str, dict]:
    """Airports to pick a whole country at once, by ISO country code.

    `linked` countries list the candidates of an "Ovunque" search: the route
    catalogs of the origins, or the general pool for origins without one.
    The other countries list the airports the portal knows from any catalog,
    curated set or `MAIN_AIRPORTS`: no direct flight from these origins is on record.
    Like the catalogs, neither list is exhaustive.
    """
    from . import routes

    snapshot = json.loads(routes.SNAPSHOT.read_text())
    pool = {c for codes in snapshot.values() if isinstance(codes, list) for c in codes}
    linked = {code for origin in origins for code in routes.destinations(origin.upper())} or pool
    known = pool | {code for _label, codes in DESTINATION_SETS.values() for code in codes}
    known |= {code for codes in MAIN_AIRPORTS.values() for code in codes.split()}
    grouped: dict[str, dict] = {}
    for is_linked, codes in ((True, linked), (False, known - linked)):
        for code in sorted(codes):
            info = details(code)
            if not exists(code) or not info["country"] or code.upper() in map(str.upper, origins):
                continue
            entry = grouped.setdefault(info["country"], {"linked": is_linked, "airports": []})
            if entry["linked"] == is_linked:
                entry["airports"].append(
                    {"code": code, "city": info["city"], "name": info["name"]}
                )
    return grouped


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
