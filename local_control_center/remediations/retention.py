"""Retencion acotada de `remediation_actions`: poda de terminales antiguos y backfill de payloads.

Medido en la instalacion real: 498 filas / 284 MiB (~597 KB por fila, maximo 14,3 MB), casi todo
`payload.details.teamSchedule.roles` y `.resourceBlockers`. `compaction.py` recorta esas dos claves
al escribir (`repository.create_action`); este modulo cubre lo que ese recorte en origen no toca:
las filas ya persistidas antes del cambio (backfill por pasadas horarias acotadas, ver
`backfill_oversized_remediation_payloads`) y la retencion por antiguedad de estados terminales.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import time

from local_control_center.remediations.compaction import compact_remediation_payload
from local_control_center.remediations.repository import DISPLAY_DETAILS_LIMIT_BYTES
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_cutoff_iso

REMEDIATION_TERMINAL_RETENTION_SECONDS = 30 * 24 * 60 * 60
REMEDIATION_TERMINAL_STATUSES = ("resolved", "dismissed", "failed")


def prune_terminal_remediation_actions(
    connection: sqlite3.Connection,
    *,
    retention_seconds: int = REMEDIATION_TERMINAL_RETENTION_SECONDS,
    batch_size: int = 5_000,
    max_batches: int = 200,
) -> int:
    """Borra remediaciones terminales resueltas hace mas de ``retention_seconds``; nunca `pending`.

    El allowlist positivo de estados (``resolved``/``dismissed``/``failed``) es la garantia
    estructural de "nunca pending": una fila pending jamas entra al ``WHERE status IN (...)``. Cada
    lote es su propio ``DELETE`` en autocommit, igual que ``drain_resource_history``.
    """
    cutoff = utc_cutoff_iso(retention_seconds)
    placeholders = ",".join("?" for _ in REMEDIATION_TERMINAL_STATUSES)
    total = 0
    for _ in range(max_batches):
        cursor = connection.execute(
            f"""
            DELETE FROM remediation_actions WHERE rowid IN (
                SELECT rowid FROM remediation_actions
                WHERE status IN ({placeholders}) AND resolved_at IS NOT NULL AND resolved_at < ?
                ORDER BY rowid LIMIT ?
            )
            """,
            (*REMEDIATION_TERMINAL_STATUSES, cutoff, batch_size),
        )
        total += cursor.rowcount
        if cursor.rowcount < batch_size:
            break
    return total


BACKFILL_MAX_ROWS_PER_PASS = 25
BACKFILL_MAX_SECONDS_PER_PASS = 10.0


def backfill_oversized_remediation_payloads(
    connection: sqlite3.Connection,
    *,
    size_limit_bytes: int = DISPLAY_DETAILS_LIMIT_BYTES,
    max_rows_per_pass: int = BACKFILL_MAX_ROWS_PER_PASS,
    max_seconds_per_pass: float = BACKFILL_MAX_SECONDS_PER_PASS,
) -> int:
    """Compacta una tanda acotada de filas oversized, una pasada de la retencion horaria.

    Decodificar y recomprimir un blocker con miles de rechazos cuesta cientos de ms de CPU por
    fila; hacerlo dentro de una transaccion (o encadenar muchos ``UPDATE`` sin soltar el candado)
    retendria el candado de escritura mientras el API sigue con su propio ``busy_timeout``. Por eso
    el ``SELECT`` y la compactacion en Python ocurren sin transaccion abierta, y cada ``UPDATE`` es
    su propio statement autocommit; la pasada corta apenas se agota ``max_rows_per_pass`` o
    ``max_seconds_per_pass`` (lo que ocurra primero), dejando el resto para la proxima hora. El
    propio ``WHERE length(...) > ?`` hace el backfill idempotente: una fila ya compactada deja de
    calificar y no se vuelve a tocar.
    """
    rows = connection.execute(
        """
        SELECT id, payload_json FROM remediation_actions
        WHERE length(CAST(payload_json AS BLOB)) > ?
        ORDER BY rowid LIMIT ?
        """,
        (size_limit_bytes, max_rows_per_pass),
    ).fetchall()
    # El reloj arranca despues del SELECT: el presupuesto de tiempo es para decodificar/recomprimir,
    # no para localizar candidatas (que ya viene acotado por LIMIT).
    started = time.perf_counter()
    compacted_count = 0
    for row in rows:
        if time.perf_counter() - started > max_seconds_per_pass:
            break
        compacted = compact_remediation_payload(json_loads(row["payload_json"], {}))
        connection.execute(
            "UPDATE remediation_actions SET payload_json = ? WHERE id = ?",
            (json_dumps(compacted), row["id"]),
        )
        compacted_count += 1
    return compacted_count


def drain_remediation_retention(
    connection: sqlite3.Connection,
    *,
    batch_size: int = 5_000,
    max_batches: int = 200,
    backfill_max_rows: int = BACKFILL_MAX_ROWS_PER_PASS,
    backfill_max_seconds: float = BACKFILL_MAX_SECONDS_PER_PASS,
) -> dict[str, int]:
    """Aplica la retencion completa de remediaciones: poda de terminales + backfill de payloads."""
    return {
        "prunedTerminalActions": prune_terminal_remediation_actions(
            connection, batch_size=batch_size, max_batches=max_batches
        ),
        "compactedOversizedPayloads": backfill_oversized_remediation_payloads(
            connection, max_rows_per_pass=backfill_max_rows, max_seconds_per_pass=backfill_max_seconds
        ),
    }
