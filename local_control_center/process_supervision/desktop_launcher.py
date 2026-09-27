"""Lanzador del explorador de archivos del SO para abrir la carpeta de un proyecto registrado.

Vive en ``process_supervision`` porque es el único paquete autorizado a crear procesos. El argv es
fijo por plataforma (``explorer.exe`` en Windows, ``open`` en macOS, ``xdg-open`` en Linux) más una
única ruta absoluta de directorio existente; nunca usa shell ni acepta flags del llamador. El
proceso queda desacoplado (el explorador debe sobrevivir a la request) y un hilo daemon lo reaperá.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .context import assert_external_boundary

REAP_TIMEOUT_SECONDS = 60


class FileManagerUnavailable(RuntimeError):
    """El host no tiene un lanzador de explorador de archivos utilizable."""


def file_manager_argv(path: Path, *, platform: str | None = None) -> list[str]:
    """Devuelve el argv fijo del explorador de archivos para ``path`` en la plataforma dada.

    Raises:
        FileManagerUnavailable: si no hay lanzador (p. ej. Linux sin ``xdg-open``).
        ValueError: si la ruta no es absoluta o parece una opción.
    """
    target = str(path)
    if not path.is_absolute() or target.startswith("-"):
        raise ValueError("File manager target must be an absolute directory path.")
    system = platform or sys.platform
    if system == "win32":
        system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
        explorer = Path(system_root) / "explorer.exe"
        return [str(explorer) if explorer.exists() else "explorer.exe", target]
    if system == "darwin":
        return ["/usr/bin/open", target]
    launcher = shutil.which("xdg-open")
    if not launcher:
        raise FileManagerUnavailable("xdg-open was not found on PATH; install xdg-utils to open folders.")
    return [launcher, target]


def open_in_file_manager(
    path: Path, *, popen_factory: Callable[..., Any] = subprocess.Popen, platform: str | None = None
) -> dict[str, Any]:
    """Abre ``path`` (directorio existente) en el explorador del SO sin shell y sin esperar su cierre."""
    assert_external_boundary()
    resolved = path.resolve(strict=False)
    if not resolved.is_dir():
        raise FileNotFoundError(f"Project folder no longer exists: {resolved}")
    argv = file_manager_argv(resolved, platform=platform)
    system = platform or sys.platform
    options: dict[str, Any] = {
        "shell": False,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if system == "win32":
        options["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200
        )
    else:
        options["start_new_session"] = True
    process = popen_factory(argv, **options)
    wait = getattr(process, "wait", None)
    if callable(wait):
        # explorer.exe/open/xdg-open delegan en el explorador ya residente y terminan enseguida.
        threading.Thread(target=_reap, args=(wait,), name="aido-file-manager-reaper", daemon=True).start()
    return {"argv": argv, "pid": getattr(process, "pid", None)}


def _reap(wait: Callable[..., Any]) -> None:
    try:
        wait(timeout=REAP_TIMEOUT_SECONDS)
    except Exception:  # el reaper nunca debe propagar; el proceso sigue desacoplado.
        return
