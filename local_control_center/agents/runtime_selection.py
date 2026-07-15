"""Helpers canónicos de selección/estado de runtime compartidos por agentes y workflows.

Único hogar de los predicados y shapes que hasta ahora vivían copiados por agente
(``is_ollama_runtime`` x4 byte-idénticas, ``runtime_provider_family`` x7 equivalentes,
``runtime_unavailable_result`` x4, ``RUNTIME_UNAVAILABLE_STATUS`` x4). Los helpers que
DIVERGEN por agente (``_runtime_mode``, ``_execution_result_from_tool_call``) se quedan
en cada agente a propósito: su lógica es específica y unificarla cambiaría comportamiento.

@author Rodrigo Mason
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

RUNTIME_UNAVAILABLE_STATUS = "runtime_unavailable"

_PYTHON_LAUNCHER_NAMES = {"python.exe", "python3.exe", "py.exe"}


def is_ollama_runtime(runtime: dict[str, Any]) -> bool:
    """Indica si el runtime es Ollama, sea por id o por familia de proveedor."""
    return str(runtime.get("id") or "") == "ollama" or runtime.get("providerFamily") == "ollama"


def runtime_provider_family(runtime: dict[str, Any]) -> str:
    """Devuelve la familia de proveedor del runtime, normalizando Ollama por id o familia."""
    return "ollama" if is_ollama_runtime(runtime) else str(runtime.get("providerFamily") or "")


def runtime_unavailable_result(reason: str) -> dict[str, Any]:
    """Shape estándar de resultado cuando no hay runtime ejecutable (byte-idéntico x3 agentes)."""
    return {
        "status": RUNTIME_UNAVAILABLE_STATUS,
        "reason": reason,
        "execution": "not_executed",
    }


def display_command(argv: list[str]) -> str:
    """Representación imprimible de un argv: normaliza el launcher de Python y quotea con list2cmdline."""
    if not argv:
        return ""
    executable = Path(argv[0]).name or str(argv[0])
    if executable.lower() in _PYTHON_LAUNCHER_NAMES:
        executable = "python"
    return subprocess.list2cmdline([executable, *[str(item) for item in argv[1:]]])
