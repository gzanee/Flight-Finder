"""
Destinazioni realmente servite da un aeroporto.

Le rose di destinazioni in `airports.py` sono elenchi curati a mano: utili come
punto di partenza, ma non sanno nulla delle rotte vere. Se Ryanair apre domani
VCE-Riga, quell'elenco non se ne accorge.

Qui ricostruiamo invece la lista reale, in due passaggi:

1. la tabella "Airlines and destinations" della pagina Wikipedia dell'aeroporto
   da' i collegamenti alle destinazioni servite;
2. Wikidata (proprieta' P238) traduce ogni pagina nel suo codice IATA.

Il secondo passaggio non e' un dettaglio: indovinare il codice dal nome produce
errori gravi e silenziosi — "Athens International Airport" somiglia abbastanza a
un aeroporto indiano da far finire la ricerca a Bikaner. Wikidata da' il codice
ufficiale, oppure nessun codice.

Il risultato viene messo in cache su disco per 30 giorni (le mappe delle rotte
cambiano con le stagioni, non con le ore). Se la rete non risponde o Wikipedia
cambia formato, si ricade sullo snapshot incluso nel repository.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import threading
import time
import urllib.parse
from pathlib import Path

logger = logging.getLogger(__name__)

WIKI_HTML = "https://en.wikipedia.org/api/rest_v1/page/html/{page}"
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

CACHE_TTL_SECONDS = 30 * 24 * 3600
BATCH = 50  # limite delle API di Wikipedia/Wikidata per richiesta
IATA_LENGTH = 3

#: Pagina Wikipedia per ogni aeroporto di partenza gestito dal portale.
WIKI_PAGES = {
    "VCE": "Venice_Marco_Polo_Airport",
    "TSF": "Treviso_Airport",
}

SNAPSHOT = Path(__file__).parent / "data" / "routes.json"

_lock = threading.Lock()
_memo: dict[str, list[str]] = {}


# ---------------------------------------------------------------------- #
# Cache su disco
# ---------------------------------------------------------------------- #


def _cache_path() -> Path:
    import os

    return Path(os.getenv("FLIGHT_FINDER_CACHE_DIR", ".cache")) / "routes.json"


def _read_cache(origin: str) -> list[str] | None:
    try:
        blob = json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return None
    entry = blob.get(origin)
    if not entry or time.time() - entry.get("ts", 0) > CACHE_TTL_SECONDS:
        return None
    return entry.get("destinations") or None


def _write_cache(origin: str, destinations: list[str]) -> None:
    path = _cache_path()
    try:
        blob = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        blob = {}
    blob[origin] = {"ts": time.time(), "destinations": destinations}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(blob, indent=1))
    except OSError as exc:
        logger.warning("Impossibile salvare le rotte di %s: %s", origin, exc)


def _read_snapshot(origin: str) -> list[str]:
    try:
        return json.loads(SNAPSHOT.read_text()).get(origin, [])
    except (OSError, ValueError):
        return []


# ---------------------------------------------------------------------- #
# Estrazione
# ---------------------------------------------------------------------- #


def _client():
    import primp

    return primp.Client(impersonate="chrome", timeout=45)


def _linked_pages(client, wiki_page: str) -> list[str]:
    """Nomi delle pagine collegate nella tabella delle destinazioni."""
    html = client.get(WIKI_HTML.format(page=wiki_page)).text
    start = html.find("Airlines and destinations")
    if start == -1:
        raise RuntimeError(f"Tabella destinazioni non trovata in {wiki_page}")

    section = html[start : start + 150_000]
    pages: list[str] = []
    for row in re.findall(r"<tr>(.*?)</tr>", section, re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        if len(cells) < 2:  # riga di intestazione o malformata
            continue
        # La prima cella e' la compagnia, la seconda l'elenco delle destinazioni.
        for href in re.findall(r'<a[^>]*href="\./([^"#]+)"', cells[1]):
            name = html_lib.unescape(urllib.parse.unquote(href)).replace("_", " ")
            if name not in pages:
                pages.append(name)
    return pages


def _resolve_redirects(client, pages: list[str]) -> list[str]:
    """
    Porta ogni titolo alla sua forma canonica.

    Serve davvero: le tabelle linkano "London Heathrow Airport" mentre la pagina
    si chiama "Heathrow Airport", e Wikidata cerca per titolo esatto. Senza
    questo passaggio si perdono Londra, Parigi, Madrid, Roma e New York.
    """
    canonical: dict[str, str] = {}
    for i in range(0, len(pages), BATCH):
        params = urllib.parse.urlencode(
            {
                "action": "query",
                "redirects": "1",
                "format": "json",
                "titles": "|".join(pages[i : i + BATCH]),
            }
        )
        data = json.loads(client.get(f"{WIKI_API}?{params}").text).get("query", {})
        for entry in data.get("redirects", []):
            canonical[entry["from"]] = entry["to"]
    return list(dict.fromkeys(canonical.get(p, p) for p in pages))


def _iata_codes(client, pages: list[str]) -> list[str]:
    """Codici IATA ufficiali da Wikidata (proprieta' P238)."""
    codes: list[str] = []
    for i in range(0, len(pages), BATCH):
        params = urllib.parse.urlencode(
            {
                "action": "wbgetentities",
                "sites": "enwiki",
                "titles": "|".join(pages[i : i + BATCH]),
                "props": "claims",
                "format": "json",
            }
        )
        entities = json.loads(client.get(f"{WIKIDATA_API}?{params}").text).get("entities", {})
        for entity in entities.values():
            claim = entity.get("claims", {}).get("P238")
            if not claim:
                continue
            code = claim[0]["mainsnak"]["datavalue"]["value"]
            if isinstance(code, str) and len(code) == IATA_LENGTH and code not in codes:
                codes.append(code.upper())
    return codes


def fetch(origin: str) -> list[str]:
    """Scarica le destinazioni reali di un aeroporto. Solleva in caso di errore."""
    page = WIKI_PAGES.get(origin.upper())
    if not page:
        raise RuntimeError(f"Nessuna pagina Wikipedia configurata per {origin}")

    client = _client()
    pages = _resolve_redirects(client, _linked_pages(client, page))
    codes = [c for c in _iata_codes(client, pages) if c != origin.upper()]
    if not codes:
        raise RuntimeError(f"Nessuna destinazione estratta per {origin}")
    return sorted(codes)


# ---------------------------------------------------------------------- #
# API pubblica
# ---------------------------------------------------------------------- #


def destinations(origin: str, refresh: bool = False) -> list[str]:
    """
    Destinazioni reali di un aeroporto, con tre livelli di ripiego.

    Memoria del processo, poi cache su disco, poi rete, poi lo snapshot incluso
    nel repository: una ricerca non deve fallire perche' Wikipedia e' lenta.
    """
    origin = origin.upper()

    with _lock:
        if not refresh and origin in _memo:
            return _memo[origin]

    if not refresh:
        cached = _read_cache(origin)
        if cached:
            with _lock:
                _memo[origin] = cached
            return cached

    try:
        codes = fetch(origin)
        _write_cache(origin, codes)
    except Exception as exc:
        logger.warning("Rotte reali di %s non scaricabili (%s): uso lo snapshot", origin, exc)
        codes = _read_snapshot(origin)

    with _lock:
        _memo[origin] = codes
    return codes


def destinations_for(origins: list[str], known: set[str] | None = None) -> dict[str, list[str]]:
    """
    Mappa aeroporto di partenza -> sue destinazioni reali.

    Tenere le liste separate per origine, invece di unirle, evita di cercare
    rotte che non esistono: TSF non vola dove vola VCE, e il prodotto cartesiano
    sprecherebbe gran parte delle richieste.
    """
    result = {}
    for origin in origins:
        codes = destinations(origin)
        if known is not None:
            codes = [c for c in codes if c in known]
        result[origin] = codes
    return result
