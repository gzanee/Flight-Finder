#!/bin/bash
# Doppio clic dal Finder: avvia il portale e lo apre nel browser.
# Chiudi questa finestra del Terminale (o premi Ctrl+C) per fermarlo.

cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" || exit 1

PORT="${PORT:-$(grep -E '^PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2)}"
PORT="${PORT:-5001}"
URL="http://localhost:${PORT}/"

# Già in esecuzione? Apri solo il browser.
if curl -fs "${URL}api/health" >/dev/null 2>&1; then
  echo "Il portale è già attivo su ${URL}"
  open "$URL"
  exit 0
fi

# Primo avvio: crea l'ambiente e installa le dipendenze.
if [ ! -x .venv/bin/python ]; then
  echo "Preparo l'ambiente Python (solo la prima volta)..."
  python3 -m venv .venv || { echo "python3 non trovato"; read -r; exit 1; }
  .venv/bin/pip install -q -r requirements.txt || { echo "Installazione fallita"; read -r; exit 1; }
fi

# Apri il browser appena il server risponde.
(
  for _ in $(seq 1 60); do
    if curl -fs "${URL}api/health" >/dev/null 2>&1; then open "$URL"; exit 0; fi
    sleep 0.5
  done
) &

echo "Avvio Flight Finder su ${URL} — chiudi questa finestra per fermarlo."
PORT="$PORT" exec .venv/bin/python app.py
