"""Exporta el worker sin cargar su runtime al importar contratos de liderazgo.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import LocalWorkerRuntime

__all__ = ["LocalWorkerRuntime"]


def __getattr__(name: str) -> type[LocalWorkerRuntime]:
    """Conserva el export público sin crear un ciclo entre worker y remediaciones."""
    if name == "LocalWorkerRuntime":
        from .runtime import LocalWorkerRuntime

        return LocalWorkerRuntime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
