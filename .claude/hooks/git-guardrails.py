#!/usr/bin/env python3
"""PreToolUse hook: bloquea comandos git destructivos.

Contrato: recibe JSON por stdin; exit 2 + stderr = bloquear; exit 0 = permitir.
"""
import json
import re
import sys

BLOCKED = [
    (r"git\s+push\b(?!.*--force-with-lease).*(--force|\s-f\b)",
     "git push --force reescribe el historial remoto. Alternativa segura: --force-with-lease"),
    (r"git\s+reset\s+--hard", "git reset --hard descarta cambios locales sin confirmacion"),
    (r"git\s+clean\s+-[a-z]*f", "git clean -f elimina archivos no trackeados permanentemente"),
    (r"git\s+checkout\s+--\s+\.", "git checkout -- . descarta todo el working directory"),
    (r"git\s+branch\s+-D\b", "git branch -D elimina una rama con cambios sin mergear"),
    (r"git\s+rebase\s+(-i|--interactive)", "git rebase -i reescribe historial (y el harness no soporta -i)"),
    (r"git\s+stash\s+(drop|clear)", "git stash drop/clear elimina el stash permanentemente"),
    (r"git\s+(filter-repo|filter-branch)", "reescribe el historial completo del repositorio"),
]


def main() -> int:
    # Leer como bytes y decodificar con utf-8-sig: algunos pipelines (PowerShell en
    # Windows) anteponen un BOM que rompe json.load y desactivaria el guardrail.
    raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace").strip()
    if not raw:
        return 0  # sin payload no hay decision; sigue el flujo normal de permisos
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # No fallar en silencio: si no se puede leer el payload, avisar.
        print(
            "git-guardrails: no se pudo parsear el payload del hook; "
            "los comandos git NO estan siendo filtrados.",
            file=sys.stderr,
        )
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = str(payload.get("tool_input", {}).get("command", ""))

    for pattern, reason in BLOCKED:
        if re.search(pattern, command, re.IGNORECASE):
            print(
                f"BLOQUEADO por git-guardrails: {reason}\n"
                f"Comando: {command}\n"
                f"Si es intencional, ejecutalo tu mismo en la terminal.",
                file=sys.stderr,
            )
            return 2  # exit 2 = bloquear la tool call

    return 0


if __name__ == "__main__":
    sys.exit(main())
