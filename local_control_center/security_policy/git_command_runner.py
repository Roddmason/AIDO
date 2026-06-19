"""Ejecuta el CLI de git sin shell y nunca propaga su ausencia como excepcion.

Envuelve ``subprocess.run`` para los pocos comandos git del slice. Invariante: ``shell=False``
y argv estructurado (sin interpolacion de strings, evitando inyeccion); si git no esta
instalado no lanza, devuelve un ``CompletedProcess`` con returncode 127 para que el caller
degrade de forma controlada.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def git_available() -> bool:
    """Indica si el ejecutable ``git`` esta presente en el ``PATH``."""
    return shutil.which("git") is not None


def run_git(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Corre ``git`` con argv estructurado y captura su salida de texto.

    Si git no esta disponible devuelve un ``CompletedProcess`` con returncode 127 en lugar de
    lanzar, dejando la degradacion al caller. Usa ``shell=False`` y ``check=False``, asi un
    exit code distinto de cero se reporta en el resultado, no como excepcion.
    """
    if not git_available():
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=127, stdout="", stderr="git CLI is not available"
        )
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
