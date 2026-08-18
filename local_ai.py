"""Integración no interactiva con Claude Code y Codex usando sesiones locales."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


# Antes cubría una unica llamada pidiendo hasta ~55 candidatos de una. Desde la
# resolucion incremental por rondas (ver app.py/_round_batch_size) cada llamada
# pide como maximo ~18 candidatos, así que un timeout menor sigue siendo
# holgado y permite mas rondas dentro del mismo PLAYLISTAI_JOB_TIMEOUT_SECONDS
# (ver relacion documentada en CONTEXT.md).
LOCAL_AI_TIMEOUT_SECONDS = 100
AUTH_CHECK_TIMEOUT_SECONDS = 10


class LocalAIError(RuntimeError):
    """Error controlado al invocar una CLI de suscripción."""


def _local_cli_path() -> str:
    """Amplía PATH para apps GUI, que en macOS no heredan el shell del usuario."""
    try:
        home = Path.home()
    except RuntimeError:
        home = Path.cwd()
    candidates = [
        home / ".local" / "bin",
        home / ".npm-global" / "bin",
        home / ".asdf" / "shims",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    app_data = os.environ.get("APPDATA")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if app_data:
        candidates.append(Path(app_data) / "npm")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "nodejs")

    # npm instalado mediante nvm/fnm mantiene cada versión de Node en su propio bin.
    candidates.extend(sorted((home / ".nvm" / "versions" / "node").glob("*/bin"), reverse=True))
    candidates.extend(
        sorted(
            (home / "Library" / "Application Support" / "fnm" / "node-versions").glob(
                "*/installation/bin"
            ),
            reverse=True,
        )
    )

    existing_path = os.environ.get("PATH", "").split(os.pathsep)
    ordered = [str(path) for path in candidates if path.is_dir()] + existing_path
    return os.pathsep.join(dict.fromkeys(item for item in ordered if item))


def _command_path(name: str) -> str | None:
    return shutil.which(name, path=_local_cli_path())


def _subscription_env() -> dict[str, str]:
    """Evita que una CLI seleccionada como suscripción consuma una API key."""
    blocked = {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() not in blocked}
    env["NO_COLOR"] = "1"
    env["CI"] = "1"
    env["PATH"] = _local_cli_path()
    return env


def _run_process(
    command: list[str],
    *,
    input_text: str | None = None,
    cwd: str | None = None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        return subprocess.run(
            command,
            input=input_text,
            cwd=cwd,
            env=_subscription_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        raise LocalAIError(
            f"La CLI local superó el límite de {timeout} segundos."
        ) from exc
    except OSError as exc:
        raise LocalAIError("No se pudo iniciar la CLI local seleccionada.") from exc


def _failure_message(provider_name: str, result: subprocess.CompletedProcess[str]) -> str:
    lines = [line.strip() for line in (result.stderr or "").splitlines() if line.strip()]
    detail = lines[-1][:240] if lines else f"código de salida {result.returncode}"
    return f"{provider_name} no pudo completar la consulta: {detail}"


def cli_status(provider: str) -> dict[str, object]:
    """Devuelve instalación/autenticación sin exponer datos de la cuenta."""
    if provider == "claude_code":
        path = _command_path("claude")
        if not path:
            return {
                "installed": False,
                "authenticated": False,
                "available": False,
                "detail": "Claude Code no está instalado",
            }
        result = _run_process(
            [path, "auth", "status"],
            timeout=AUTH_CHECK_TIMEOUT_SECONDS,
        )
        authenticated = False
        if result.returncode == 0:
            try:
                authenticated = bool(json.loads(result.stdout).get("loggedIn"))
            except (json.JSONDecodeError, AttributeError):
                authenticated = "logged" in result.stdout.lower()
        return {
            "installed": True,
            "authenticated": authenticated,
            "available": authenticated,
            "detail": "Sesión de Claude Code activa" if authenticated else "Inicia sesión con: claude",
        }

    if provider == "codex":
        path = _command_path("codex")
        if not path:
            return {
                "installed": False,
                "authenticated": False,
                "available": False,
                "detail": "Codex CLI no está instalado",
            }
        result = _run_process(
            [path, "login", "status"],
            timeout=AUTH_CHECK_TIMEOUT_SECONDS,
        )
        status_text = f"{result.stdout}\n{result.stderr}".lower()
        authenticated = result.returncode == 0 and "logged in" in status_text
        return {
            "installed": True,
            "authenticated": authenticated,
            "available": authenticated,
            "detail": "Sesión de Codex activa" if authenticated else "Inicia sesión con: codex login",
        }

    return {
        "installed": False,
        "authenticated": False,
        "available": False,
        "detail": "CLI local no soportada",
    }


def _claude_text(stdout: str) -> str:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise LocalAIError("Claude Code devolvió una respuesta que no se pudo interpretar.") from exc

    structured = payload.get("structured_output")
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    result = payload.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    raise LocalAIError("Claude Code no devolvió texto utilizable.")


def _call_claude_code(
    prompt: str,
    model: str | None,
    output_schema: dict | None,
) -> str:
    path = _command_path("claude")
    if not path:
        raise LocalAIError("Claude Code no está instalado o no está disponible en PATH.")

    command = [
        path,
        "-p",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--safe-mode",
        "--disable-slash-commands",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "",
    ]
    if model and model != "default":
        command.extend(["--model", model])
    if output_schema:
        command.extend(["--json-schema", json.dumps(output_schema, ensure_ascii=False)])

    result = _run_process(
        command,
        input_text=prompt,
        timeout=LOCAL_AI_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise LocalAIError(_failure_message("Claude Code", result))
    return _claude_text(result.stdout)


def _call_codex(
    prompt: str,
    model: str | None,
    output_schema: dict | None,
) -> str:
    path = _command_path("codex")
    if not path:
        raise LocalAIError("Codex CLI no está instalado o no está disponible en PATH.")

    with tempfile.TemporaryDirectory(prefix="playlistai-codex-") as temp_dir:
        output_path = Path(temp_dir) / "last-message.txt"
        command = [
            path,
            "exec",
            "--ephemeral",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--cd",
            temp_dir,
            "--output-last-message",
            str(output_path),
            "--color",
            "never",
        ]
        if model and model != "default":
            command.extend(["--model", model])
        if output_schema:
            schema_path = Path(temp_dir) / "schema.json"
            schema_path.write_text(
                json.dumps(output_schema, ensure_ascii=False),
                encoding="utf-8",
            )
            command.extend(["--output-schema", str(schema_path)])
        command.append("-")

        result = _run_process(
            command,
            input_text=prompt,
            cwd=temp_dir,
            timeout=LOCAL_AI_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise LocalAIError(_failure_message("Codex", result))
        if not output_path.exists():
            raise LocalAIError("Codex no generó una respuesta final.")
        text = output_path.read_text(encoding="utf-8").strip()
        if not text:
            raise LocalAIError("Codex devolvió una respuesta vacía.")
        return text


def call_local_ai(
    prompt: str,
    *,
    provider: str,
    model: str | None = None,
    output_schema: dict | None = None,
) -> str:
    if provider == "claude_code":
        return _call_claude_code(prompt, model, output_schema)
    if provider == "codex":
        return _call_codex(prompt, model, output_schema)
    raise LocalAIError(f"Proveedor de suscripción no soportado: {provider}")
