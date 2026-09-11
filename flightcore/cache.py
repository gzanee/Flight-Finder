"""Cache in memoria con scadenza, condivisa da tutte le ricerche."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

_DEFAULT_TTL = 900  # 15 minuti: i prezzi dei voli non cambiano al secondo


class TTLCache:
    def __init__(self, ttl: int = _DEFAULT_TTL, max_entries: int = 512):
        self.ttl = ttl
        self.max_entries = max_entries
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            stamped, value = entry
            if time.time() - stamped > self.ttl:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key, value):
        with self._lock:
            if len(self._data) >= self.max_entries:
                # Sfratta la voce piu' vecchia: senza tetto la cache cresce
                # senza limite su un server che resta acceso per giorni.
                oldest = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest, None)
            self._data[key] = (time.time(), value)

    def get_or_set(self, key, factory: Callable[[], Any]):
        hit = self.get(key)
        if hit is not None:
            return hit
        value = factory()
        self.set(key, value)
        return value

    def clear(self):
        with self._lock:
            self._data.clear()


search_cache = TTLCache()
