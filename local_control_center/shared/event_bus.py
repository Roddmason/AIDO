"""Bitácora append-only de eventos operativos y de auditoría sobre SQLite.

Persiste y lee dos flujos: ``events`` (telemetría/actividad por job/proyecto) y
``audit_events`` (acciones de actores sobre objetivos). Redacta secretos del payload
antes de escribir y normaliza las filas a dicts en camelCase para la capa de API.
Escribe una fila por llamada usando el autocommit de la conexión; no abre transacciones propias.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from .redaction import redact_secrets
from .serialization import json_dumps, json_loads
from .time import utc_now


def row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    """Convierte una fila de ``events`` en el dict camelCase de API, derivando ``severity`` del payload."""
    payload = json_loads(row["payload"])
    return {
        "id": row["id"],
        "jobId": row["job_id"],
        "projectId": row["project_id"],
        "type": row["type"],
        "severity": str(payload.get("severity") or "info"),
        "payload": payload,
        "createdAt": row["created_at"],
    }


def row_to_audit(row: sqlite3.Row) -> dict[str, Any]:
    """Convierte una fila de ``audit_events`` en el dict camelCase de API."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "action": row["action"],
        "actor": row["actor"],
        "target": row["target"],
        "payload": json_loads(row["payload"]),
        "createdAt": row["created_at"],
    }


class EventBus:
    """Acceso de lectura/escritura a los eventos operativos y de auditoría de un proyecto."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def record_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        project_id: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        """Inserta un evento con payload redactado y devuelve la fila persistida ya normalizada."""
        event_id = f"event-{uuid.uuid4()}"
        clean_payload = redact_secrets(payload or {})
        self.connection.execute(
            """
            INSERT INTO events (id, job_id, project_id, type, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, job_id, project_id, event_type, json_dumps(clean_payload), utc_now()),
        )
        row = self.connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return row_to_event(row)

    def list_events(
        self,
        project_id: str | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lista eventos (todos o por proyecto) ordenados del más reciente al más antiguo."""
        limit_sql = ""
        params: list[Any] = []
        if limit is not None:
            if limit < 1:
                raise ValueError("Event list limit must be positive.")
            limit_sql = " LIMIT ?"
        if project_id:
            params.append(project_id)
            if limit is not None:
                params.append(limit)
            rows = self.connection.execute(
                f"SELECT * FROM events WHERE project_id = ? ORDER BY created_at DESC, rowid DESC{limit_sql}",
                params,
            ).fetchall()
        else:
            if limit is not None:
                params.append(limit)
            rows = self.connection.execute(
                f"SELECT * FROM events ORDER BY created_at DESC, rowid DESC{limit_sql}",
                params,
            ).fetchall()
        return [row_to_event(row) for row in rows]

    def record_audit(
        self,
        *,
        action: str,
        target: str,
        payload: dict[str, Any] | None = None,
        project_id: str | None = None,
        actor: str = "system",
    ) -> dict[str, Any]:
        """Inserta un evento de auditoría con payload redactado y devuelve la fila normalizada."""
        audit_id = f"audit-{uuid.uuid4()}"
        clean_payload = redact_secrets(payload or {})
        self.connection.execute(
            """
            INSERT INTO audit_events (id, project_id, action, actor, target, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (audit_id, project_id, action, actor, target, json_dumps(clean_payload), utc_now()),
        )
        row = self.connection.execute("SELECT * FROM audit_events WHERE id = ?", (audit_id,)).fetchone()
        return row_to_audit(row)

    def list_audit_events(
        self,
        project_id: str | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lista eventos de auditoría (todos o por proyecto) del más reciente al más antiguo."""
        limit_sql = ""
        params: list[Any] = []
        if limit is not None:
            if limit < 1:
                raise ValueError("Audit event list limit must be positive.")
            limit_sql = " LIMIT ?"
        if project_id:
            params.append(project_id)
            if limit is not None:
                params.append(limit)
            rows = self.connection.execute(
                f"SELECT * FROM audit_events WHERE project_id = ? ORDER BY created_at DESC, rowid DESC{limit_sql}",
                params,
            ).fetchall()
        else:
            if limit is not None:
                params.append(limit)
            rows = self.connection.execute(
                f"SELECT * FROM audit_events ORDER BY created_at DESC, rowid DESC{limit_sql}",
                params,
            ).fetchall()
        return [row_to_audit(row) for row in rows]
