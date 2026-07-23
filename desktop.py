"""Lanzador de escritorio para los builds de Windows y macOS."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from runtime_paths import ensure_env_file, env_file_path, log_file_path


HOST = "127.0.0.1"
PORT = 5000
URL = f"http://{HOST}:{PORT}"
AUTO_OPEN = os.environ.get("PLAYLISTAI_NO_BROWSER") != "1"


created_env = ensure_env_file()
logging.basicConfig(
    filename=log_file_path(),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

def open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-t", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        logging.exception("No se pudo abrir el archivo de configuración: %s", path)


def server_is_running() -> bool:
    try:
        with urllib.request.urlopen(URL, timeout=1) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def open_browser_when_ready() -> None:
    for _ in range(60):
        if server_is_running():
            if AUTO_OPEN:
                webbrowser.open(URL)
            return
        time.sleep(0.25)
    logging.error("El servidor no estuvo listo a tiempo en %s", URL)


def main() -> int:
    if created_env:
        logging.info("Se creó la configuración inicial en %s", env_file_path())
        open_path(env_file_path())
        return 0

    if server_is_running():
        if AUTO_OPEN:
            webbrowser.open(URL)
        return 0

    # Importar después de crear .env garantiza que app.py cargue la configuración.
    from app import app
    from werkzeug.serving import make_server

    try:
        server = make_server(HOST, PORT, app, threaded=True)
    except OSError:
        logging.exception("No se pudo iniciar PlaylistAI en %s", URL)
        return 1

    threading.Thread(
        target=open_browser_when_ready,
        name="playlistai-browser",
        daemon=True,
    ).start()

    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
