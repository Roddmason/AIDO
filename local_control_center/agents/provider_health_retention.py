"""Retencion acotada de `provider_health_checks`, la tabla de chequeos de salud de proveedores.

Medido en la instalacion real: 6.803 filas / 148 MiB, sin indice y sin poda; solo se borraban al
eliminar un endpoint local. El unico lector conocido (``ollama.api._latest_latency_for_provider``)
pide siempre la fila mas reciente por ``provider_id``, asi que esa fila nunca se borra sin importar
su antiguedad.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3

from local_control_center.shared.time import utc_cutoff_iso

PROVIDER_HEALTH_CHECK_RETENTION_SECONDS = 7 * 24 * 60 * 60


def prune_provider_health_checks(
    connection: sqlite3.Connection,
    *,
    retention_seconds: int = PROVIDER_HEALTH_CHECK_RETENTION_SECONDS,
    batch_size: int = 5_000,
    max_batches: int = 200,
) -> int:
    """Borra chequeos vencidos por lotes cortos, conservando siempre el ultimo por proveedor.

    Sin indice en ``created_at``/``provider_id``: igual que ``prune_admission_decisions``, cada lote
    es un unico ``DELETE ... WHERE rowid IN (SELECT ... ORDER BY rowid LIMIT ?)`` en autocommit, para
    no retener el candado de escritura mientras el API y el worker siguen trabajando.
    """
    cutoff = utc_cutoff_iso(retention_seconds)
    total = 0
    for _ in range(max_batches):
        cursor = connection.execute(
            """
            DELETE FROM provider_health_checks WHERE rowid IN (
                SELECT rowid FROM provider_health_checks
                WHERE created_at < ?
                  AND rowid NOT IN (SELECT MAX(rowid) FROM provider_health_checks GROUP BY provider_id)
                ORDER BY rowid LIMIT ?
            )
            """,
            (cutoff, batch_size),
        )
        total += cursor.rowcount
        if cursor.rowcount < batch_size:
            break
    return total
