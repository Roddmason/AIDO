"""Política acotada de retención para muestras de capacidad del host.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3

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


def drain_resource_history(
    connection: sqlite3.Connection, *, batch_size: int = 5_000, max_batches: int = 200
) -> int:
    """Aplica la retencion de recursos en lotes cortos y devuelve cuantas decisiones borro.

    Pensada para la poda periodica del worker sobre una conexion autocommit: cada lote es su
    propia transaccion, asi un atraso grande (131.757 decisiones vencidas en la instalacion real)
    no retiene el candado de escritura mientras el API y el worker siguen trabajando. El tope de
    lotes acota una corrida; lo que quede se drena en la siguiente.
    """
    repository = ResourceRepository(connection)
    repository.prune_samples(retention_seconds=RESOURCE_SAMPLE_RETENTION_SECONDS)
    total = 0
    for _ in range(max_batches):
        removed = repository.prune_admission_decisions(
            retention_seconds=ADMISSION_DECISION_RETENTION_SECONDS, batch_size=batch_size
        )
        total += removed
        if removed < batch_size:
            break
    return total
