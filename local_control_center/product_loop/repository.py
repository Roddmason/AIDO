"""Persistencia SQLite del product loop: el estado durable y su bitácora de transiciones.

Guarda cada loop en ``product_loops`` (estado actual, anterior, contexto y versión) y cada cambio en
``product_loop_transitions`` (append-only, una fila por versión). Los mapeadores ``row_to_*`` proyectan
las filas al dict camelCase del contrato.

Transacciones: la conexión se abre en autocommit (``isolation_level=None``, ver ``shared/db.py``) y
estos métodos NO abren transacciones propias; cada ``execute`` se confirma de inmediato. Una transición
escribe en dos tablas (UPDATE del loop + INSERT de la transición), por lo que el caller —el
``ProductLoopCoordinator``— las agrupa en una única ``immediate_transaction`` para que el avance de
estado y su registro se confirmen atómicamente y el loop nunca quede en un estado a medias.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def row_to_product_loop(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``product_loops`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "initiativeId": row["initiative_id"],
        "title": row["title"],
        "state": row["state"],
        "previousState": row["previous_state"],
        "status": row["status"],
        "context": json_loads(row["context"]),
        "version": row["version"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_product_loop_transition(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``product_loop_transitions`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "loopId": row["loop_id"],
        "projectId": row["project_id"],
        "fromState": row["from_state"],
        "toState": row["to_state"],
        "reason": row["reason"],
        "actor": row["actor"],
        "trigger": row["trigger"],
        "version": row["version"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
    }


class ProductLoopRepository:
    """Acceso a datos del product loop sobre la conexión SQLite del caller (autocommit por statement)."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_loop(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta un loop nuevo (id ``product-loop-<uuid>``, versión 1) y devuelve el registro creado."""
        loop_id = f"product-loop-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO product_loops
                (id, project_id, initiative_id, title, state, previous_state, status, context,
                 version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                loop_id,
                body["projectId"],
                body.get("initiativeId"),
                body["title"],
                body["state"],
                body.get("previousState"),
                body["status"],
                json_dumps(body.get("context") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_loop(loop_id)

    def get_loop(self, loop_id: str) -> dict[str, Any]:
        """Devuelve el loop por id (lee siempre el estado durable de la base).

        Raises:
            KeyError: si no existe ningún loop con ese id.
        """
        row = self.connection.execute("SELECT * FROM product_loops WHERE id = ?", (loop_id,)).fetchone()
        if not row:
            raise KeyError(f"Product loop not found: {loop_id}")
        return row_to_product_loop(row)

    def list_loops(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista loops (todos o por proyecto), el más recientemente actualizado primero."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM product_loops WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM product_loops ORDER BY updated_at DESC").fetchall()
        return [row_to_product_loop(row) for row in rows]

    def update_loop_state(
        self,
        loop_id: str,
        *,
        state: str,
        previous_state: str | None,
        status: str,
        context: dict[str, Any],
        version: int,
    ) -> dict[str, Any]:
        """Aplica el nuevo estado, estado anterior, status, contexto y versión al loop.

        Raises:
            KeyError: si el loop no existe.
        """
        self.get_loop(loop_id)
        self.connection.execute(
            """
            UPDATE product_loops
            SET state = ?, previous_state = ?, status = ?, context = ?, version = ?, updated_at = ?
            WHERE id = ?
            """,
            (state, previous_state, status, json_dumps(context or {}), version, utc_now(), loop_id),
        )
        return self.get_loop(loop_id)

    def create_transition(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una transición en la bitácora append-only y la devuelve."""
        transition_id = f"product-loop-transition-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO product_loop_transitions
                (id, loop_id, project_id, from_state, to_state, reason, actor, trigger, version,
                 metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transition_id,
                body["loopId"],
                body["projectId"],
                body["fromState"],
                body["toState"],
                body.get("reason", ""),
                body.get("actor", "operator"),
                body.get("trigger", ""),
                int(body["version"]),
                json_dumps(body.get("metadata") or {}),
                utc_now(),
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM product_loop_transitions WHERE id = ?", (transition_id,)
        ).fetchone()
        return row_to_product_loop_transition(row)

    def list_transitions(self, loop_id: str) -> list[dict[str, Any]]:
        """Lista las transiciones de un loop en orden cronológico estable (por versión ascendente)."""
        rows = self.connection.execute(
            "SELECT * FROM product_loop_transitions WHERE loop_id = ? ORDER BY version ASC",
            (loop_id,),
        ).fetchall()
        return [row_to_product_loop_transition(row) for row in rows]
