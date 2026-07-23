"""Rutas de ejecución compartidas por el código fuente y los binarios."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


APP_NAME = "PlaylistAI"


def resource_path(name: str) -> Path:
    """Devuelve un recurso incluido por PyInstaller o ubicado en el repositorio."""
    return Path(__file__).resolve().parent / name


def config_directory() -> Path:
    """Directorio escribible que contiene .env y los logs del lanzador."""
    override = os.environ.get("PLAYLISTAI_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            # El ejecutable de Windows se mantiene portable.
            return Path(sys.executable).resolve().parent
        if sys.platform == "darwin":
            # Nunca se escriben credenciales dentro del bundle .app.
            return Path.home() / "Library" / "Application Support" / APP_NAME

    return Path(__file__).resolve().parent


def env_file_path() -> Path:
    override = os.environ.get("PLAYLISTAI_ENV_FILE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return config_directory() / ".env"


def ensure_env_file() -> bool:
    """Crea .env desde la plantilla. Devuelve True si fue creado."""
    destination = env_file_path()
    if destination.exists():
        return False

    template = resource_path(".env.example")
    if not template.exists():
        return False

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, destination)
    return True


def log_file_path() -> Path:
    directory = config_directory()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "playlistai.log"
