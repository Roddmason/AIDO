from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from local_control_center.store import utc_now


DEFAULT_WORKFLOW_STEPS = [
    "idea_intake",
    "project_discovery",
    "backlog_generation",
    "architecture_review",
    "sprint_plan",
    "workspace_create",
    "implementation",
    "local_tests",
    "qa_validation",
    "technical_review",
    "pr_creation",
    "release_candidate",
]


def json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, fallback: Any = None) -> Any:
    if value in (None, ""):
        return {} if fallback is None else fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {} if fallback is None else fallback


def row_to_workflow(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "kind": row["kind"],
        "title": row["title"],
        "status": row["status"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_workflow_run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workflowId": row["workflow_id"],
        "projectId": row["project_id"],
        "status": row["status"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "metadata": json_loads(row["metadata"]),
    }


def row_to_workflow_step(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workflowRunId": row["workflow_run_id"],
        "workflowId": row["workflow_id"],
        "projectId": row["project_id"],
        "name": row["name"],
        "status": row["status"],
        "agentProfileId": row["agent_profile_id"],
        "input": json_loads(row["input"]),
        "output": json_loads(row["output"]),
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class WorkflowsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_workflow(self, *, project_id: str, kind: str, title: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        workflow_id = f"workflow-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO workflows (id, project_id, kind, title, status, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)
            """,
            (workflow_id, project_id, kind, title, json_dumps(metadata or {}), timestamp, timestamp),
        )
        return self.get_workflow(workflow_id)

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        if not row:
            raise KeyError(f"Workflow not found: {workflow_id}")
        return row_to_workflow(row)

    def list_workflows(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM workflows WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM workflows ORDER BY created_at DESC").fetchall()
        return [row_to_workflow(row) for row in rows]

    def start_workflow(self, workflow_id: str, *, reason: str = "") -> dict[str, Any]:
        workflow = self.get_workflow(workflow_id)
        timestamp = utc_now()
        run_id = f"workflow-run-{uuid.uuid4()}"
        self.connection.execute(
            "UPDATE workflows SET status = 'running', updated_at = ? WHERE id = ?",
            (timestamp, workflow_id),
        )
        self.connection.execute(
            """
            INSERT INTO workflow_runs (id, workflow_id, project_id, status, started_at, completed_at, metadata)
            VALUES (?, ?, ?, 'running', ?, NULL, ?)
            """,
            (run_id, workflow_id, workflow["projectId"], timestamp, json_dumps({"reason": reason})),
        )
        for index, step_name in enumerate(DEFAULT_WORKFLOW_STEPS):
            self.connection.execute(
                """
                INSERT INTO workflow_steps
                    (id, workflow_run_id, workflow_id, project_id, name, status, agent_profile_id,
                     input, output, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    f"workflow-step-{uuid.uuid4()}",
                    run_id,
                    workflow_id,
                    workflow["projectId"],
                    step_name,
                    "ready" if index == 0 else "pending",
                    json_dumps({}),
                    json_dumps({}),
                    json_dumps({"order": index}),
                    timestamp,
                    timestamp,
                ),
            )
        self.record_workflow_event(
            workflow_id=workflow_id,
            workflow_run_id=run_id,
            project_id=workflow["projectId"],
            event_type="workflow.started",
            payload={"reason": reason},
        )
        return {
            "workflow": self.get_workflow(workflow_id),
            "workflowRun": self.get_workflow_run(run_id),
            "workflowSteps": self.list_workflow_steps(workflow_run_id=run_id),
        }

    def update_workflow_status(self, workflow_id: str, *, status: str, reason: str = "") -> dict[str, Any]:
        workflow = self.get_workflow(workflow_id)
        timestamp = utc_now()
        self.connection.execute(
            "UPDATE workflows SET status = ?, updated_at = ? WHERE id = ?",
            (status, timestamp, workflow_id),
        )
        self.record_workflow_event(
            workflow_id=workflow_id,
            workflow_run_id=None,
            project_id=workflow["projectId"],
            event_type=f"workflow.{status}",
            payload={"reason": reason},
        )
        return self.get_workflow(workflow_id)

    def get_workflow_run(self, run_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"Workflow run not found: {run_id}")
        return row_to_workflow_run(row)

    def list_workflow_runs(self, workflow_id: str | None = None) -> list[dict[str, Any]]:
        if workflow_id:
            rows = self.connection.execute(
                "SELECT * FROM workflow_runs WHERE workflow_id = ? ORDER BY started_at DESC",
                (workflow_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM workflow_runs ORDER BY started_at DESC").fetchall()
        return [row_to_workflow_run(row) for row in rows]

    def list_workflow_steps(self, workflow_run_id: str | None = None) -> list[dict[str, Any]]:
        if workflow_run_id:
            rows = self.connection.execute(
                "SELECT * FROM workflow_steps WHERE workflow_run_id = ? ORDER BY created_at ASC",
                (workflow_run_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM workflow_steps ORDER BY created_at ASC").fetchall()
        return [row_to_workflow_step(row) for row in rows]

    def record_workflow_event(
        self,
        *,
        workflow_id: str,
        workflow_run_id: str | None,
        project_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> dict[str, Any]:
        event_id = f"workflow-event-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO workflow_events
                (id, workflow_id, workflow_run_id, step_id, project_id, type, payload, severity,
                 created_at, correlation_id, causation_id)
            VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (event_id, workflow_id, workflow_run_id, project_id, event_type, json_dumps(payload or {}), severity, utc_now()),
        )
        row = self.connection.execute("SELECT * FROM workflow_events WHERE id = ?", (event_id,)).fetchone()
        return {
            "id": row["id"],
            "workflowId": row["workflow_id"],
            "workflowRunId": row["workflow_run_id"],
            "projectId": row["project_id"],
            "type": row["type"],
            "payload": json_loads(row["payload"]),
            "severity": row["severity"],
            "createdAt": row["created_at"],
        }
