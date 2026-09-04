"""Política acotada de retención para muestras de capacidad del host.

@author Rodrigo Mason
"""

from __future__ import annotations

from .repository import ResourceRepository

RESOURCE_SAMPLE_RETENTION_SECONDS = 7 * 24 * 60 * 60


def prune_resource_history(repository: ResourceRepository) -> int:
    """Conserva como máximo siete días de muestras y devuelve filas eliminadas."""
    return repository.prune_samples(retention_seconds=RESOURCE_SAMPLE_RETENTION_SECONDS)
