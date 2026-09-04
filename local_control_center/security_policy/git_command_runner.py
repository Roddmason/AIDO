"""Ejecuta el CLI de git sin shell y nunca propaga su ausencia como excepcion.

Envuelve ``subprocess.run`` para los pocos comandos git del slice. Invariante: ``shell=False``
y argv estructurado (sin interpolacion de strings, evitando inyeccion); si git no esta
instalado no lanza, devuelve un ``CompletedProcess`` con returncode 127 para que el caller
degrade de forma controlada.

@author Rodrigo Mason
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from local_control_center.process_supervision.service import run_supervised_capture


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
    command = ["git", *args]
    completed = run_supervised_capture(
        command,
        cwd=str(cwd or Path.cwd()),
        timeout_seconds=120,
        workload_class="qa_light",
        popen_factory=subprocess.Popen,
    )
    return subprocess.CompletedProcess(
        args=command,
        returncode=completed["returnCode"] if completed["returnCode"] is not None else 124,
        stdout=completed["stdout"],
        stderr=completed["stderr"],
    )
