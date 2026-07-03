"""SQLite repository for remediation actions surfaced to the UI.

Transacciones: cada método usa la conexión SQLite del caller; inserciones y cambios de estado se
confirman por statement salvo que el caller los envuelva en ``immediate_transaction``.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.remediations.contracts import (
    BLOCKER_TYPES,
    REMEDIATION_ACTION_TYPES,
    REMEDIATION_STATUSES,
)
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def row_to_remediation(row: sqlite3.Row) -> dict[str, Any]:
    """Map a remediation row to the public camelCase contract."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "threadId": row["thread_id"],
        "loopId": row["loop_id"],
        "stage": row["stage"],
        "blockerType": row["blocker_type"],
        "title": redact_secrets(row["title"]),
        "description": redact_secrets(row["description"]),
        "actionType": row["action_type"],
        "payload": redact_secrets(json_loads(row["payload_json"], {})),
        "status": row["status"],
        "createdAt": row["created_at"],
        "resolvedAt": row["resolved_at"],
    }


class RemediationActionsRepository:
    """Persistence boundary for user-repairable blocker actions."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_action(
        self,
        *,
        project_id: str,
        thread_id: str | None,
        loop_id: str | None,
        stage: str,
        blocker_type: str,
        title: str,
        description: str,
        action_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert a pending action, idempotent for the same pending blocker/action pair."""
        if blocker_type not in BLOCKER_TYPES:
            raise ValueError(f"Unknown blocker type: {blocker_type}")
        if action_type not in REMEDIATION_ACTION_TYPES:
            raise ValueError(f"Unknown remediation action type: {action_type}")

        clean_thread_id = str(thread_id or "")
        clean_loop_id = str(loop_id or "")
        existing = self.connection.execute(
            """
            SELECT *
            FROM remediation_actions
            WHERE project_id = ?
              AND thread_id = ?
              AND loop_id = ?
              AND stage = ?
              AND blocker_type = ?
              AND action_type = ?
              AND status = 'pending'
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (project_id, clean_thread_id, clean_loop_id, stage, blocker_type, action_type),
        ).fetchone()
        if existing:
            return row_to_remediation(existing)

        action_id = f"remediation-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO remediation_actions
                (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
                 action_type, payload_json, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
            """,
            (
                action_id,
                project_id,
                clean_thread_id,
                clean_loop_id,
                str(stage or ""),
                blocker_type,
                str(redact_secrets(title or "")).strip(),
                str(redact_secrets(description or "")).strip(),
                action_type,
                json_dumps(redact_secrets(payload or {})),
                timestamp,
            ),
        )
        return self.get(action_id)

    def list_for_thread(self, thread_id: str) -> list[dict[str, Any]]:
        """List all remediation actions for a thread, newest last for stable UI ordering."""
        rows = self.connection.execute(
            """
            SELECT *
            FROM remediation_actions
            WHERE thread_id = ?
            ORDER BY created_at ASC, rowid ASC
            """,
            (thread_id,),
        ).fetchall()
        return [row_to_remediation(row) for row in rows]

    def get(self, action_id: str) -> dict[str, Any]:
        """Return one remediation action or raise ``KeyError``."""
        row = self.connection.execute("SELECT * FROM remediation_actions WHERE id = ?", (action_id,)).fetchone()
        if not row:
            raise KeyError(f"Remediation action not found: {action_id}")
        return row_to_remediation(row)

    def mark_status(self, action_id: str, status: str) -> dict[str, Any]:
        """Update a remediation lifecycle status."""
        if status not in REMEDIATION_STATUSES:
            raise ValueError(f"Unknown remediation status: {status}")
        resolved_at = utc_now() if status in {"resolved", "dismissed", "failed"} else None
        with immediate_transaction(self.connection):
            updated = self.connection.execute(
                """
                UPDATE remediation_actions
                SET status = ?, resolved_at = ?
                WHERE id = ?
                """,
                (status, resolved_at, action_id),
            )
            if updated.rowcount == 0:
                raise KeyError(f"Remediation action not found: {action_id}")
        return self.get(action_id)
