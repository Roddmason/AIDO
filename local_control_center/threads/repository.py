"""Persistencia SQLite de los threads reales: header, timeline, artifacts, eventos y decisiones.

Espeja el resto de repositorios de slice: la conexión opera en autocommit por statement
(``isolation_level=None``) y los métodos no abren transacciones propias salvo cuando deben escribir
varias filas de forma atómica o asignar una ``sequence`` monótona sin colisionar con el
``UNIQUE(thread_id, sequence)``; en esos casos envuelven en ``immediate_transaction`` (o reusan la del
caller). Los mapeadores ``row_to_*`` proyectan cada fila al dict camelCase del contrato y ``redact_secrets``
limpia todo contenido libre antes de persistirlo.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import nullcontext
from typing import Any

from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now
from local_control_center.threads.contracts import (
    THREAD_DECISION_STATUSES,
    THREAD_MESSAGE_KINDS,
    THREAD_OWNER_TYPES,
    THREAD_STATUSES,
)

# Append-only tables whose per-thread ``sequence`` is computed by ``_next_sequence``. The set is an
# allowlist so the table name can never reach the SQL string from anywhere but this module.
_SEQUENCE_TABLES = frozenset({"thread_messages", "thread_agent_events"})
_ACTIVE_DELETE_BLOCKING_STATUSES = frozenset({"queued", "running"})


class ThreadLifecycleError(ValueError):
    """Error de lifecycle que debe mapearse a conflicto HTTP cuando bloquea una mutacion valida."""


def row_to_thread(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``project_threads`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "ownerType": row["owner_type"],
        "ownerId": row["owner_id"],
        "title": row["title"],
        "status": row["status"],
        "summary": row["summary"],
        "metadata": json_loads(row["metadata"], {}),
        "archivedAt": _row_value(row, "archived_at"),
        "archivedBy": _row_value(row, "archived_by"),
        "deletedAt": _row_value(row, "deleted_at"),
        "deletedBy": _row_value(row, "deleted_by"),
        "lifecycleReason": _row_value(row, "lifecycle_reason"),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _row_value(row: sqlite3.Row, column: str, default: Any = None) -> Any:
    """Lee una columna opcional de SQLite sin romper bases previas a la migracion."""
    return row[column] if column in row.keys() else default


def row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``thread_messages`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "threadId": row["thread_id"],
        "projectId": row["project_id"],
        "sequence": row["sequence"],
        "kind": row["kind"],
        "author": row["author"],
        "content": row["content"],
        "metadata": json_loads(row["metadata"], {}),
        "createdAt": row["created_at"],
    }


def row_to_artifact(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``thread_artifacts`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "threadId": row["thread_id"],
        "projectId": row["project_id"],
        "messageId": row["message_id"],
        "artifactId": row["artifact_id"],
        "kind": row["kind"],
        "title": row["title"],
        "metadata": json_loads(row["metadata"], {}),
        "createdAt": row["created_at"],
    }


def row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``thread_agent_events`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "threadId": row["thread_id"],
        "projectId": row["project_id"],
        "sequence": row["sequence"],
        "type": row["type"],
        "agentRole": row["agent_role"],
        "payload": json_loads(row["payload"], {}),
        "metadata": json_loads(row["metadata"], {}),
        "createdAt": row["created_at"],
    }


def row_to_decision(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``thread_decisions`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "threadId": row["thread_id"],
        "projectId": row["project_id"],
        "messageId": row["message_id"],
        "title": row["title"],
        "status": row["status"],
        "prompt": row["prompt"],
        "options": json_loads(row["options"], []),
        "resolution": row["resolution"],
        "decidedBy": row["decided_by"],
        "decidedAt": row["decided_at"],
        "metadata": json_loads(row["metadata"], {}),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class ThreadsRepository:
    """Acceso a datos de los threads sobre la conexión SQLite del caller (autocommit por statement)."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _transaction(self):
        """Reusa la transacción del caller si existe; si no, abre una ``immediate_transaction`` propia."""
        if self.connection.in_transaction:
            return nullcontext(self.connection)
        return immediate_transaction(self.connection)

    def _project_id_for(self, thread_id: str) -> str:
        row = self.connection.execute(
            "SELECT project_id FROM project_threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Thread not found: {thread_id}")
        return row["project_id"]

    # -- threads ---------------------------------------------------------------
    def create_thread(
        self,
        *,
        project_id: str,
        owner_type: str,
        owner_id: str,
        title: str,
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Crea un hilo (id ``thread-<uuid>``, estado ``open``) y devuelve el registro creado."""
        if owner_type not in THREAD_OWNER_TYPES:
            raise ValueError(f"Unknown thread owner type: {owner_type}")
        clean_title = str(redact_secrets(title or "")).strip()
        clean_summary = str(redact_secrets(summary or "")).strip()
        if not clean_title:
            raise ValueError("Thread title is required")
        thread_id = f"thread-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO project_threads
                (id, project_id, owner_type, owner_id, title, status, summary, metadata,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
            """,
            (
                thread_id,
                project_id,
                owner_type,
                owner_id,
                clean_title,
                clean_summary,
                json_dumps(redact_secrets(metadata or {})),
                timestamp,
                timestamp,
            ),
        )
        self._index_thread(thread_id)
        return self.get_thread(thread_id)

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        """Devuelve un hilo por id o lanza ``KeyError`` si no existe."""
        row = self.connection.execute("SELECT * FROM project_threads WHERE id = ?", (thread_id,)).fetchone()
        if not row:
            raise KeyError(f"Thread not found: {thread_id}")
        return row_to_thread(row)

    def list_threads(
        self,
        *,
        project_id: str | None = None,
        owner_type: str | None = None,
        owner_id: str | None = None,
        include_archived: bool = False,
        include_deleted: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lista hilos (más recientes primero) con filtro opcional por proyecto y entidad dueña.

        ``limit`` acota a los N con actividad más reciente (desempate determinista por rowid).
        """
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if owner_type is not None:
            clauses.append("owner_type = ?")
            params.append(owner_type)
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if not include_deleted:
            clauses.append("(deleted_at IS NULL AND status <> 'deleted')")
        if not include_archived:
            archived_clause = "(archived_at IS NULL AND status <> 'archived')"
            if include_deleted:
                archived_clause = f"({archived_clause} OR deleted_at IS NOT NULL OR status = 'deleted')"
            clauses.append(archived_clause)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(int(limit))
        rows = self.connection.execute(
            f"SELECT * FROM project_threads {where} ORDER BY updated_at DESC, rowid DESC{limit_sql}",
            params,
        ).fetchall()
        return [row_to_thread(row) for row in rows]

    def rename_thread(
        self,
        thread_id: str,
        title: str,
        *,
        actor: str = "system",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Renombra el hilo y registra ``thread.renamed`` en la auditoria compartida."""
        clean_title = str(redact_secrets(title or "")).strip()
        if not clean_title:
            raise ValueError("Thread title is required")
        actor_name = _required_text(actor, "Lifecycle actor is required")
        timestamp = utc_now()
        with self._transaction():
            current = self.get_thread(thread_id)
            updated = self.connection.execute(
                "UPDATE project_threads SET title = ?, updated_at = ? WHERE id = ?",
                (clean_title, timestamp, thread_id),
            )
            if updated.rowcount == 0:
                raise KeyError(f"Thread not found: {thread_id}")
            EventBus(self.connection).record_audit(
                action="thread.renamed",
                target=thread_id,
                project_id=current["projectId"],
                actor=actor_name,
                payload={
                    "previousTitle": current["title"],
                    "title": clean_title,
                    "reason": reason.strip() if isinstance(reason, str) and reason.strip() else None,
                },
            )
            self._index_thread(thread_id)
        return self.get_thread(thread_id)

    def archive_thread(self, thread_id: str, reason: str, actor: str) -> dict[str, Any]:
        """Archiva el hilo sin tocar mensajes/artifacts y registra ``thread.archived``."""
        reason_text = _required_text(reason, "Lifecycle reason is required")
        actor_name = _required_text(actor, "Lifecycle actor is required")
        timestamp = utc_now()
        with self._transaction():
            current = self.get_thread(thread_id)
            if current["deletedAt"] or current["status"] == "deleted":
                raise ValueError("Deleted thread cannot be archived")
            self.connection.execute(
                """
                UPDATE project_threads
                SET status = 'archived',
                    archived_at = ?,
                    archived_by = ?,
                    lifecycle_reason = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (timestamp, actor_name, reason_text, timestamp, thread_id),
            )
            EventBus(self.connection).record_audit(
                action="thread.archived",
                target=thread_id,
                project_id=current["projectId"],
                actor=actor_name,
                payload={"reason": reason_text, "previousStatus": current["status"]},
            )
            self._index_thread(thread_id)
        return self.get_thread(thread_id)

    def unarchive_thread(self, thread_id: str, reason: str, actor: str) -> dict[str, Any]:
        """Reabre un hilo archivado y registra ``thread.unarchived``."""
        reason_text = _required_text(reason, "Lifecycle reason is required")
        actor_name = _required_text(actor, "Lifecycle actor is required")
        timestamp = utc_now()
        with self._transaction():
            current = self.get_thread(thread_id)
            if current["deletedAt"] or current["status"] == "deleted":
                raise ValueError("Deleted thread cannot be unarchived")
            self.connection.execute(
                """
                UPDATE project_threads
                SET status = 'open',
                    archived_at = NULL,
                    archived_by = NULL,
                    lifecycle_reason = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (reason_text, timestamp, thread_id),
            )
            EventBus(self.connection).record_audit(
                action="thread.unarchived",
                target=thread_id,
                project_id=current["projectId"],
                actor=actor_name,
                payload={"reason": reason_text, "previousStatus": current["status"]},
            )
            self._index_thread(thread_id)
        return self.get_thread(thread_id)

    def soft_delete_thread(self, thread_id: str, reason: str, actor: str) -> dict[str, Any]:
        """Marca el hilo como eliminado sin borrar mensajes ni artifacts fisicamente."""
        reason_text = _required_text(reason, "Lifecycle reason is required")
        actor_name = _required_text(actor, "Lifecycle actor is required")
        timestamp = utc_now()
        with self._transaction():
            current = self.get_thread(thread_id)
            if current["status"] in _ACTIVE_DELETE_BLOCKING_STATUSES:
                raise ThreadLifecycleError(
                    f"Thread {thread_id} is {current['status']} and cannot be deleted until it stops."
                )
            self.connection.execute(
                """
                UPDATE project_threads
                SET status = 'deleted',
                    deleted_at = ?,
                    deleted_by = ?,
                    lifecycle_reason = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (timestamp, actor_name, reason_text, timestamp, thread_id),
            )
            EventBus(self.connection).record_audit(
                action="thread.deleted",
                target=thread_id,
                project_id=current["projectId"],
                actor=actor_name,
                payload={"reason": reason_text, "previousStatus": current["status"]},
            )
            self._index_thread(thread_id)
        return self.get_thread(thread_id)

    def set_status(self, thread_id: str, status: str) -> dict[str, Any]:
        """Cambia el estado del hilo (valida contra ``THREAD_STATUSES``) y refresca ``updated_at``."""
        if status not in THREAD_STATUSES:
            raise ValueError(f"Unknown thread status: {status}")
        updated = self.connection.execute(
            "UPDATE project_threads SET status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now(), thread_id),
        )
        if updated.rowcount == 0:
            raise KeyError(f"Thread not found: {thread_id}")
        self._index_thread(thread_id)
        return self.get_thread(thread_id)

    # -- messages --------------------------------------------------------------
    def append_message(
        self,
        *,
        thread_id: str,
        kind: str,
        author: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Agrega un mensaje al timeline asignando la siguiente ``sequence`` de forma atómica."""
        if kind not in THREAD_MESSAGE_KINDS:
            raise ValueError(f"Unknown thread message kind: {kind}")
        project_id = self._project_id_for(thread_id)
        message_id = f"thread-msg-{uuid.uuid4()}"
        timestamp = utc_now()
        with self._transaction():
            sequence = self._next_sequence("thread_messages", thread_id)
            self.connection.execute(
                """
                INSERT INTO thread_messages
                    (id, thread_id, project_id, sequence, kind, author, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    project_id,
                    sequence,
                    kind,
                    author,
                    redact_secrets(content),
                    json_dumps(redact_secrets(metadata or {})),
                    timestamp,
                ),
            )
            self._index_thread(thread_id)
        return self.get_message(message_id)

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Devuelve un mensaje por id o lanza ``KeyError`` si no existe."""
        row = self.connection.execute("SELECT * FROM thread_messages WHERE id = ?", (message_id,)).fetchone()
        if not row:
            raise KeyError(f"Message not found: {message_id}")
        return row_to_message(row)

    def list_messages(self, thread_id: str) -> list[dict[str, Any]]:
        """Lista los mensajes del hilo ordenados por ``sequence`` ascendente."""
        rows = self.connection.execute(
            "SELECT * FROM thread_messages WHERE thread_id = ? ORDER BY sequence ASC",
            (thread_id,),
        ).fetchall()
        return [row_to_message(row) for row in rows]

    # -- artifacts -------------------------------------------------------------
    def attach_artifact(
        self,
        *,
        thread_id: str,
        kind: str,
        title: str,
        artifact_id: str,
        message_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Enlaza un artifact al hilo (su contenido va en ``metadata``, saneado)."""
        project_id = self._project_id_for(thread_id)
        row_id = f"thread-artifact-{uuid.uuid4()}"
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO thread_artifacts
                    (id, thread_id, project_id, message_id, artifact_id, kind, title, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    thread_id,
                    project_id,
                    message_id,
                    artifact_id,
                    kind,
                    str(redact_secrets(title or "")).strip(),
                    json_dumps(redact_secrets(payload or {})),
                    utc_now(),
                ),
            )
            self._index_thread(thread_id)
        row = self.connection.execute("SELECT * FROM thread_artifacts WHERE id = ?", (row_id,)).fetchone()
        return row_to_artifact(row)

    def list_artifacts(self, thread_id: str) -> list[dict[str, Any]]:
        """Lista los artifacts del hilo, más antiguos primero (orden de aparición)."""
        rows = self.connection.execute(
            "SELECT * FROM thread_artifacts WHERE thread_id = ? ORDER BY created_at ASC, rowid ASC",
            (thread_id,),
        ).fetchall()
        return [row_to_artifact(row) for row in rows]

    # -- events ----------------------------------------------------------------
    def record_event(
        self,
        *,
        thread_id: str,
        type: str,
        payload: dict[str, Any] | None = None,
        agent_role: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Registra un evento append-only del hilo con su ``sequence`` monótona."""
        project_id = self._project_id_for(thread_id)
        event_id = f"thread-event-{uuid.uuid4()}"
        timestamp = utc_now()
        with self._transaction():
            sequence = self._next_sequence("thread_agent_events", thread_id)
            self.connection.execute(
                """
                INSERT INTO thread_agent_events
                    (id, thread_id, project_id, sequence, type, agent_role, payload, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    thread_id,
                    project_id,
                    sequence,
                    type,
                    agent_role,
                    json_dumps(redact_secrets(payload or {})),
                    json_dumps(redact_secrets(metadata or {})),
                    timestamp,
                ),
            )
            self._index_thread(thread_id)
        row = self.connection.execute(
            "SELECT * FROM thread_agent_events WHERE id = ?", (event_id,)
        ).fetchone()
        return row_to_event(row)

    def list_events(self, thread_id: str) -> list[dict[str, Any]]:
        """Lista los eventos del hilo ordenados por ``sequence`` ascendente."""
        rows = self.connection.execute(
            "SELECT * FROM thread_agent_events WHERE thread_id = ? ORDER BY sequence ASC",
            (thread_id,),
        ).fetchall()
        return [row_to_event(row) for row in rows]

    def list_events_after(
        self,
        thread_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        """Lista una ventana incremental de eventos con ``sequence`` mayor al valor recibido."""
        self._project_id_for(thread_id)
        bounded_limit = max(1, min(int(limit), 300))
        rows = self.connection.execute(
            """
            SELECT * FROM thread_agent_events
            WHERE thread_id = ? AND sequence > ?
            ORDER BY sequence ASC
            LIMIT ?
            """,
            (thread_id, int(after_sequence), bounded_limit),
        ).fetchall()
        return [row_to_event(row) for row in rows]

    # -- decisions -------------------------------------------------------------
    def create_decision(
        self,
        *,
        thread_id: str,
        title: str,
        prompt: str,
        options: list[str] | None = None,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Crea una solicitud de decisión pendiente enlazada al mensaje que la levantó."""
        project_id = self._project_id_for(thread_id)
        decision_id = f"thread-decision-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO thread_decisions
                (id, thread_id, project_id, message_id, title, status, prompt, options, resolution,
                 decided_by, decided_at, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, NULL, NULL, NULL, ?, ?, ?)
            """,
            (
                decision_id,
                thread_id,
                project_id,
                message_id,
                title,
                prompt,
                json_dumps(list(options or [])),
                json_dumps(redact_secrets(metadata or {})),
                timestamp,
                timestamp,
            ),
        )
        self._index_thread(thread_id)
        return self.get_decision(decision_id)

    def get_decision(self, decision_id: str) -> dict[str, Any]:
        """Devuelve una decisión por id o lanza ``KeyError`` si no existe."""
        row = self.connection.execute(
            "SELECT * FROM thread_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Decision not found: {decision_id}")
        return row_to_decision(row)

    def resolve_decision(
        self,
        *,
        thread_id: str,
        decision_id: str,
        resolution: str,
        decided_by: str | None = None,
        status: str = "resolved",
    ) -> dict[str, Any]:
        """Resuelve (o descarta) una decisión pendiente del hilo registrando quién y cuándo."""
        if status not in THREAD_DECISION_STATUSES:
            raise ValueError(f"Unknown decision status: {status}")
        timestamp = utc_now()
        updated = self.connection.execute(
            """
            UPDATE thread_decisions
            SET status = ?, resolution = ?, decided_by = ?, decided_at = ?, updated_at = ?
            WHERE id = ? AND thread_id = ?
            """,
            (status, resolution, decided_by, timestamp, timestamp, decision_id, thread_id),
        )
        if updated.rowcount == 0:
            raise KeyError(f"Decision not found: {decision_id}")
        self._index_thread(thread_id)
        return self.get_decision(decision_id)

    def list_decisions(self, thread_id: str) -> list[dict[str, Any]]:
        """Lista las decisiones del hilo, más recientes primero."""
        rows = self.connection.execute(
            "SELECT * FROM thread_decisions WHERE thread_id = ? ORDER BY created_at DESC, rowid DESC",
            (thread_id,),
        ).fetchall()
        return [row_to_decision(row) for row in rows]

    def set_summary(self, thread_id: str, summary: str) -> None:
        """Actualiza el summary tocando ``updated_at`` y refrescando el índice de similitud."""
        timestamp = utc_now()
        with self._transaction():
            updated = self.connection.execute(
                "UPDATE project_threads SET summary = ?, updated_at = ? WHERE id = ?",
                (summary, timestamp, thread_id),
            )
            if updated.rowcount == 0:
                raise KeyError(f"Thread not found: {thread_id}")
            self._index_thread(thread_id)

    # -- helpers ---------------------------------------------------------------
    def _index_thread(self, thread_id: str) -> None:
        from local_control_center.threads.similarity import ThreadSimilarityService

        ThreadSimilarityService(self.connection).index_thread(thread_id)

    def _next_sequence(self, table: str, thread_id: str) -> int:
        if table not in _SEQUENCE_TABLES:
            raise ValueError(f"Unknown sequence table: {table}")
        row = self.connection.execute(
            f"SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM {table} WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return int(row["next"])


def _required_text(value: str, message: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(message)
    return text
