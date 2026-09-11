"""
Motore di ricerca: orchestra i provider.

Tre responsabilita':

1. **Catena di fallback.** I provider vengono provati in ordine finche' uno
   risponde. fli e' primario perche' restituisce l'elenco completo dei voli;
   fast-flights subentra quando l'endpoint interno di Google rifiuta le
   richieste, cosa che accade a intermittenza.
2. **Ricerca multi-origine.** VCE e TSF sono due aeroporti della stessa citta':
   cercare su entrambi e unire i risultati e' il comportamento che serve qui.
3. **Ricerca "ovunque".** Nessuna delle librerie espone un endpoint funzionante
   per l'esplorazione, quindi la costruiamo scandendo in parallelo una rosa di
   destinazioni e tenendo la migliore offerta per ciascuna.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace

from .breaker import CircuitBreaker
from .cache import search_cache
from .models import DatePrice, Offer, SearchQuery
from .providers.base import ProviderError

logger = logging.getLogger(__name__)

# Google inizia a rifiutare le richieste sotto raffica: meglio poche in
# parallelo e risultati completi che molte e un blocco a meta' scansione.
MAX_WORKERS = 4


class Engine:
    def __init__(self, providers=None, country: str = "IT", language: str = "it"):
        if providers is None:
            providers = _default_providers(country, language)
        self.providers = [p for p in providers if p.available()]
        self.breaker = CircuitBreaker()
        if not self.providers:
            raise RuntimeError(
                "Nessun provider disponibile: installa le dipendenze con "
                "pip install -r requirements.txt"
            )

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self.providers]

    # ------------------------------------------------------------------ #
    # Ricerca su una rotta
    # ------------------------------------------------------------------ #

    def search(self, query: SearchQuery, use_cache: bool = True) -> tuple[list[Offer], str | None]:
        """Restituisce (offerte, nome del provider che ha risposto)."""
        key = ("search", _query_key(query))
        if use_cache:
            cached = search_cache.get(key)
            if cached is not None:
                return cached

        errors = []
        for provider in self.providers:
            if self.breaker.is_open(provider.name):
                errors.append(f"{provider.name}: in pausa dopo ripetuti fallimenti")
                continue

            try:
                offers = provider.search(query)
            except ProviderError as exc:
                logger.warning("%s ha fallito: %s", provider.name, exc)
                self.breaker.record_failure(provider.name, str(exc))
                errors.append(f"{provider.name}: {exc}")
                continue

            if offers:
                self.breaker.record_success(provider.name)
                offers = _apply_client_filters(offers, query)
                result = (offers, provider.name)
                search_cache.set(key, result)
                return result

            # Una risposta vuota non e' un errore di per se' (puo' davvero non
            # esserci nessun volo): pesa meno di un guasto nel circuit breaker.
            self.breaker.record_empty(provider.name)
            logger.info("%s non ha restituito offerte, passo al successivo", provider.name)
            errors.append(f"{provider.name}: nessun risultato")

        logger.warning("Nessun provider ha prodotto risultati: %s", "; ".join(errors))
        return [], None

    def search_many_origins(
        self, origins: list[str], query: SearchQuery
    ) -> tuple[list[Offer], list[str]]:
        """Cerca la stessa rotta da piu' aeroporti di partenza e unisce i risultati."""
        jobs = [replace(query, origin=origin) for origin in origins]
        offers: list[Offer] = []
        used: list[str] = []

        for result, provider in self._parallel(self.search, jobs):
            offers.extend(result)
            if provider and provider not in used:
                used.append(provider)

        offers.sort(key=_sort_key(query.sort_by))
        return offers, used

    # ------------------------------------------------------------------ #
    # Ricerca "ovunque"
    # ------------------------------------------------------------------ #

    def explore(
        self, destinations_by_origin: dict[str, list[str]], query: SearchQuery
    ) -> tuple[list[Offer], list[str]]:
        """
        Scandisce le destinazioni e tiene la migliore offerta per ognuna.

        Le destinazioni sono indicate **per aeroporto di partenza**, non in un
        unico elenco: TSF non serve le stesse citta' di VCE, e incrociare tutte
        le origini con tutte le mete sprecherebbe gran parte delle richieste su
        rotte che non esistono.

        Una destinazione per riga di risultato: mostrare sei voli per Barcellona
        e nessuno per le altre citta' non aiuta chi sta ancora decidendo dove
        andare.
        """
        origins = {o.upper() for o in destinations_by_origin}
        per_origin = {
            origin: [
                replace(query, origin=origin, destination=destination)
                for destination in destinations
                if destination.upper() not in origins
            ]
            for origin, destinations in destinations_by_origin.items()
        }
        jobs = _interleave(per_origin.values())

        best: dict[str, Offer] = {}
        used: list[str] = []
        for offers, provider in self._parallel(self.search, jobs):
            if provider and provider not in used:
                used.append(provider)
            for offer in offers:
                current = best.get(offer.destination)
                if current is None or offer.price < current.price:
                    best[offer.destination] = offer

        results = sorted(best.values(), key=lambda o: o.price)
        return results, used

    # ------------------------------------------------------------------ #
    # Calendario prezzi
    # ------------------------------------------------------------------ #

    def cheapest_dates(self, origin: str, destination: str, from_date: str, to_date: str, **kwargs):
        key = ("dates", origin, destination, from_date, to_date, tuple(sorted(kwargs.items())))
        cached = search_cache.get(key)
        if cached is not None:
            return cached

        for provider in self.providers:
            if not provider.supports_dates or self.breaker.is_open(provider.name):
                continue
            try:
                dates: list[DatePrice] = provider.cheapest_dates(
                    origin, destination, from_date, to_date, **kwargs
                )
            except ProviderError as exc:
                logger.warning("%s: calendario non disponibile (%s)", provider.name, exc)
                self.breaker.record_failure(provider.name, str(exc))
                continue
            if dates:
                result = (dates, provider.name)
                search_cache.set(key, result)
                return result
        return [], None

    # ------------------------------------------------------------------ #

    def diagnosis(self) -> str | None:
        """
        Spiegazione leggibile quando una ricerca torna vuota per colpa nostra.

        Senza questo, chi cerca durante un blocco di Google legge "nessun volo
        trovato" e conclude che la rotta non esiste: il caso in cui e' piu'
        importante dire la verita' e' proprio quello in cui l'app non ha dati.
        """
        stato = self.breaker.status()
        in_pausa = {n: s for n, s in stato.items() if s["paused"]}
        if len(in_pausa) < len(self.providers):
            return None

        limitati = [s["last_error"] for s in in_pausa.values() if s["last_error"]]
        attesa = max((s["retry_in_s"] for s in in_pausa.values()), default=0)
        for messaggio in limitati:
            if "limitato le richieste" in messaggio:
                return (
                    "Google ha temporaneamente limitato le richieste da questo "
                    f"indirizzo. Riprova fra circa {max(1, attesa // 60)} minuti."
                )
        return (
            "Nessuna fonte disponibile in questo momento "
            f"(nuovo tentativo fra circa {max(1, attesa // 60)} minuti)."
        )

    def _parallel(self, fn, jobs):
        if not jobs:
            return
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(jobs))) as pool:
            futures = {pool.submit(fn, job): job for job in jobs}
            for future in as_completed(futures):
                try:
                    yield future.result()
                except Exception as exc:  # una rotta che fallisce non ferma le altre
                    logger.warning("Ricerca fallita per %s: %s", futures[future].destination, exc)


