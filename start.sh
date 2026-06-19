#!/usr/bin/env bash
set -euo pipefail

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

python -m pip install -r requirements.txt -q

echo ""
echo "PlaylistAI corriendo en http://127.0.0.1:5000"
echo ""

python app.py
