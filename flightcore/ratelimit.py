"""
Limitatore di frequenza globale verso Google.

Serve perche' una scansione "ovunque" e' fatta di centinaia di richieste: senza
freno il pool di thread ne spara una dozzina al secondo e Google risponde 429
con un captcha, bloccando l'intero portale per svariati minuti. Rallentare di
qualche secondo una scansione e' molto meglio che restare fuori per un'ora.

Il limite e' condiviso da tutti i thread e da tutti i provider, perche' anche
Google ci vede come un unico client.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Token bucket semplice: al massimo `rate` richieste al secondo, con raffiche."""

    def __init__(self, rate: float = 5.0, burst: int = 5):
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Blocca finche' non e' il momento di fare la richiesta."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.burst, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                attesa = (1 - self._tokens) / self.rate
            # L'attesa avviene fuori dal lock, altrimenti gli altri thread
            # resterebbero fermi senza motivo.
            time.sleep(attesa)


#: Limite condiviso. 5 richieste al secondo si sono rivelate sostenibili nei
#: test; a ~13 al secondo Google risponde 429 nel giro di poche centinaia di
#: richieste.
limiter = RateLimiter(rate=5.0, burst=5)
