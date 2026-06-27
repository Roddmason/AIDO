"""SQLite persistence for AIDO self-improvement proposals, lessons and performance records.

The slice stores durable coordination records only. It links to existing project, backlog,
workspace, workflow, evidence and approval records by ID instead of duplicating their payloads.
The caller owns the transaction boundary: repository methods reuse the supplied connection and do
not commit or roll back.
No method edits the running source tree or promotes lessons into global memory directly.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def new_proposal_id() -> str:
    """Return a stable self-improvement proposal id."""
    return f"self-improvement-proposal-{uuid.uuid4()}"


def row_to_self_improvement_proposal(row: sqlite3.Row) -> dict[str, Any]:
    """Map a proposal row to the public camelCase contract."""
    return {
        "id": row["id"],
        "selfProjectId": row["self_project_id"],
        "sourceProjectId": row["source_project_id"],
        "title": row["title"],
        "summary": row["summary"],
        "status": row["status"],
        "goalLoopId": row["goal_loop_id"],
        "epicId": row["epic_id"],
        "storyId": row["story_id"],
        "taskId": row["task_id"],
        "workspaceId": row["workspace_id"],
        "workflowId": row["workflow_id"],
        "targetPaths": json_loads(row["target_paths"], []),
        "qaCommands": json_loads(row["qa_commands"], []),
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_self_improvement_lesson(row: sqlite3.Row) -> dict[str, Any]:
    """Map a lesson row to the public camelCase contract."""
    return {
        "id": row["id"],
        "selfProjectId": row["self_project_id"],
        "sourceProjectId": row["source_project_id"],
        "proposalId": row["proposal_id"],
        "scope": row["scope"],
        "title": row["title"],
        "lesson": row["lesson"],
        "status": row["status"],
        "promotionStatus": row["promotion_status"],
        "evidencePackageIds": json_loads(row["evidence_package_ids"], []),
        "promotionJobId": row["promotion_job_id"],
        "promotionActionRequestId": row["promotion_action_request_id"],
        "approvedActionRequestId": row["approved_action_request_id"],
        "promotedBy": row["promoted_by"],
        "promotedAt": row["promoted_at"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_self_improvement_performance_record(row: sqlite3.Row) -> dict[str, Any]:
    """Map a performance row to the public camelCase contract."""
    return {
        "id": row["id"],
        "selfProjectId": row["self_project_id"],
        "sourceProjectId": row["source_project_id"],
        "proposalId": row["proposal_id"],
        "metricName": row["metric_name"],
        "value": row["value"],
        "unit": row["unit"],
        "baselineValue": row["baseline_value"],
        "targetValue": row["target_value"],
        "evidencePackageId": row["evidence_package_id"],
        "recordedBy": row["recorded_by"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
    }


class SelfImprovementRepository:
    """Data access for self-improvement coordination records over the caller connection."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_proposal(self, body: dict[str, Any]) -> dict[str, Any]:
        """Insert a self-improvement proposal linked to its generated SDLC records."""
        proposal_id = body.get("id") or new_proposal_id()
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO self_improvement_proposals
                (id, self_project_id, source_project_id, title, summary, status, goal_loop_id,
                 epic_id, story_id, task_id, workspace_id, workflow_id, target_paths, qa_commands,
                 metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal_id,
                body["selfProjectId"],
                body["sourceProjectId"],
                str(redact_secrets(body["title"])),
                str(redact_secrets(body.get("summary", ""))),
                body.get("status", "queued_for_pr"),
                body["goalLoopId"],
                body["epicId"],
                body["storyId"],
                body["taskId"],
                body["workspaceId"],
                body["workflowId"],
                json_dumps(redact_secrets(body.get("targetPaths") or [])),
                json_dumps(redact_secrets(body.get("qaCommands") or [])),
                json_dumps(redact_secrets(body.get("metadata") or {})),
                timestamp,
                timestamp,
            ),
        )
        return self.get_proposal(proposal_id)

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        """Fetch one proposal by id."""
        row = self.connection.execute(
            "SELECT * FROM self_improvement_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Self-improvement proposal not found: {proposal_id}")
        return row_to_self_improvement_proposal(row)

    def list_proposals(self, source_project_id: str | None = None) -> list[dict[str, Any]]:
        """List proposals newest-first, optionally for one source project."""
        if source_project_id:
            rows = self.connection.execute(
                """
                SELECT * FROM self_improvement_proposals
                WHERE source_project_id = ?
                ORDER BY created_at DESC
                """,
                (source_project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM self_improvement_proposals ORDER BY created_at DESC"
            ).fetchall()
        return [row_to_self_improvement_proposal(row) for row in rows]

    def create_lesson(self, body: dict[str, Any]) -> dict[str, Any]:
        """Insert a lesson. Global lessons start pending promotion, never promoted."""
        lesson_id = f"self-improvement-lesson-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO self_improvement_lessons
                (id, self_project_id, source_project_id, proposal_id, scope, title, lesson,
                 status, promotion_status, evidence_package_ids, promotion_job_id,
                 promotion_action_request_id, approved_action_request_id, promoted_by, promoted_at,
                 metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', '', '', NULL, ?, ?, ?)
            """,
            (
                lesson_id,
                body["selfProjectId"],
                body["sourceProjectId"],
                body.get("proposalId"),
                body["scope"],
                str(redact_secrets(body["title"])),
                str(redact_secrets(body["lesson"])),
                body.get("status", "recorded"),
                body.get("promotionStatus", "not_requested"),
                json_dumps([str(item) for item in body.get("evidencePackageIds") or []]),
                json_dumps(redact_secrets(body.get("metadata") or {})),
                timestamp,
                timestamp,
            ),
        )
        return self.get_lesson(lesson_id)

    def get_lesson(self, lesson_id: str) -> dict[str, Any]:
        """Fetch a lesson by id."""
        row = self.connection.execute(
            "SELECT * FROM self_improvement_lessons WHERE id = ?", (lesson_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Self-improvement lesson not found: {lesson_id}")
        return row_to_self_improvement_lesson(row)

    def update_lesson_promotion_request(
        self, lesson_id: str, *, job_id: str, action_request_id: str
    ) -> dict[str, Any]:
        """Attach the approval job/action request generated for a global lesson."""
        self.get_lesson(lesson_id)
        self.connection.execute(
            """
            UPDATE self_improvement_lessons
            SET promotion_job_id = ?, promotion_action_request_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (job_id, action_request_id, utc_now(), lesson_id),
        )
        return self.get_lesson(lesson_id)

    def promote_lesson(
        self, lesson_id: str, *, actor: str, approved_action_request_id: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """Mark a global lesson promoted after approval verification by the coordinator."""
        self.get_lesson(lesson_id)
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE self_improvement_lessons
            SET status = 'promoted',
                promotion_status = 'promoted',
                approved_action_request_id = ?,
                promoted_by = ?,
                promoted_at = ?,
                metadata = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                approved_action_request_id,
                actor,
                timestamp,
                json_dumps(redact_secrets(metadata)),
                timestamp,
                lesson_id,
            ),
        )
        return self.get_lesson(lesson_id)

    def list_lessons(self, source_project_id: str | None = None) -> list[dict[str, Any]]:
        """List lessons newest-first, optionally for one source project."""
        if source_project_id:
            rows = self.connection.execute(
                """
                SELECT * FROM self_improvement_lessons
                WHERE source_project_id = ?
                ORDER BY created_at DESC
                """,
                (source_project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM self_improvement_lessons ORDER BY created_at DESC"
            ).fetchall()
        return [row_to_self_improvement_lesson(row) for row in rows]

    def create_performance_record(self, body: dict[str, Any]) -> dict[str, Any]:
        """Insert a measured performance record with an evidence package link."""
        record_id = f"self-improvement-performance-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO self_improvement_performance_records
                (id, self_project_id, source_project_id, proposal_id, metric_name, value, unit,
                 baseline_value, target_value, evidence_package_id, recorded_by, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                body["selfProjectId"],
                body["sourceProjectId"],
                body.get("proposalId"),
                body["metricName"],
                float(body["value"]),
                body.get("unit", ""),
                body.get("baselineValue"),
                body.get("targetValue"),
                body["evidencePackageId"],
                body.get("recordedBy", "operator"),
                json_dumps(redact_secrets(body.get("metadata") or {})),
                timestamp,
            ),
        )
        return self.get_performance_record(record_id)

    def get_performance_record(self, record_id: str) -> dict[str, Any]:
        """Fetch one performance record by id."""
        row = self.connection.execute(
            "SELECT * FROM self_improvement_performance_records WHERE id = ?", (record_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Self-improvement performance record not found: {record_id}")
        return row_to_self_improvement_performance_record(row)

    def list_performance_records(self, source_project_id: str | None = None) -> list[dict[str, Any]]:
        """List performance records newest-first, optionally for one source project."""
        if source_project_id:
            rows = self.connection.execute(
                """
                SELECT * FROM self_improvement_performance_records
                WHERE source_project_id = ?
                ORDER BY created_at DESC
                """,
                (source_project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM self_improvement_performance_records ORDER BY created_at DESC"
            ).fetchall()
        return [row_to_self_improvement_performance_record(row) for row in rows]
