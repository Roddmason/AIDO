"""Política acotada de retención para muestras de capacidad del host.

@author Rodrigo Mason
"""

from __future__ import annotations

from .repository import ResourceRepository

RESOURCE_SAMPLE_RETENTION_SECONDS = 7 * 24 * 60 * 60
ADMISSION_DECISION_RETENTION_SECONDS = 7 * 24 * 60 * 60
"""Las decisiones de admision se conservan la misma ventana que las muestras que las motivaron."""


def prune_resource_history(repository: ResourceRepository) -> int:
    """Conserva como máximo siete días de historia de recursos y devuelve filas eliminadas.

    Cubre las **dos** tablas que crecen por cada intento de admision. Podar solo las muestras
    dejaba `resource_admission_decisions` sin techo: 119,3 MB medidos en la instalacion real.
    """
    return repository.prune_samples(
        retention_seconds=RESOURCE_SAMPLE_RETENTION_SECONDS
    ) + repository.prune_admission_decisions(retention_seconds=ADMISSION_DECISION_RETENTION_SECONDS)