def _interleave(groups) -> list:
    """
    Alterna le ricerche fra gli aeroporti di partenza invece di esaurirne uno
    alla volta.

    Su una scansione lunga Google inizia a strozzare le richieste a meta' strada:
    procedendo in blocchi, l'ultimo aeroporto dell'elenco perdeva quasi tutti i
    risultati (nei test TSF passava da 17 mete trovate a 4, mentre VCE restava
    stabile). Alternando, un eventuale throttling colpisce tutti allo stesso modo
    e il risultato parziale resta rappresentativo.
    """
    from itertools import chain, zip_longest

    sentinel = object()
    mixed = chain.from_iterable(zip_longest(*groups, fillvalue=sentinel))
    return [job for job in mixed if job is not sentinel]


def _default_providers(country: str, language: str):
    from .providers.fastflights_provider import FastFlightsProvider
    from .providers.fli_provider import FliProvider

    return [FliProvider(country=country, language=language), FastFlightsProvider(language=language)]


def _apply_client_filters(offers: list[Offer], query: SearchQuery) -> list[Offer]:
    """
    Riapplica i filtri lato nostro.

    Google li accetta ma non sempre li rispetta alla lettera, e fast-flights non
    supporta tutti quelli che fli accetta: ricontrollare qui evita che l'utente
    veda risultati che ha appena escluso.
    """
    result = offers

    if query.max_price is not None:
        result = [o for o in result if o.price <= query.max_price]

    if query.max_stops is not None:
        result = [o for o in result if o.stops <= query.max_stops]

    def hour_ok(offer: Offer) -> bool:
        hour = offer.legs[0].departure.hour
        if query.earliest_departure is not None and hour < query.earliest_departure:
            return False
        if query.latest_departure is not None and hour > query.latest_departure:
            return False
        if len(offer.legs) > 1:
            back = offer.legs[1].departure.hour
            earliest_back = query.return_earliest_departure
            if earliest_back is not None and back < earliest_back:
                return False
            if query.return_latest_departure is not None and back > query.return_latest_departure:
                return False
        return True

    result = [o for o in result if o.legs and hour_ok(o)]
    result.sort(key=_sort_key(query.sort_by))
    return result


def _sort_key(sort_by: str):
    if sort_by == "duration":
        return lambda o: (o.duration_min, o.price)
    if sort_by == "departure":
        return lambda o: (o.legs[0].departure, o.price)
    if sort_by == "emissions":
        return lambda o: (o.co2_kg if o.co2_kg is not None else 1e9, o.price)
    return lambda o: (o.price, o.duration_min)


def _query_key(query: SearchQuery):
    from dataclasses import asdict

    items = ((k, tuple(v) if isinstance(v, list) else v) for k, v in asdict(query).items())
    return tuple(sorted(items))
