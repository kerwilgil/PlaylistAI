"""Lanzador de escritorio para los builds de Windows y macOS."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox

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

# La configuración debe existir antes de importar app.py, que carga .env al importar.
from app import app  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402


def open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-t", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        messagebox.showerror(
            "PlaylistAI",
            f"No se pudo abrir el archivo automáticamente:\n{path}",
        )


def main() -> int:
    root = tk.Tk()
    root.title("PlaylistAI")
    root.geometry("520x250")
    root.resizable(False, False)

    try:
        server = make_server(HOST, PORT, app, threaded=True)
    except OSError as error:
        messagebox.showerror(
            "PlaylistAI",
            f"No se pudo iniciar en {URL}.\n\n"
            "Comprueba que no haya otra copia abierta y vuelve a intentarlo.\n\n"
            f"Detalle: {error}",
        )
        root.destroy()
        return 1

    server_thread = threading.Thread(
        target=server.serve_forever,
        name="playlistai-server",
        daemon=True,
    )
    server_thread.start()

    title = tk.Label(root, text="PlaylistAI", font=("Arial", 22, "bold"))
    title.pack(pady=(24, 8))

    status_text = (
        "Se creó el archivo de configuración.\n"
        "Añade tus credenciales, guarda el archivo y reinicia PlaylistAI."
        if created_env
        else f"PlaylistAI está ejecutándose en {URL}"
    )
    status = tk.Label(root, text=status_text, justify="center", wraplength=470)
    status.pack(pady=(0, 18))

    buttons = tk.Frame(root)
    buttons.pack()

    tk.Button(
        buttons,
        text="Abrir PlaylistAI",
        width=18,
        command=lambda: webbrowser.open(URL),
    ).grid(row=0, column=0, padx=5)
    tk.Button(
        buttons,
        text="Editar configuración",
        width=18,
        command=lambda: open_path(env_file_path()),
    ).grid(row=0, column=1, padx=5)

    def shutdown() -> None:
        server.shutdown()
        root.destroy()

    tk.Button(root, text="Cerrar", width=12, command=shutdown).pack(pady=16)
    root.protocol("WM_DELETE_WINDOW", shutdown)

    if AUTO_OPEN:
        if created_env:
            root.after(500, lambda: open_path(env_file_path()))
        else:
            root.after(500, lambda: webbrowser.open(URL))

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
