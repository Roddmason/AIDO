from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from local_control_center.store import utc_now


def json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, fallback: Any = None) -> Any:
    if value in (None, ""):
        return {} if fallback is None else fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {} if fallback is None else fallback


def row_to_evidence_package(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "workflowRunId": row["workflow_run_id"],
        "agentId": row["agent_id"],
        "taskId": row["task_id"],
        "testPlan": row["test_plan"],
        "acceptanceChecklist": json_loads(row["acceptance_checklist"], []),
        "testResults": json_loads(row["test_results"], []),
        "logs": json_loads(row["logs"], []),
        "diffRefs": json_loads(row["diff_refs"], []),
        "screenshotRefs": json_loads(row["screenshot_refs"], []),
        "riskNotes": json_loads(row["risk_notes"], []),
        "qaVerdict": row["qa_verdict"],
        "createdAt": row["created_at"],
    }


class EvidenceRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_evidence_package(
        self,
        *,
        project_id: str,
        workflow_run_id: str | None,
        agent_id: str | None,
        task_id: str,
        test_plan: str,
        acceptance_checklist: list[Any] | None = None,
        test_results: list[Any] | None = None,
        logs: list[Any] | None = None,
        diff_refs: list[Any] | None = None,
        screenshot_refs: list[Any] | None = None,
        risk_notes: list[Any] | None = None,
        qa_verdict: str = "not_started",
    ) -> dict[str, Any]:
        evidence_id = f"evidence-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO evidence_packages
                (id, project_id, workflow_run_id, agent_id, task_id, test_plan,
                 acceptance_checklist, test_results, logs, diff_refs, screenshot_refs,
                 risk_notes, qa_verdict, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                project_id,
                workflow_run_id,
                agent_id,
                task_id,
                test_plan,
                json_dumps(acceptance_checklist or []),
                json_dumps(test_results or []),
                json_dumps(logs or []),
                json_dumps(diff_refs or []),
                json_dumps(screenshot_refs or []),
                json_dumps(risk_notes or []),
                qa_verdict,
                utc_now(),
            ),
        )
        return self.get_evidence_package(evidence_id)

    def get_evidence_package(self, evidence_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM evidence_packages WHERE id = ?", (evidence_id,)).fetchone()
        if not row:
            raise KeyError(f"Evidence package not found: {evidence_id}")
        return row_to_evidence_package(row)

    def list_evidence_packages(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM evidence_packages WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM evidence_packages ORDER BY created_at DESC").fetchall()
        return [row_to_evidence_package(row) for row in rows]
