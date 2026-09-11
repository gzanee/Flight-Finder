"""
Circuit breaker per provider.

L'endpoint interno di Google alterna periodi in cui risponde e periodi in cui
rifiuta tutto. Senza questo, ogni ricerca pagherebbe comunque il round-trip
verso un provider che sappiamo gia' essere muto, rallentando tutto di qualche
secondo. Dopo alcuni fallimenti consecutivi lo mettiamo in pausa, e ogni tanto
gli diamo una possibilita' di rientrare.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _State:
    failures: int = 0
    empties: int = 0
    opened_at: float = 0.0
    last_error: str | None = None


@dataclass
class CircuitBreaker:
    #: Errori veri e propri (rete, risposta rifiutata): pochi bastano.
    threshold: int = 3
    #: Risposte vuote: possono voler dire semplicemente "su questa rotta non
    #: vola nessuno", quindi serve molta piu' evidenza prima di sospettare il
    #: provider. Senza questa distinzione una ricerca su rotte inesistenti
    #: metterebbe in pausa un provider perfettamente funzionante.
    empty_threshold: int = 8
    cooldown: int = 300  # 5 minuti, poi si riprova
    _states: dict[str, _State] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _state(self, name: str) -> _State:
        return self._states.setdefault(name, _State())

    def _tripped(self, state: _State) -> bool:
        return state.failures >= self.threshold or state.empties >= self.empty_threshold

    def is_open(self, name: str) -> bool:
        """True se il provider va saltato in questo momento."""
        with self._lock:
            state = self._state(name)
            if not self._tripped(state):
                return False
            if time.time() - state.opened_at >= self.cooldown:
                # Scaduta la pausa: lo lasciamo riprovare una volta. Se fallisce
                # ancora, record_* lo rimette in pausa immediatamente.
                state.failures = min(state.failures, self.threshold - 1)
                state.empties = min(state.empties, self.empty_threshold - 1)
                return False
            return True

    def record_success(self, name: str) -> None:
        with self._lock:
            self._states[name] = _State()

    def record_failure(self, name: str, error: str | None = None) -> None:
        with self._lock:
            state = self._state(name)
            state.failures += 1
            state.last_error = error
            if self._tripped(state):
                state.opened_at = time.time()

    def record_empty(self, name: str) -> None:
        """Risposta valida ma senza offerte: sospetta solo se si ripete a lungo."""
        with self._lock:
            state = self._state(name)
            state.empties += 1
            if self._tripped(state):
                state.opened_at = time.time()
                state.last_error = "nessun risultato, ripetutamente"

    def status(self) -> dict[str, dict]:
        with self._lock:
            now = time.time()
            return {
                name: {
                    "failures": state.failures,
                    "empties": state.empties,
                    "paused": self._tripped(state) and now - state.opened_at < self.cooldown,
                    "retry_in_s": max(0, int(self.cooldown - (now - state.opened_at)))
                    if self._tripped(state)
                    else 0,
                    "last_error": state.last_error,
                }
                for name, state in self._states.items()
            }

    def reset(self) -> None:
        with self._lock:
            self._states.clear()
