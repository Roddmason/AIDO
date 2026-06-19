"""Resolución de rutas de runtime configurables por variables de entorno.

Decide dónde vive la base de datos SQLite y cuál es el directorio de trabajo,
con valores por defecto bajo el home del usuario y overrides por entorno para
tests y despliegues. Mantiene esta política en un solo lugar para no dispersar rutas.
"""

from __future__ import annotations

import os
from pathlib import Path


def default_db_path() -> Path:
    """Resuelve la ruta de la SQLite: usa ``LOCAL_CONTROL_CENTER_DB`` o el default bajo el home."""
    explicit = os.environ.get("LOCAL_CONTROL_CENTER_DB")
    if explicit:
        return Path(explicit)
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home()))
    return home / ".claude" / "local-control-center" / "platform.sqlite"


def default_cwd() -> Path:
    """Resuelve el directorio de trabajo: ``LOCAL_CONTROL_CENTER_CWD`` o el cwd del proceso."""
    explicit = os.environ.get("LOCAL_CONTROL_CENTER_CWD")
    return Path(explicit) if explicit else Path.cwd()
