#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Este build debe ejecutarse en macOS; PyInstaller no hace compilación cruzada." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "No se encontró uv. Instálalo desde https://docs.astral.sh/uv/ y vuelve a ejecutar." >&2
  exit 1
fi

uv run \
  --with-requirements requirements.txt \
  --with-requirements requirements-build.txt \
  pyinstaller \
  --noconfirm \
  --clean \
  packaging/macos.spec

# Firma ad hoc suficiente para ejecutar el build local en el mismo Mac.
codesign --force --deep --sign - "dist/PlaylistAI.app"
codesign --verify --deep --strict --verbose=2 "dist/PlaylistAI.app"

rm -f "dist/PlaylistAI-macOS.zip"
ditto -c -k --keepParent "dist/PlaylistAI.app" "dist/PlaylistAI-macOS.zip"

echo ""
echo "Build listo:"
echo "  $PROJECT_ROOT/dist/PlaylistAI.app"
echo "  $PROJECT_ROOT/dist/PlaylistAI-macOS.zip"
echo ""
echo "La configuración se guardará en:"
echo "  ~/Library/Application Support/PlaylistAI/.env"
