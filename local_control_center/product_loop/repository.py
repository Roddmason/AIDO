"""Persistencia SQLite del product loop: el estado durable y su bitácora de transiciones.

Guarda cada loop en ``product_loops`` (estado actual, anterior, contexto y versión) y cada cambio en
``product_loop_transitions`` (append-only, una fila por versión). Los mapeadores ``row_to_*`` proyectan
las filas al dict camelCase del contrato.

Transacciones: la conexión se abre en autocommit (``isolation_level=None``, ver ``shared/db.py``) y
estos métodos NO abren transacciones propias; cada ``execute`` se confirma de inmediato. Una transición
escribe en dos tablas (UPDATE del loop + INSERT de la transición), por lo que el caller —el
``ProductLoopCoordinator``— las agrupa en una única ``immediate_transaction`` para que el avance de
estado y su registro se confirmen atómicamente y el loop nunca quede en un estado a medias.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def stable_task_suffix(thread_id: str | None, loop_id: str) -> str:
    """Sufijo estable por hilo para nombrar workspace/rama/task del loop.

    Deriva del ``thread_id`` para que TODOS los turnos del mismo hilo compartan una identidad
    —una rama estable reutilizada, no una ``codex/product-*-<hash>`` por mensaje— y cae al
    ``loop_id`` cuando no hay hilo. Es determinista y seguro como segmento de rama git (solo
    caracteres alfanuméricos, hasta 12). ``cost_performance`` reusa este mismo helper para
    correlacionar costo por hilo con el mismo esquema.
    """
    source = str(thread_id or loop_id or "")
    normalized = "".join(char for char in source.lower() if char.isalnum())
    return normalized[-12:] or "workspace"


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


def row_to_product_loop_feedback(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``product_loop_feedback`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "loopId": row["loop_id"],
        "projectId": row["project_id"],
        "action": row["action"],
        "classification": row["classification"],
        "feedback": row["feedback"],
        "actor": row["actor"],
        "targetType": row["target_type"],
        "targetId": row["target_id"],
        "status": row["status"],
        "effects": json_loads(row["effects"], []),
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
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

    def update_loop_context(self, loop_id: str, *, context: dict[str, Any]) -> dict[str, Any]:
        """Actualiza solo el contexto durable del loop, sin tocar estado ni versión de la FSM.

        Camino de medición (consumo de presupuesto): reescribe ``context`` y ``updated_at`` pero deja
        intactos ``state``/``version``, de modo que el consumo no genera una transición ni desplaza el
        guard optimista de la FSM. La atomicidad del read-modify-write la garantiza el caller con una
        ``immediate_transaction``.

        Raises:
            KeyError: si el loop no existe.
        """
        self.get_loop(loop_id)
        self.connection.execute(
            "UPDATE product_loops SET context = ?, updated_at = ? WHERE id = ?",
            (json_dumps(context or {}), utc_now(), loop_id),
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

    def create_feedback(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta un comando de feedback del usuario y devuelve el registro creado."""
        feedback_id = f"product-loop-feedback-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO product_loop_feedback
                (id, loop_id, project_id, action, classification, feedback, actor, target_type,
                 target_id, status, effects, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback_id,
                body["loopId"],
                body["projectId"],
                body["action"],
                body["classification"],
                str(redact_secrets(body["feedback"])),
                body.get("actor", "operator"),
                body["targetType"],
                body["targetId"],
                body.get("status", "recorded"),
                json_dumps(redact_secrets(body.get("effects") or [])),
                json_dumps(redact_secrets(body.get("metadata") or {})),
                timestamp,
                timestamp,
            ),
        )
        return self.get_feedback(feedback_id)

    def get_feedback(self, feedback_id: str) -> dict[str, Any]:
        """Recupera un feedback por id.

        Raises:
            KeyError: si no existe ningún registro de feedback con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM product_loop_feedback WHERE id = ?", (feedback_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Product loop feedback not found: {feedback_id}")
        return row_to_product_loop_feedback(row)

    def update_feedback_effects(
        self,
        feedback_id: str,
        *,
        effects: list[dict[str, Any]],
        status: str = "applied",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Actualiza efectos/status de un feedback tras aplicar sus cambios dentro de la transacción."""
        current = self.get_feedback(feedback_id)
        self.connection.execute(
            """
            UPDATE product_loop_feedback
            SET status = ?, effects = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                json_dumps(redact_secrets(effects)),
                json_dumps(redact_secrets(metadata if metadata is not None else current["metadata"])),
                utc_now(),
                feedback_id,
            ),
        )
        return self.get_feedback(feedback_id)

    def list_feedback(
        self, *, loop_id: str | None = None, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lista feedback por loop o proyecto, en orden cronológico."""
        conditions: list[str] = []
        params: list[Any] = []
        if loop_id:
            conditions.append("loop_id = ?")
            params.append(loop_id)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM product_loop_feedback {where} ORDER BY created_at ASC",
            params,
        ).fetchall()
        return [row_to_product_loop_feedback(row) for row in rows]
