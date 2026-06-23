#!/bin/bash
cd "$(dirname "$0")" || exit 1

chmod +x ./start.sh 2>/dev/null || true
./start.sh
status=$?

if [ "$status" -ne 0 ]; then
  echo
  echo "PlaylistAI no pudo iniciar. Revisa el error anterior."
  read -r -p "Presiona Enter para cerrar..."
fi

exit "$status"
