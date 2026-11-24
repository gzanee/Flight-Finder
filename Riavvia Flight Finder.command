#!/bin/bash
# Doppio clic dal Finder: ferma il portale se è acceso e lo riavvia.

cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" || exit 1

PORT="${PORT:-$(grep -E '^PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2)}"
PORT="${PORT:-5001}"

# Solo il portale di questa cartella: un altro programma sulla porta resta acceso.
for pid in $(lsof -ti "tcp:${PORT}" -sTCP:LISTEN 2>/dev/null); do
  where="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  if [ "$where" = "$PWD" ]; then
    echo "Fermo il portale (processo $pid)..."
    kill "$pid"
  else
    echo "La porta ${PORT} è usata da un altro programma (processo $pid): non lo tocco."
    read -r; exit 1
  fi
done
for _ in $(seq 1 20); do
  lsof -ti "tcp:${PORT}" -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 0.25
done

exec "./Avvia Flight Finder.command"
