"""SQLite repository for remediation actions surfaced to the UI.

Transacciones: cada método usa la conexión SQLite del caller; inserciones y cambios de estado se
confirman por statement salvo que el caller los envuelva en ``immediate_transaction``.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.remediations.compaction import compact_remediation_payload
from local_control_center.remediations.contracts import (
    BLOCKER_TYPES,
    REMEDIATION_ACTION_TYPES,
    REMEDIATION_STATUSES,
)
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

DISPLAY_DETAILS_LIMIT_BYTES = 64 * 1024


def row_to_remediation(row: sqlite3.Row) -> dict[str, Any]:
    """Map a remediation row to the public camelCase contract."""
    keys = set(row.keys())
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
        "technicalReason": redact_secrets(row["technical_reason"]) if "technical_reason" in keys else "",
        "primary": bool(row["is_primary"]) if "is_primary" in keys else False,
        "destructive": bool(row["is_destructive"]) if "is_destructive" in keys else False,
        "confirmationRequired": bool(row["confirmation_required"])
        if "confirmation_required" in keys
        else False,
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
        technical_reason: str = "",
        primary: bool = False,
        destructive: bool = False,
        confirmation_required: bool = False,
    ) -> dict[str, Any]:
        """Insert a pending action, idempotent for the same pending blocker/action pair."""
        if blocker_type not in BLOCKER_TYPES:
            raise ValueError(f"Unknown blocker type: {blocker_type}")
        if action_type not in REMEDIATION_ACTION_TYPES:
            raise ValueError(f"Unknown remediation action type: {action_type}")

        clean_payload = redact_secrets(payload or {})
        clean_title = str(redact_secrets(title or "")).strip()
        clean_description = str(redact_secrets(description or "")).strip()
        clean_reason = str(redact_secrets(technical_reason or "")).strip()
        clean_thread_id = str(thread_id or "")
        clean_loop_id = str(loop_id or "")
        existing_rows = self.connection.execute(
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
            """,
            (project_id, clean_thread_id, clean_loop_id, stage, blocker_type, action_type),
        ).fetchall()

        def job_id(payload: dict[str, Any]) -> str:
            details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
            return str(payload.get("jobId") or details.get("jobId") or "").strip()

        def same_action_identity(row: sqlite3.Row) -> bool:
            existing_payload = json_loads(row["payload_json"], {})
            existing_job = job_id(existing_payload)
            if not clean_loop_id and existing_job and existing_job != job_id(clean_payload):
                return False
            if action_type == "open_settings_section":
                return (
                    str(existing_payload.get("section") or "").strip()
                    == str(clean_payload.get("section") or "").strip()
                )
            if action_type == "answer_question":
                return (
                    str(existing_payload.get("decisionId") or "").strip()
                    == str(clean_payload.get("decisionId") or "").strip()
                )
            return True

        existing = next((row for row in existing_rows if same_action_identity(row)), None)
        if existing:
            existing_payload = json_loads(existing["payload_json"], {})
            merged_payload = {**existing_payload, **clean_payload}
            self.connection.execute(
                """
                UPDATE remediation_actions
                SET title = ?,
                    description = ?,
                    payload_json = ?,
                    technical_reason = ?,
                    is_primary = ?,
                    is_destructive = ?,
                    confirmation_required = ?
                WHERE id = ?
                """,
                (
                    clean_title or existing["title"],
                    clean_description or existing["description"],
                    json_dumps(compact_remediation_payload(redact_secrets(merged_payload))),
                    clean_reason or existing["technical_reason"],
                    1 if primary or bool(existing["is_primary"]) else 0,
                    1 if destructive or bool(existing["is_destructive"]) else 0,
                    1 if confirmation_required or bool(existing["confirmation_required"]) else 0,
                    existing["id"],
                ),
            )
            return self.get(existing["id"])

        action_id = f"remediation-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO remediation_actions
                (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
                 action_type, payload_json, technical_reason, is_primary, is_destructive,
                 confirmation_required, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
            """,
            (
                action_id,
                project_id,
                clean_thread_id,
                clean_loop_id,
                str(stage or ""),
                blocker_type,
                clean_title,
                clean_description,
                action_type,
                json_dumps(compact_remediation_payload(clean_payload)),
                clean_reason,
                1 if primary else 0,
                1 if destructive else 0,
                1 if confirmation_required else 0,
                timestamp,
            ),
        )
        return self.get(action_id)

    def list_for_thread(self, thread_id: str, *, summary: bool = False) -> list[dict[str, Any]]:
        """List every action in stable order, optionally projecting large display-only evidence."""
        if summary:
            # Project inside SQLite: Python must never parse/redact the omitted evidence on display reads.
            rows = self.connection.execute(
                """
                SELECT id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
                       action_type, technical_reason, is_primary, is_destructive, confirmation_required,
                       status, created_at, resolved_at,
                       CASE WHEN json_valid(payload_json) THEN
                            CASE WHEN length(CAST(json_extract(payload_json, '$.details') AS BLOB)) > ?
                                 THEN json_set(json_remove(payload_json, '$.details'), '$.detailsEvidence',
                                               json_object('actionId', id, 'stored', json('true')))
                                 ELSE payload_json END
                            ELSE payload_json END AS payload_json
                FROM remediation_actions
                WHERE thread_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (DISPLAY_DETAILS_LIMIT_BYTES, thread_id),
            ).fetchall()
            return [row_to_remediation(row) for row in rows]
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

    def latest_loop_id_for_thread(self, *, project_id: str, thread_id: str) -> str | None:
        """Identify the current run by creation order, never by edits to an older loop."""
        row = self.connection.execute(
            """
            SELECT id FROM product_loops
            WHERE project_id = ?
              AND COALESCE(NULLIF(json_extract(context, '$.durableRun.thread.projectThreadId'), ''),
                           json_extract(context, '$.fsm.correlationId')) = ?
            ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (project_id, thread_id),
        ).fetchone()
        return str(row["id"]) if row else None

    def dismiss_pending_from_superseded_loops(
        self, thread_id: str, *, include_actions: bool = True
    ) -> list[dict[str, Any]]:
        """Archive old blocked-run repairs without resolving their causes or manual decisions."""
        with immediate_transaction(self.connection):
            thread = self.connection.execute(
                "SELECT project_id FROM project_threads WHERE id = ?", (thread_id,)
            ).fetchone()
            if thread is None:
                return []
            current = self.latest_loop_id_for_thread(project_id=thread["project_id"], thread_id=thread_id)
            if current is None:
                return []
            rows = self.connection.execute(
                """
                SELECT remediation_actions.id FROM remediation_actions
                INNER JOIN product_loops ON product_loops.id = remediation_actions.loop_id
                WHERE remediation_actions.thread_id = ? AND remediation_actions.project_id = ?
                  AND product_loops.project_id = remediation_actions.project_id
                  AND COALESCE(NULLIF(json_extract(product_loops.context, '$.durableRun.thread.projectThreadId'), ''),
                               json_extract(product_loops.context, '$.fsm.correlationId')) = remediation_actions.thread_id
                  AND product_loops.state = 'blocked' AND product_loops.id <> ?
                  AND remediation_actions.status = 'pending'
                  AND remediation_actions.action_type NOT IN
                      ('answer_question', 'approve_resource_decision', 'continue_plan_only')
                ORDER BY remediation_actions.created_at ASC, remediation_actions.rowid ASC
                """,
                (thread_id, thread["project_id"], current),
            ).fetchall()
            action_ids = [str(row["id"]) for row in rows]
            self.connection.executemany(
                "UPDATE remediation_actions SET status = 'dismissed', resolved_at = ? "
                "WHERE id = ? AND status = 'pending'",
                [(utc_now(), action_id) for action_id in action_ids],
            )
        return [self.get(action_id) for action_id in action_ids] if include_actions else []

    def resolve_pending_for_loop(self, loop_id: str) -> list[dict[str, Any]]:
        """Resolve every pending action owned by one terminal Product Loop, preserving audit rows."""
        clean_loop_id = str(loop_id or "").strip()
        if not clean_loop_id:
            return []
        with immediate_transaction(self.connection):
            action_ids = self.resolve_pending_for_loop_in_transaction(clean_loop_id)
        return [self.get(action_id) for action_id in action_ids]

    def resolve_pending_for_loop_in_transaction(self, loop_id: str) -> list[str]:
        """Resolve a terminal loop's actions inside the caller's existing SQLite transaction."""
        if not self.connection.in_transaction:
            raise RuntimeError("resolve_pending_for_loop_in_transaction requires an active transaction.")
        clean_loop_id = str(loop_id or "").strip()
        if not clean_loop_id:
            return []
        rows = self.connection.execute(
            """
            SELECT remediation_actions.id
            FROM remediation_actions
            INNER JOIN product_loops ON product_loops.id = remediation_actions.loop_id
            WHERE remediation_actions.loop_id = ?
              AND remediation_actions.status = 'pending'
              AND product_loops.state IN ('cancelled', 'delivered')
            ORDER BY remediation_actions.created_at ASC, remediation_actions.rowid ASC
            """,
            (clean_loop_id,),
        ).fetchall()
        action_ids = [str(row["id"]) for row in rows]
        if action_ids:
            self.connection.execute(
                """
                UPDATE remediation_actions
                SET status = 'resolved', resolved_at = ?
                WHERE loop_id = ?
                  AND status = 'pending'
                  AND loop_id IN (
                      SELECT id
                      FROM product_loops
                      WHERE state IN ('cancelled', 'delivered')
                  )
                """,
                (utc_now(), clean_loop_id),
            )
        return action_ids

    def resolve_pending_for_blocker_in_transaction(
        self,
        loop_id: str,
        *,
        stage: str,
        blocker_type: str,
    ) -> list[str]:
        """Resolve one superseded loop blocker family inside the caller's transaction."""
        if not self.connection.in_transaction:
            raise RuntimeError("resolve_pending_for_blocker_in_transaction requires an active transaction.")
        clean_loop_id = str(loop_id or "").strip()
        clean_stage = str(stage or "").strip()
        clean_blocker_type = str(blocker_type or "").strip()
        if not clean_loop_id or not clean_stage or not clean_blocker_type:
            return []
        rows = self.connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE loop_id = ?
              AND stage = ?
              AND blocker_type = ?
              AND status = 'pending'
            ORDER BY created_at ASC, rowid ASC
            """,
            (clean_loop_id, clean_stage, clean_blocker_type),
        ).fetchall()
        action_ids = [str(row["id"]) for row in rows]
        if action_ids:
            self.connection.execute(
                """
                UPDATE remediation_actions
                SET status = 'resolved', resolved_at = ?
                WHERE loop_id = ?
                  AND stage = ?
                  AND blocker_type = ?
                  AND status = 'pending'
                """,
                (utc_now(), clean_loop_id, clean_stage, clean_blocker_type),
            )
        return action_ids

    def resolve_pending_from_terminal_loops(
        self, thread_id: str, *, include_actions: bool = True
    ) -> list[dict[str, Any]]:
        """Reconcile legacy pending rows whose Product Loop is already cancelled or delivered."""
        clean_thread_id = str(thread_id or "").strip()
        if not clean_thread_id:
            return []
        rows = self.connection.execute(
            """
            SELECT remediation_actions.id
            FROM remediation_actions
            INNER JOIN product_loops ON product_loops.id = remediation_actions.loop_id
            WHERE remediation_actions.thread_id = ?
              AND remediation_actions.status = 'pending'
              AND product_loops.state IN ('cancelled', 'delivered')
            ORDER BY remediation_actions.created_at ASC, remediation_actions.rowid ASC
            """,
            (clean_thread_id,),
        ).fetchall()
        action_ids = [str(row["id"]) for row in rows]
        if not action_ids:
            return []
        with immediate_transaction(self.connection):
            self.connection.execute(
                """
                UPDATE remediation_actions
                SET status = 'resolved', resolved_at = ?
                WHERE thread_id = ?
                  AND status = 'pending'
                  AND loop_id IN (
                      SELECT id
                      FROM product_loops
                      WHERE state IN ('cancelled', 'delivered')
                  )
                """,
                (utc_now(), clean_thread_id),
            )
        return [self.get(action_id) for action_id in action_ids] if include_actions else []

    def resolve_pending_from_terminal_jobs(
        self, thread_id: str, *, include_actions: bool = True
    ) -> list[dict[str, Any]]:
        """Close worker preflight repairs for cancelled/completed jobs, retaining audit history."""
        clean_thread_id = str(thread_id or "").strip()
        if not clean_thread_id:
            return []
        with immediate_transaction(self.connection):
            rows = self.connection.execute(
                """
                SELECT remediation_actions.id
                FROM remediation_actions
                INNER JOIN jobs ON jobs.id = COALESCE(
                    NULLIF(json_extract(remediation_actions.payload_json, '$.jobId'), ''),
                    json_extract(remediation_actions.payload_json, '$.details.jobId')
                )
                WHERE remediation_actions.thread_id = ?
                  AND remediation_actions.loop_id = ''
                  AND remediation_actions.stage IN ('runtime', 'gitleaks', 'worker')
                  AND remediation_actions.status = 'pending'
                  AND jobs.status IN ('cancelled', 'completed')
                  AND jobs.project_id = remediation_actions.project_id
                  AND json_extract(jobs.payload, '$.threadId') = remediation_actions.thread_id
                ORDER BY remediation_actions.created_at ASC, remediation_actions.rowid ASC
                """,
                (clean_thread_id,),
            ).fetchall()
            action_ids = [str(row["id"]) for row in rows]
            self.connection.executemany(
                "UPDATE remediation_actions SET status = 'resolved', resolved_at = ? "
                "WHERE id = ? AND status = 'pending'",
                [(utc_now(), action_id) for action_id in action_ids],
            )
        return [self.get(action_id) for action_id in action_ids] if include_actions else []

    def get(self, action_id: str) -> dict[str, Any]:
        """Return one remediation action or raise ``KeyError``."""
        row = self.connection.execute(
            "SELECT * FROM remediation_actions WHERE id = ?", (action_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Remediation action not found: {action_id}")
        return row_to_remediation(row)

    def mark_status(self, action_id: str, status: str) -> dict[str, Any]:
        """Update a remediation lifecycle status."""
        with immediate_transaction(self.connection):
            return self.mark_status_in_transaction(action_id, status)

    def mark_status_in_transaction(self, action_id: str, status: str) -> dict[str, Any]:
        """Update a remediation lifecycle status inside the caller's active transaction."""
        if not self.connection.in_transaction:
            raise RuntimeError("mark_status_in_transaction requires an active transaction.")
        if status not in REMEDIATION_STATUSES:
            raise ValueError(f"Unknown remediation status: {status}")
        resolved_at = utc_now() if status in {"resolved", "dismissed", "failed"} else None
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

    def merge_payload(self, action_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Fusiona ``patch`` (claves de primer nivel, redactado) en el payload persistido de la acción."""
        with immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT payload_json FROM remediation_actions WHERE id = ?", (action_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"Remediation action not found: {action_id}")
            merged = {**json_loads(row["payload_json"], {}), **redact_secrets(patch)}
            self.connection.execute(
                "UPDATE remediation_actions SET payload_json = ? WHERE id = ?",
                (json_dumps(merged), action_id),
            )
        return self.get(action_id)
