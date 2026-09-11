"""Interfaccia comune dei provider e utilita' condivise."""

from __future__ import annotations

from ..models import DatePrice, Offer, SearchQuery


class ProviderError(RuntimeError):
    """Il provider non e' riuscito a rispondere (rete, blocco, parsing)."""


class Provider:
    name = "base"
    #: Un provider che non sa fare i calendari prezzi lascia questo a False.
    supports_dates = False

    def search(self, query: SearchQuery) -> list[Offer]:
        raise NotImplementedError

    def cheapest_dates(
        self, origin: str, destination: str, from_date: str, to_date: str, **kwargs
    ) -> list[DatePrice]:
        raise NotImplementedError

    def available(self) -> bool:
        return True
