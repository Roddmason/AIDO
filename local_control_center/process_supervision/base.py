"""Protocolo común para supervisores nativos de árboles de procesos.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any, Protocol

from .models import ProcessLaunchSpec, ProcessStats, SupervisedProcess


class ProcessSupervisor(Protocol):
    """Contrato mínimo de contención, terminación, observación y liberación."""

    def start(self, spec: ProcessLaunchSpec, **popen_kwargs: Any) -> SupervisedProcess:
        """Crea el proceso contenido antes de permitir que ejecute trabajo."""
        ...

    def terminate_tree(
        self, process: SupervisedProcess, *, grace_seconds: float, reason: str
    ) -> ProcessStats:
        """Solicita cierre y fuerza el término de todo el árbol tras la gracia."""
        ...

    def stats(self, process: SupervisedProcess) -> ProcessStats:
        """Obtiene estadísticas acumuladas y descendientes aún activos."""
        ...

    def release(self, process: SupervisedProcess) -> None:
        """Libera el contenedor nativo de forma idempotente."""
        ...
