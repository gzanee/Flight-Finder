"""
Handshake di consenso cookie di Google (obbligatorio per gli IP europei).

Perche' serve
-------------
Da un IP UE, ogni richiesta a google.com/travel/flights viene intercettata da
consent.google.com finche' il client non possiede il cookie ``SOCS``. Le
librerie di scraping (fli, fast-flights, swoop) non lo gestiscono: ricevono la
pagina di consenso, non trovano i dati e restituiscono zero risultati senza
sollevare eccezioni. E' la causa del classico "funziona per tutti tranne che a
me" nelle issue di quei progetti.

Questo modulo esegue una volta il flusso "Reject all" (rifiuta tutti i cookie
di profilazione: e' sufficiente a ottenere SOCS ed e' la scelta meno invasiva),
mette in cache il risultato su disco e inietta i cookie nei client HTTP usati
dai provider.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CONSENT_SAVE_URL = "https://consent.google.com/save"
PROBE_URL = "https://www.google.com/travel/flights?hl=en&gl={country}&curr={currency}"

# I cookie SOCS di Google durano ~13 mesi, ma li rinnoviamo con largo anticipo:
# se Google cambia il flusso ce ne accorgiamo presto invece che tra un anno.
CACHE_TTL_SECONDS = 7 * 24 * 3600

_RELEVANT_COOKIES = ("SOCS", "CONSENT", "__Secure-ENID")

_lock = threading.Lock()
_cookies: dict[str, str] | None = None
_installed = False


def _cache_path() -> Path:
    return Path(os.getenv("FLIGHT_FINDER_CACHE_DIR", ".cache")) / "google_consent.json"


def _read_cache() -> dict[str, str] | None:
    path = _cache_path()
    try:
        blob = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if time.time() - blob.get("ts", 0) > CACHE_TTL_SECONDS:
        return None
    cookies = blob.get("cookies")
    # Un cookie di consenso senza SOCS non sblocca nulla: trattalo come assente.
    return cookies if cookies and "SOCS" in cookies else None


def _write_cache(cookies: dict[str, str]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ts": time.time(), "cookies": cookies}))
    except OSError as exc:
        logger.warning("Impossibile scrivere la cache del consenso: %s", exc)


def _hidden_inputs(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r'<input[^>]*type="hidden"[^>]*>', block):
        tag = match.group(0)
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if name:
            fields[name.group(1)] = html_lib.unescape(value.group(1) if value else "")
    return fields


def _perform_handshake(country: str, currency: str) -> dict[str, str]:
    import primp

    client = primp.Client(impersonate="chrome", cookie_store=True, timeout=45)
    headers = {"Accept-Language": "en-US,en;q=0.9"}
    page = client.get(PROBE_URL.format(country=country, currency=currency), headers=headers).text

    if "consent.google.com" not in page[:2000]:
        logger.info("Nessun consenso richiesto da questo IP")
        return {}

    # La pagina contiene due form identici per struttura: "Reject all" porta
    # set_eom=true, "Accept all" porta set_eom=false. Isoliamo il primo.
    anchor = page.find('set_eom" value="true"')
    if anchor == -1:
        raise RuntimeError(
            "Form di consenso non riconosciuto: Google ha probabilmente cambiato la pagina"
        )
    form = page[page.rfind("<form", 0, anchor) : page.find("</form>", anchor)]

    client.post(
        CONSENT_SAVE_URL,
        data=_hidden_inputs(form),
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
    )

    jar = client.get_cookies("https://www.google.com") or {}
    cookies = {k: v for k, v in jar.items() if k in _RELEVANT_COOKIES}
    if "SOCS" not in cookies:
        raise RuntimeError("Handshake completato ma Google non ha rilasciato il cookie SOCS")
    return cookies


def get_cookies(
    country: str = "IT", currency: str = "EUR", refresh: bool = False
) -> dict[str, str]:
    """Restituisce i cookie di consenso, dalla cache o da un nuovo handshake."""
    global _cookies
    with _lock:
        if _cookies is not None and not refresh:
            return _cookies
        if not refresh:
            cached = _read_cache()
            if cached:
                _cookies = cached
                return _cookies
        try:
            _cookies = _perform_handshake(country, currency)
        except Exception as exc:
            logger.warning("Handshake di consenso fallito (%s); procedo senza cookie", exc)
            _cookies = {}
        if _cookies:
            _write_cache(_cookies)
        return _cookies


def cookie_header(**kwargs) -> str:
    return "; ".join(f"{k}={v}" for k, v in get_cookies(**kwargs).items())


def install(country: str = "IT", currency: str = "EUR", refresh: bool = False) -> dict[str, str]:
    """
    Inietta i cookie di consenso nei client HTTP dei provider.

    Va chiamata prima della prima richiesta: entrambe le librerie copiano gli
    header alla creazione della sessione, quindi iniettare dopo non avrebbe
    effetto sulle sessioni gia' aperte.
    """
    global _installed
    cookies = get_cookies(country=country, currency=currency, refresh=refresh)
    if not cookies:
        return cookies

    with _lock:
        _install_into_fli(cookies, reset_sessions=refresh or _installed)
        _install_into_primp(cookies)
        _installed = True
    return cookies


def _install_into_fli(cookies: dict[str, str], reset_sessions: bool) -> None:
    """fli usa curl_cffi e copia Client.DEFAULT_HEADERS in ogni sessione."""
    try:
        import fli.search.client as fli_client
    except ImportError:
        return

    header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    fli_client.Client.DEFAULT_HEADERS = {**fli_client.Client.DEFAULT_HEADERS, "cookie": header}
    if reset_sessions:
        # Le sessioni sono in un threading.local del singleton: per propagare un
        # cookie rinnovato bisogna buttare via il singleton, non solo gli header.
        fli_client.client = None


def _install_into_primp(cookies: dict[str, str]) -> None:
    """fast-flights usa primp.Client, che e' un tipo Rust: si avvolge con una factory."""
    try:
        import primp
    except ImportError:
        return

    if getattr(primp.Client, "_flight_finder_patched", False):
        return

    original = primp.Client

    def factory(*args, **kwargs):
        merged = dict(cookies)
        merged.update(kwargs.get("cookies") or {})
        kwargs["cookies"] = merged
        return original(*args, **kwargs)

    factory._flight_finder_patched = True  # type: ignore[attr-defined]
    factory._original = original  # type: ignore[attr-defined]
    primp.Client = factory  # type: ignore[assignment]

    # fast_flights fa `from primp import Client`: se il modulo e' gia' stato
    # importato tiene un riferimento al tipo originale e va aggiornato a mano.
    import sys

    fetcher = sys.modules.get("fast_flights.fetcher")
    if fetcher is not None:
        fetcher.Client = factory
