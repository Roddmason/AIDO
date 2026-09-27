"""Lectura de variables de entorno que también ve las persistidas en Windows después de arrancar.

Windows copia el entorno a un proceso al crearlo: una variable creada con ``setx`` o desde "Variables
de entorno" no llega a AIDO ni a la terminal que ya estaban abiertos. El operador la ve en el panel
del sistema y AIDO responde "is not set". Se consulta primero el entorno del proceso (manda un valor
explícito) y, solo en Windows, luego el del usuario (clave ``Environment`` de HKCU) y el del sistema.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping

_SYSTEM_ENVIRONMENT_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"


def _windows_persisted_value(name: str) -> str | None:
    """Valor persistido de ``name`` para el usuario y luego el sistema; ``None`` fuera de Windows."""
    if os.name != "nt":
        return None
    import winreg  # type: ignore[import-not-found]

    for hive, path in (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (winreg.HKEY_LOCAL_MACHINE, _SYSTEM_ENVIRONMENT_KEY),
    ):
        try:
            with winreg.OpenKey(hive, path) as key:
                value, kind = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        return os.path.expandvars(value) if kind == winreg.REG_EXPAND_SZ else value
    return None


def lookup_environment_variable(name: str, environ: Mapping[str, str] | None = None) -> str | None:
    """Valor de ``name`` en el entorno dado o, sin ``environ`` explícito, en el del proceso o Windows.

    Un ``environ`` explícito (tests, overrides) nunca consulta el registro.
    """
    source = os.environ if environ is None else environ
    value = source.get(name)
    if value is not None and value.strip():
        return value
    if environ is not None:
        return value
    return _windows_persisted_value(name) or value


class ProcessAndPersistedEnvironment(Mapping[str, str]):
    """``os.environ`` que, al faltar una clave, consulta lo persistido en Windows (solo lecturas)."""

    def __getitem__(self, name: str) -> str:
        value = lookup_environment_variable(name)
        if value is None:
            raise KeyError(name)
        return value

    def __iter__(self) -> Iterator[str]:
        return iter(os.environ)

    def __len__(self) -> int:
        return len(os.environ)


def process_environment() -> Mapping[str, str]:
    """Entorno por defecto de AIDO: el del proceso más lo persistido en Windows."""
    return ProcessAndPersistedEnvironment()


__all__ = ["ProcessAndPersistedEnvironment", "lookup_environment_variable", "process_environment"]
