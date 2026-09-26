"""Retencion acotada de `operational_executions`, la cola/historial de operaciones encoladas.

Medido en la instalacion real: 20.472 filas / 225 MiB (~11 KB/fila), dominadas por chequeos de salud
(`models.provider_health_check` ~6,7k, `models.health_cli_runtime` ~10,8k). Esas dos operaciones se
retienen 7 dias; el resto de operaciones terminales, 30 dias. `git_workspace/api.py` cachea el
ultimo `git.refresh` completado por proyecto (``WHERE project_id=? AND operation='git.refresh' AND
status='completed' ORDER BY finished_at DESC LIMIT 1``); esa fila nunca se poda, sin importar su
antiguedad. Las filas se insertan y transicionan en orden temporal real (nunca retrocede
``finished_at``), asi que el ``rowid`` mas alto de ese filtro es siempre la mas reciente.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3

from local_control_center.executions.models import TERMINAL_STATUSES
from local_control_center.shared.time import utc_cutoff_iso

OPERATIONAL_HEALTH_RETENTION_SECONDS = 7 * 24 * 60 * 60
OPERATIONAL_DEFAULT_RETENTION_SECONDS = 30 * 24 * 60 * 60
HEALTH_CHECK_OPERATIONS = ("models.provider_health_check", "models.health_cli_runtime")
_TERMINAL_STATUSES = tuple(TERMINAL_STATUSES)


def prune_operational_executions(
    connection: sqlite3.Connection,
    *,
    health_retention_seconds: int = OPERATIONAL_HEALTH_RETENTION_SECONDS,
    default_retention_seconds: int = OPERATIONAL_DEFAULT_RETENTION_SECONDS,
    batch_size: int = 5_000,
    max_batches: int = 200,
) -> int:
    """Poda ejecuciones terminales por lotes cortos con dos ventanas de retencion.

    Nunca toca `queued`/`running`/`cancel_requested`/`resource_wait` (no terminales) ni el ultimo
    `git.refresh` completado de cada proyecto. Usa el indice existente `(status, created_at)`.
    """
    status_placeholders = ",".join("?" for _ in _TERMINAL_STATUSES)
    health_placeholders = ",".join("?" for _ in HEALTH_CHECK_OPERATIONS)
    total = 0

    health_cutoff = utc_cutoff_iso(health_retention_seconds)
    for _ in range(max_batches):
        cursor = connection.execute(
            f"""
            DELETE FROM operational_executions WHERE rowid IN (
                SELECT rowid FROM operational_executions
                WHERE status IN ({status_placeholders})
                  AND operation IN ({health_placeholders})
                  AND created_at < ?
                ORDER BY rowid LIMIT ?
            )
            """,
            (*_TERMINAL_STATUSES, *HEALTH_CHECK_OPERATIONS, health_cutoff, batch_size),
        )
        total += cursor.rowcount
        if cursor.rowcount < batch_size:
            break

    default_cutoff = utc_cutoff_iso(default_retention_seconds)
    for _ in range(max_batches):
        cursor = connection.execute(
            f"""
            DELETE FROM operational_executions WHERE rowid IN (
                SELECT rowid FROM operational_executions
                WHERE status IN ({status_placeholders})
                  AND operation NOT IN ({health_placeholders})
                  AND created_at < ?
                  AND rowid NOT IN (
                      SELECT MAX(rowid) FROM operational_executions
                      WHERE operation = 'git.refresh' AND status = 'completed'
                      GROUP BY project_id
                  )
                ORDER BY rowid LIMIT ?
            )
            """,
            (*_TERMINAL_STATUSES, *HEALTH_CHECK_OPERATIONS, default_cutoff, batch_size),
        )
        total += cursor.rowcount
        if cursor.rowcount < batch_size:
            break
    return total
