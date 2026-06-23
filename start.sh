#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "Se creo .env desde .env.example. Agrega tus keys para conectar Spotify e IA."
  echo ""
fi

open_browser() {
  if [ "${PLAYLISTAI_NO_BROWSER:-}" = "1" ]; then
    return
  fi

  if command -v open >/dev/null 2>&1; then
    (
      for _ in $(seq 1 30); do
        if curl -fsS "http://127.0.0.1:5000" >/dev/null 2>&1; then
          open "http://127.0.0.1:5000"
          exit 0
        fi
        sleep 1
      done
    ) >/dev/null 2>&1 &
  elif command -v xdg-open >/dev/null 2>&1; then
    (
      for _ in $(seq 1 30); do
        if curl -fsS "http://127.0.0.1:5000" >/dev/null 2>&1; then
          xdg-open "http://127.0.0.1:5000"
          exit 0
        fi
        sleep 1
      done
    ) >/dev/null 2>&1 &
  fi
}

if command -v uv >/dev/null 2>&1; then
  echo ""
  echo "PlaylistAI corriendo en http://127.0.0.1:5000"
  echo ""
  open_browser
  uv run --with-requirements requirements.txt python app.py
  exit $?
fi

PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    PYTHON_BIN="python"
  fi
fi

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install -r requirements.txt -q

echo ""
echo "PlaylistAI corriendo en http://127.0.0.1:5000"
echo ""

open_browser
python app.py
