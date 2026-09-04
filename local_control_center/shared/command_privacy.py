"""Evidencia de comandos CLI sin prompts y binding exacto para grants de un solo uso.

El hash vincula el comando y argv originales; el resumen nunca se usa como autorización.
Los comandos no CLI conservan el contrato previo. No interpreta ni ejecuta texto shell.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CLI_EXECUTABLES = {"codex", "claude", "openhands", "swe-agent", "sweagent"}
BINDING_KEY = "executionBindingSha256"


def is_cli_argv(argv) -> bool:
    """Reconoce exclusivamente el ejecutable estructurado, no palabras en un prompt."""
    return bool(
        isinstance(argv, list)
        and argv
        and isinstance(argv[0], str)
        and Path(argv[0]).stem.lower() in CLI_EXECUTABLES
    )


def execution_binding(command: str, argv: list[str]) -> str:
    """Hash inequívoco de ambos campos crudos; cambiar prompt, flags o orden invalida el grant."""
    return hashlib.sha256(
        json.dumps([command, argv], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def private_command_evidence(command: str, argv: list[str], payload: dict) -> tuple[str, list[str], dict]:
    """Separa el binding de autorización del texto seguro para lectura y auditoría."""
    from local_control_center.process_supervision.service import safe_command_summary

    clean = dict(payload)
    clean.pop(BINDING_KEY, None)
    if not is_cli_argv(argv):
        return command, argv, clean
    summary = safe_command_summary(argv)
    clean.update({BINDING_KEY: execution_binding(command, argv), "commandArgv": summary})
    if "argv" in clean:
        clean["argv"] = summary
    if "command" in clean:
        clean["command"] = " ".join(summary)
    return " ".join(summary), summary, clean
