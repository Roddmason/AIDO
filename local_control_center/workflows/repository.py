"""SQLite persistence and governance seeding for workflows, runs, steps and events.

Owns the workflow tables and the rules that keep runs auditable: it validates declared
metadata (rejecting force-push and direct-main operations), seeds the step graph on start,
and materializes release-control gates (pr_review evidence, production approval jobs, retro
governance records) as side effects of starting a run.

Transactions: every write uses the caller-supplied ``connection`` and never commits;
the caller owns the commit/rollback boundary. ``start_workflow`` performs a multi-statement
unit of work (workflow status UPDATE + run INSERT + one INSERT per step + gate side effects)
that is only durable if the caller commits, so a failed start leaves no partial run behind.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.governance.repository import GovernanceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

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

RELEASE_CONTROL_STEPS = {"pr_review", "release_gate", "retro"}
ISSUE_TO_PATCH_STEPS = [
    "workspace_create",
    "implementation",
    "local_tests",
    "qa_validation",
    "technical_review",
]
ISSUE_TO_PR_STEPS = [
    "developer_agent",
    "qa_validation",
    "security_review",
    "architecture_review",
    "devops_validation",
    "evidence_aggregation",
    "approval",
    "branch_promotion",
    "pr_creation",
]
ALLOWED_DECLARED_STEP_NAMES = set(DEFAULT_WORKFLOW_STEPS) | RELEASE_CONTROL_STEPS | set(ISSUE_TO_PR_STEPS)
BLOCKED_MAIN_OPERATIONS = {
    "commit_to_main",
    "direct_main_edit",
    "direct_push",
    "edit_main",
    "push",
    "push_to_main",
}


def _is_truthy(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "y"})


def _step_name(spec: Any) -> str:
    if isinstance(spec, str):
        return spec.strip()
    if isinstance(spec, dict):
        return str(spec.get("name") or "").strip()
    return ""


def _assert_release_safety(spec: dict[str, Any], *, location: str) -> None:
    if any(_is_truthy(spec.get(key)) for key in ("forcePush", "force_push", "forcePushEnabled")):
        raise ValueError(f"{location}: force push is not allowed in governed workflows.")
    command = str(spec.get("command") or "").lower()
    if "push --force" in command or "push -f" in command or "--force-with-lease" in command:
        raise ValueError(f"{location}: force push is not allowed in governed workflows.")

    branch = (
        str(spec.get("branch") or spec.get("targetBranch") or spec.get("baseBranch") or "").strip().lower()
    )
    operation = str(spec.get("operation") or spec.get("action") or "").strip().lower()
    direct_main = _is_truthy(spec.get("directMainEdit")) or _is_truthy(spec.get("direct_main_edit"))
    if branch in {"main", "master"} and (direct_main or operation in BLOCKED_MAIN_OPERATIONS):
        raise ValueError(f"{location}: direct main edits are not allowed; use PR review and release gates.")

    nested_metadata = spec.get("metadata")
    if isinstance(nested_metadata, dict):
        _assert_release_safety(nested_metadata, location=f"{location}.metadata")


def validate_workflow_metadata(metadata: dict[str, Any]) -> None:
    """Reject unsafe or malformed workflow metadata before it can seed a run.

    Enforces release safety (no force push, no direct main edits) on the metadata and any
    declared step, and bounds the declared step list to known names, a non-empty list and at
    most 32 entries with object-typed input/output/metadata fields.

    Raises:
        ValueError: on any violated invariant, with a location-prefixed message.
    """
    _assert_release_safety(metadata, location="metadata")
    steps = metadata.get("steps")
    if steps is None:
        return
    if not isinstance(steps, list) or not steps:
        raise ValueError("metadata.steps must be a non-empty list when provided.")
    if len(steps) > 32:
        raise ValueError("metadata.steps supports at most 32 declared workflow steps.")
    for index, raw_step in enumerate(steps):
        name = _step_name(raw_step)
        if name not in ALLOWED_DECLARED_STEP_NAMES:
            raise ValueError(f"metadata.steps[{index}].name must be a known workflow step.")
        if isinstance(raw_step, dict):
            _assert_release_safety(raw_step, location=f"metadata.steps[{index}]")
            for field in ("input", "output", "metadata"):
                if (
                    field in raw_step
                    and raw_step[field] is not None
                    and not isinstance(raw_step[field], dict)
                ):
                    raise ValueError(f"metadata.steps[{index}].{field} must be an object.")


def _normalize_declared_steps(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    steps = metadata.get("steps")
    if steps is None:
        return [{"name": name} for name in DEFAULT_WORKFLOW_STEPS]
    validate_workflow_metadata(metadata)
    normalized: list[dict[str, Any]] = []
    for raw_step in steps:
        if isinstance(raw_step, str):
            normalized.append({"name": raw_step.strip()})
        else:
            normalized.append(dict(raw_step))
    return normalized


def row_to_workflow(row: sqlite3.Row) -> dict[str, Any]:
    """Map a ``workflows`` row to the camelCase API dict, parsing JSON metadata."""
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
    """Map a ``workflow_runs`` row to the camelCase API dict, parsing JSON metadata."""
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
    """Map a ``workflow_steps`` row to the API dict, tolerating older schemas without the routing columns."""
    return {
        "id": row["id"],
        "workflowRunId": row["workflow_run_id"],
        "workflowId": row["workflow_id"],
        "projectId": row["project_id"],
        "name": row["name"],
        "status": row["status"],
        "agentProfileId": row["agent_profile_id"],
        "role": row["role"] if "role" in row.keys() else None,
        "taskType": row["task_type"] if "task_type" in row.keys() else None,
        "riskLevel": row["risk_level"] if "risk_level" in row.keys() else None,
        "modelMode": row["model_mode"] if "model_mode" in row.keys() else None,
        "manualModelOverride": row["manual_model_override"]
        if "manual_model_override" in row.keys()
        else None,
        "input": json_loads(row["input"]),
        "output": json_loads(row["output"]),
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_workflow_event(row: sqlite3.Row) -> dict[str, Any]:
    """Map a ``workflow_events`` row to the API dict, parsing JSON payload and correlation ids."""
    return {
        "id": row["id"],
        "workflowId": row["workflow_id"],
        "workflowRunId": row["workflow_run_id"],
        "workflowStepId": row["step_id"],
        "projectId": row["project_id"],
        "type": row["type"],
        "payload": json_loads(row["payload"]),
        "severity": row["severity"],
        "createdAt": row["created_at"],
        "correlationId": row["correlation_id"],
        "causationId": row["causation_id"],
    }


class WorkflowsRepository:
    """Data-access layer for workflows, runs, steps and events over a shared SQLite connection.

    Reads and writes use the injected ``connection`` and never commit; the caller controls the
    transaction. Several methods also emit timeline events as part of the same unit of work.
    """

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_workflow(
        self, *, project_id: str, kind: str, title: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Insert a queued workflow and return it; metadata defaults to an empty object."""
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
        """Fetch a workflow by id.

        Raises:
            KeyError: if no workflow has that id.
        """
        row = self.connection.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        if not row:
            raise KeyError(f"Workflow not found: {workflow_id}")
        return row_to_workflow(row)

    def list_workflows(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """List workflows newest-first, optionally scoped to one project."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM workflows WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM workflows ORDER BY created_at DESC").fetchall()
        return [row_to_workflow(row) for row in rows]

    def start_workflow(self, workflow_id: str, *, reason: str = "") -> dict[str, Any]:
        """Create a run, mark the workflow running and seed its steps and release-control gates.

        Single unit of work on the caller's connection: flips the workflow to ``running``,
        inserts a run, inserts one step per declared step (the first ``ready``, the rest
        ``pending``), seeds gate metadata for pr_review/release_gate/retro, and records the
        ``workflow.started`` event. Durable only if the caller commits.

        Raises:
            ValueError: if the stored metadata fails ``validate_workflow_metadata``.
        """
        workflow = self.get_workflow(workflow_id)
        validate_workflow_metadata(workflow.get("metadata") or {})
        step_specs = _normalize_declared_steps(workflow.get("metadata") or {})
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
        for index, step_spec in enumerate(step_specs):
            step_name = str(step_spec.get("name") or "").strip()
            step_id = f"workflow-step-{uuid.uuid4()}"
            step_metadata = {
                **(step_spec.get("metadata") or {}),
                "order": index,
            }
            if step_name == "pr_review":
                step_metadata = {
                    **step_metadata,
                    "requiresEvidence": True,
                    "requiredEvidence": "qa_passed",
                    "gateState": "blocked_pending_qa_evidence",
                }
            if (
                step_name == "release_gate"
                and str(step_spec.get("environment") or "").strip().lower() == "production"
            ):
                step_metadata = {
                    **step_metadata,
                    "environment": "production",
                    "requiresApproval": True,
                    "gateState": "blocked_pending_human_approval",
                }
            if step_name == "retro":
                step_metadata = {
                    **step_metadata,
                    "gateState": "governance_seeded",
                }
            self.connection.execute(
                """
                INSERT INTO workflow_steps
                    (id, workflow_run_id, workflow_id, project_id, name, status, agent_profile_id,
                     role, task_type, risk_level, model_mode, manual_model_override,
                     input, output, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    run_id,
                    workflow_id,
                    workflow["projectId"],
                    step_name,
                    "ready" if index == 0 else "pending",
                    str(step_spec.get("role") or self._default_role_for_step(step_name)),
                    str(step_spec.get("taskType") or step_name),
                    str(step_spec.get("riskLevel") or "medium"),
                    step_spec.get("modelMode"),
                    step_spec.get("manualModelOverride"),
                    json_dumps(step_spec.get("input") or {}),
                    json_dumps(step_spec.get("output") or {}),
                    json_dumps(step_metadata),
                    timestamp,
                    timestamp,
                ),
            )
            self._record_release_control_gate(
                workflow=workflow,
                workflow_run_id=run_id,
                step_id=step_id,
                step_name=step_name,
                step_spec=step_spec,
                step_metadata=step_metadata,
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

    def _default_role_for_step(self, step_name: str) -> str:
        return {
            "idea_intake": "product_owner",
            "project_discovery": "analyst",
            "backlog_generation": "analyst",
            "architecture_review": "technical_lead",
            "sprint_plan": "product_owner",
            "workspace_create": "developer",
            "implementation": "developer",
            "developer_agent": "developer",
            "local_tests": "qa",
            "qa_validation": "qa",
            "security_review": "security_reviewer",
            "technical_review": "technical_lead",
            "devops_validation": "release_manager",
            "evidence_aggregation": "technical_lead",
            "approval": "technical_lead",
            "branch_promotion": "release_manager",
            "pr_creation": "developer",
            "release_candidate": "release_manager",
            "pr_review": "technical_lead",
            "release_gate": "release_manager",
            "retro": "technical_lead",
        }.get(step_name, "developer")

    def _record_release_control_gate(
        self,
        *,
        workflow: dict[str, Any],
        workflow_run_id: str,
        step_id: str,
        step_name: str,
        step_spec: dict[str, Any],
        step_metadata: dict[str, Any],
    ) -> None:
        if step_name == "pr_review":
            payload = {
                "workflowId": workflow["id"],
                "workflowRunId": workflow_run_id,
                "workflowStepId": step_id,
                "requiredEvidence": step_metadata["requiredEvidence"],
            }
            self.record_workflow_event(
                workflow_id=workflow["id"],
                workflow_run_id=workflow_run_id,
                step_id=step_id,
                project_id=workflow["projectId"],
                event_type="workflow.gate.pr_review.evidence_required",
                payload=payload,
                severity="warning",
            )
            EventBus(self.connection).record_audit(
                project_id=workflow["projectId"],
                action="workflow.gate.pr_review.evidence_required",
                actor="system",
                target=step_id,
                payload=payload,
            )
            return

        if step_name == "release_gate" and step_metadata.get("environment") == "production":
            job_result = JobsRepository(self.connection).create_job(
                project_id=workflow["projectId"],
                kind="release.production",
                status="approval_required",
                workflow_run_id=workflow_run_id,
                workflow_step_id=step_id,
                payload={
                    "workflowId": workflow["id"],
                    "workflowRunId": workflow_run_id,
                    "workflowStepId": step_id,
                    "environment": "production",
                    "approvalRequired": True,
                    "reason": "Production release requires explicit human approval.",
                },
            )
            payload = {
                "workflowId": workflow["id"],
                "workflowRunId": workflow_run_id,
                "workflowStepId": step_id,
                "environment": "production",
                "jobId": job_result["job"]["id"],
                "actionRequestIds": [item["id"] for item in job_result["actionRequests"]],
            }
            self.record_workflow_event(
                workflow_id=workflow["id"],
                workflow_run_id=workflow_run_id,
                step_id=step_id,
                project_id=workflow["projectId"],
                event_type="workflow.gate.release_gate.approval_required",
                payload=payload,
                severity="warning",
            )
            EventBus(self.connection).record_audit(
                project_id=workflow["projectId"],
                action="workflow.gate.release_gate.approval_required",
                actor="system",
                target=step_id,
                payload=payload,
            )
            return

        if step_name == "retro":
            metadata = {
                "sourceType": "workflow_retro",
                "sourceId": step_id,
                "workflowId": workflow["id"],
                "workflowRunId": workflow_run_id,
                "workflowStepId": step_id,
            }
            governance = GovernanceRepository(self.connection)
            risk = governance.create_risk(
                {
                    "projectId": workflow["projectId"],
                    "title": f"Retrospective findings pending: {workflow['title']}",
                    "severity": "low",
                    "status": "open",
                    "description": "The workflow includes a retrospective gate; findings must be captured before closing the release loop.",
                    "mitigation": "Record outcomes, unresolved risks, and follow-up work after the release gate.",
                    "owner": str(step_spec.get("role") or "technical_lead"),
                    "metadata": metadata,
                }
            )
            next_step = governance.create_next_step(
                {
                    "projectId": workflow["projectId"],
                    "title": f"Capture retrospective outcomes: {workflow['title']}",
                    "status": "planned",
                    "priority": "medium",
                    "sourceRiskId": risk["id"],
                    "owner": str(step_spec.get("role") or "technical_lead"),
                    "metadata": metadata,
                }
            )
            decision = governance.create_architecture_decision(
                {
                    "projectId": workflow["projectId"],
                    "title": f"Retrospective control seeded: {workflow['title']}",
                    "status": "proposed",
                    "context": "AIDO release workflows require retrospective records to close the SDLC feedback loop.",
                    "decision": "Create governance records during workflow start so the release cannot become unauditable.",
                    "consequences": ["Retro outcomes remain explicit backlog/governance records."],
                    "linkedRiskIds": [risk["id"]],
                    "nextStepIds": [next_step["id"]],
                    "metadata": metadata,
                }
            )
            payload = {
                **metadata,
                "riskId": risk["id"],
                "nextStepId": next_step["id"],
                "decisionId": decision["id"],
            }
            self.record_workflow_event(
                workflow_id=workflow["id"],
                workflow_run_id=workflow_run_id,
                step_id=step_id,
                project_id=workflow["projectId"],
                event_type="workflow.gate.retro.governance_created",
                payload=payload,
                severity="info",
            )
            EventBus(self.connection).record_audit(
                project_id=workflow["projectId"],
                action="workflow.gate.retro.governance_created",
                actor="system",
                target=step_id,
                payload=payload,
            )

    def update_workflow_status(self, workflow_id: str, *, status: str, reason: str = "") -> dict[str, Any]:
        """Set the workflow status, record a ``workflow.<status>`` event, and return the workflow."""
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

    def update_workflow_run_status(
        self,
        run_id: str,
        *,
        status: str,
        metadata: dict[str, Any] | None = None,
        completed: bool = False,
        clear_completed: bool = False,
    ) -> dict[str, Any]:
        """Update a run's status/metadata and record a ``workflow.run.<status>`` event.

        ``completed`` stamps ``completed_at`` to now; ``clear_completed`` resets it to NULL.
        ``metadata`` left as ``None`` keeps the current value rather than overwriting it.
        """
        current = self.get_workflow_run(run_id)
        next_metadata = current["metadata"] if metadata is None else metadata
        self.connection.execute(
            """
            UPDATE workflow_runs
            SET status = ?,
                completed_at = CASE WHEN ? THEN ? WHEN ? THEN NULL ELSE completed_at END,
                metadata = ?
            WHERE id = ?
            """,
            (
                status,
                1 if completed else 0,
                utc_now(),
                1 if clear_completed else 0,
                json_dumps(next_metadata),
                run_id,
            ),
        )
        self.record_workflow_event(
            workflow_id=current["workflowId"],
            workflow_run_id=run_id,
            project_id=current["projectId"],
            event_type=f"workflow.run.{status}",
            payload={"status": status},
        )
        return self.get_workflow_run(run_id)

    def get_workflow_run(self, run_id: str) -> dict[str, Any]:
        """Fetch a run by id.

        Raises:
            KeyError: if no run has that id.
        """
        row = self.connection.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"Workflow run not found: {run_id}")
        return row_to_workflow_run(row)

    def list_workflow_runs(self, workflow_id: str | None = None) -> list[dict[str, Any]]:
        """List runs newest-first, optionally scoped to one workflow."""
        if workflow_id:
            rows = self.connection.execute(
                "SELECT * FROM workflow_runs WHERE workflow_id = ? ORDER BY started_at DESC",
                (workflow_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM workflow_runs ORDER BY started_at DESC").fetchall()
        return [row_to_workflow_run(row) for row in rows]

    def list_workflow_steps(self, workflow_run_id: str | None = None) -> list[dict[str, Any]]:
        """List steps in creation (execution) order, optionally scoped to one run."""
        if workflow_run_id:
            rows = self.connection.execute(
                "SELECT * FROM workflow_steps WHERE workflow_run_id = ? ORDER BY created_at ASC",
                (workflow_run_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM workflow_steps ORDER BY created_at ASC").fetchall()
        return [row_to_workflow_step(row) for row in rows]

    def list_workflow_events(self, workflow_run_id: str | None = None) -> list[dict[str, Any]]:
        """List events for one run oldest-first, or all events newest-first when no run is given."""
        if workflow_run_id:
            rows = self.connection.execute(
                "SELECT * FROM workflow_events WHERE workflow_run_id = ? ORDER BY created_at ASC",
                (workflow_run_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM workflow_events ORDER BY created_at DESC"
            ).fetchall()
        return [row_to_workflow_event(row) for row in rows]

    def get_workflow_step(self, step_id: str) -> dict[str, Any]:
        """Fetch a step by id.

        Raises:
            KeyError: if no step has that id.
        """
        row = self.connection.execute("SELECT * FROM workflow_steps WHERE id = ?", (step_id,)).fetchone()
        if not row:
            raise KeyError(f"Workflow step not found: {step_id}")
        return row_to_workflow_step(row)

    def update_workflow_step(
        self,
        step_id: str,
        *,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Patch a step's status/metadata/output; any argument left ``None`` keeps its current value."""
        current = self.get_workflow_step(step_id)
        next_status = status or current["status"]
        next_metadata = current["metadata"] if metadata is None else metadata
        next_output = current["output"] if output is None else output
        self.connection.execute(
            """
            UPDATE workflow_steps
            SET status = ?, metadata = ?, output = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_status,
                json_dumps(next_metadata),
                json_dumps(next_output),
                utc_now(),
                step_id,
            ),
        )
        return self.get_workflow_step(step_id)

    def record_workflow_event(
        self,
        *,
        workflow_id: str,
        workflow_run_id: str | None,
        project_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        severity: str = "info",
        step_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a timeline event for the run/step and return it; correlation ids are left null."""
        event_id = f"workflow-event-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO workflow_events
                (id, workflow_id, workflow_run_id, step_id, project_id, type, payload, severity,
                 created_at, correlation_id, causation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                event_id,
                workflow_id,
                workflow_run_id,
                step_id,
                project_id,
                event_type,
                json_dumps(payload or {}),
                severity,
                utc_now(),
            ),
        )
        row = self.connection.execute("SELECT * FROM workflow_events WHERE id = ?", (event_id,)).fetchone()
        return row_to_workflow_event(row)
