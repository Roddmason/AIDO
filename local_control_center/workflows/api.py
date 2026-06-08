from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..agents.model_router import ModelRouter, RoutingRequest
from ..agents.routing_profiles import RoutingProfileStore
from ..agents.repository import AgentsRepository
from ..evidence.repository import EvidenceRepository
from ..evidence.quality import qa_passed_without_failed_results
from ..governance.repository import GovernanceRepository
from ..governance.signals import record_governance_risk
from ..jobs_approvals.repository import JobsRepository
from ..shared.event_bus import EventBus
from ..shared.time import utc_now
from ..workspaces_projects.repository import WorkspacesRepository
from .models import (
    IssueToPatchRequest,
    IssueToPatchResponse,
    WorkflowCreateRequest,
    WorkflowDetailResponse,
    WorkflowGateAdvanceRequest,
    WorkflowGateAdvanceResponse,
    WorkflowResponse,
    WorkflowsListResponse,
    WorkflowStartResponse,
    WorkflowStatusChangeRequest,
)
from .issue_to_patch_runner import IssueToPatchRunner
from .repository import WorkflowsRepository
from .repository import validate_workflow_metadata


ALLOWED_WORKFLOW_KINDS = {
    "idea_to_pr",
    "project_discovery",
    "issue_to_patch",
    "issue_to_pr",
    "qa_validation",
    "release_candidate",
    "pr_release_retro",
}


CODE_EDIT_STEPS = {"implementation", "pr_creation"}
TOOL_STEPS = {
    "workspace_create",
    "implementation",
    "local_tests",
    "qa_validation",
    "technical_review",
    "pr_creation",
    "pr_review",
    "release_gate",
}
SEARCH_STEPS = {"project_discovery", "backlog_generation"}
REASONING_STEPS = {"architecture_review", "technical_review", "release_candidate", "pr_review", "release_gate", "retro"}


def _manual_override(step: dict[str, Any]) -> dict[str, str]:
    override = step.get("manualModelOverride")
    if not isinstance(override, str) or not override.strip():
        return {}
    parts = [part.strip() for part in override.split("/") if part.strip()]
    if len(parts) == 1:
        return {"manualModel": parts[0]}
    if len(parts) == 2:
        return {"manualProvider": parts[0], "manualModel": parts[1]}
    return {"manualProvider": parts[0], "manualModel": parts[1], "manualRuntime": parts[2]}


def validate_workflow_create_body(body: WorkflowCreateRequest) -> dict[str, Any]:
    payload = body.model_dump(by_alias=True)
    kind = str(payload.get("kind") or "idea_to_pr").strip().lower()
    if kind not in ALLOWED_WORKFLOW_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of: {', '.join(sorted(ALLOWED_WORKFLOW_KINDS))}.",
        )
    title = str(payload.get("title") or payload.get("idea") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="Workflow title is required.")
    if len(title) > 180:
        raise HTTPException(status_code=422, detail="Workflow title must be 180 characters or fewer.")
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=422, detail="metadata must be an object.")
    try:
        validate_workflow_metadata(metadata)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"project_id": payload["projectId"], "kind": kind, "title": title, "metadata": metadata}


def validate_issue_to_patch_body(body: IssueToPatchRequest) -> dict[str, Any]:
    payload = body.model_dump(by_alias=True)
    title = str(payload.get("title") or "").strip()
    issue_text = str(payload.get("issueText") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="Issue title is required.")
    if len(title) > 180:
        raise HTTPException(status_code=422, detail="Issue title must be 180 characters or fewer.")
    if not issue_text:
        raise HTTPException(status_code=422, detail="issueText is required.")
    qa_commands = payload.get("qaCommands") or []
    for index, argv in enumerate(qa_commands):
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise HTTPException(status_code=422, detail=f"qaCommands[{index}] must be a non-empty structured argv list.")
    return payload


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> WorkflowsRepository:
        return WorkflowsRepository(platform.connection)

    def workspaces() -> WorkspacesRepository:
        return WorkspacesRepository(platform.connection, root=platform.cwd)

    def evidence() -> EvidenceRepository:
        return EvidenceRepository(platform.connection)

    def jobs() -> JobsRepository:
        return JobsRepository(platform.connection)

    def governance() -> GovernanceRepository:
        return GovernanceRepository(platform.connection)

    def agents() -> AgentsRepository:
        return AgentsRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    def route_started_workflow_steps(result: dict[str, Any]) -> None:
        router = ModelRouter(platform.connection)
        profiles = RoutingProfileStore(platform.connection)
        workflow_run_id = result["workflowRun"]["id"]
        for step in result["workflowSteps"]:
            role = step.get("role") or "developer"
            try:
                role_policy = profiles.get_role_policy(role)
                mode = step.get("modelMode") or role_policy["routingProfileId"]
            except KeyError:
                mode = step.get("modelMode") or "balanced_best_value"
            step_name = str(step.get("name") or step.get("taskType") or "workflow_step")
            router.preview(
                RoutingRequest(
                    role=role,
                    taskType=step.get("taskType") or step_name,
                    mode=mode,
                    riskLevel=step.get("riskLevel") or "medium",
                    contextTokensEstimate=12000 if step_name in REASONING_STEPS else 4000,
                    requiresCodeEdit=step_name in CODE_EDIT_STEPS,
                    requiresTools=step_name in TOOL_STEPS,
                    requiresSearch=step_name in SEARCH_STEPS,
                    requiresReasoning=step_name in REASONING_STEPS,
                    privacyLevel="remote_allowed",
                    workflowRunId=workflow_run_id,
                    workflowStepId=step["id"],
                    taskId=step_name,
                    **_manual_override(step),
                ),
                record=True,
            )

    def record_gate_result(step: dict[str, Any], *, suffix: str, payload: dict[str, Any], severity: str = "info") -> None:
        action = f"workflow.gate.{step['name']}.{suffix}"
        repository().record_workflow_event(
            workflow_id=step["workflowId"],
            workflow_run_id=step["workflowRunId"],
            step_id=step["id"],
            project_id=step["projectId"],
            event_type=action,
            payload=payload,
            severity=severity,
        )
        event_bus().record_audit(
            project_id=step["projectId"],
            action=action,
            actor="system",
            target=step["id"],
            payload=payload,
        )

    def block_gate(step: dict[str, Any], detail: str, *, payload: dict[str, Any]) -> None:
        record_gate_result(step, suffix="blocked", payload={"reason": detail, **payload}, severity="warning")
        raise HTTPException(status_code=409, detail=detail)

    def passed_qa_evidence_for_step(step: dict[str, Any], evidence_package_id: str | None) -> dict[str, Any] | None:
        packages = evidence().list_evidence_for_workflow_runs([step["workflowRunId"]])
        for package in packages:
            if evidence_package_id and package["id"] != evidence_package_id:
                continue
            if qa_passed_without_failed_results(package):
                return package
        return None

    def pending_release_actions_for_step(step: dict[str, Any]) -> list[dict[str, Any]]:
        release_jobs = [
            job
            for job in jobs().list_jobs_for_workflow_runs([step["workflowRunId"]])
            if job["workflowStepId"] == step["id"] and job["kind"] == "release.production"
        ]
        pending: list[dict[str, Any]] = []
        for job in release_jobs:
            pending.extend([action for action in jobs().list_action_requests(job["id"]) if action["status"] == "pending"])
        return pending

    def has_retro_governance_records(step: dict[str, Any]) -> bool:
        records = [
            *governance().list_architecture_decisions(step["projectId"]),
            *governance().list_risks(step["projectId"]),
            *governance().list_next_steps(step["projectId"]),
        ]
        return any(
            record.get("metadata", {}).get("sourceType") == "workflow_retro"
            and record.get("metadata", {}).get("workflowStepId") == step["id"]
            for record in records
        )

    @router.get("/api/v1/workflows", response_model=WorkflowsListResponse)
    async def list_workflows() -> dict[str, Any]:
        repo = repository()
        return {
            "workflows": repo.list_workflows(),
            "workflowRuns": repo.list_workflow_runs(),
            "workflowSteps": repo.list_workflow_steps(),
        }

    @router.post("/api/v1/workflows", status_code=201, response_model=WorkflowResponse)
    async def create_workflow(body: WorkflowCreateRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        validated = validate_workflow_create_body(body)
        workflow = repository().create_workflow(
            project_id=validated["project_id"],
            kind=validated["kind"],
            title=validated["title"],
            metadata=validated["metadata"],
        )
        event_bus().record_event(project_id=workflow["projectId"], event_type="workflow.created", payload={"workflowId": workflow["id"]})
        return {"workflow": workflow}

    @router.post("/api/v1/workflows/issue-to-patch", status_code=202, response_model=IssueToPatchResponse)
    async def run_issue_to_patch(body: IssueToPatchRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        payload = validate_issue_to_patch_body(body)
        try:
            result = IssueToPatchRunner(platform.connection, root=platform.cwd).run(payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workflow"]["projectId"],
            event_type=f"workflow.issue_to_patch.{result['status']}",
            payload={
                "workflowId": result["workflow"]["id"],
                "workflowRunId": result["workflowRun"]["id"],
                "runtimeId": result["runtime"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
            },
        )
        return result

    @router.get("/api/v1/workflows/{workflow_id}", response_model=WorkflowDetailResponse)
    async def get_workflow(workflow_id: str) -> dict[str, Any]:
        repo = repository()
        try:
            workflow_runs = repo.list_workflow_runs(workflow_id=workflow_id)
            workflow_run_ids = [run["id"] for run in workflow_runs]
            workflow_steps = [
                step
                for run_id in workflow_run_ids
                for step in repo.list_workflow_steps(workflow_run_id=run_id)
            ]
            runtime_workspaces = workspaces().list_workspaces_for_workflow(workflow_run_ids)
            evidence_packages = evidence().list_evidence_for_workflow_runs(workflow_run_ids)
            workflow_jobs = jobs().list_jobs_for_workflow_runs(workflow_run_ids)
            agent_runs = agents().list_agent_runs_for_workflow_runs(workflow_run_ids)
            workflow_run_details = [
                {
                    "workflowRun": run,
                    "workflowSteps": [step for step in workflow_steps if step["workflowRunId"] == run["id"]],
                    "workspaces": [workspace for workspace in runtime_workspaces if workspace["workflowRunId"] == run["id"]],
                    "evidencePackages": [
                        package for package in evidence_packages if package["workflowRunId"] == run["id"]
                    ],
                    "jobs": [job for job in workflow_jobs if job["workflowRunId"] == run["id"]],
                    "agentRuns": [agent_run for agent_run in agent_runs if agent_run["workflowRunId"] == run["id"]],
                }
                for run in workflow_runs
            ]
            return {
                "workflow": repo.get_workflow(workflow_id),
                "workflowRuns": workflow_runs,
                "workflowSteps": workflow_steps,
                "workflowRunDetails": workflow_run_details,
                "workspaces": runtime_workspaces,
                "evidencePackages": evidence_packages,
                "jobs": workflow_jobs,
                "agentRuns": agent_runs,
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/api/v1/workflows/{workflow_id}/start", status_code=202, response_model=WorkflowStartResponse)
    async def start_workflow(workflow_id: str, body: WorkflowStatusChangeRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        result = repository().start_workflow(workflow_id, reason=body.reason)
        route_started_workflow_steps(result)
        event_bus().record_event(
            project_id=result["workflow"]["projectId"],
            event_type="workflow.started",
            payload={
                "workflowId": workflow_id,
                "workflowRunId": result["workflowRun"]["id"],
                "routingDecisionCount": len(result["workflowSteps"]),
            },
        )
        return result

    @router.post(
        "/api/v1/workflows/{workflow_id}/steps/{step_id}/advance",
        status_code=202,
        response_model=WorkflowGateAdvanceResponse,
    )
    async def advance_workflow_gate(
        workflow_id: str,
        step_id: str,
        body: WorkflowGateAdvanceRequest,
        request: Request,
    ) -> dict[str, Any]:
        require_write(request)
        repo = repository()
        try:
            step = repo.get_workflow_step(step_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if step["workflowId"] != workflow_id:
            raise HTTPException(status_code=404, detail=f"Workflow step not found for workflow: {step_id}")
        if step["name"] not in {"pr_review", "release_gate", "retro"}:
            raise HTTPException(status_code=422, detail=f"Workflow step {step['name']} is not a governed gate.")

        reason = body.reason or "Advance governed workflow gate."
        if step["name"] == "pr_review":
            package = passed_qa_evidence_for_step(step, body.evidence_package_id)
            if not package:
                block_gate(
                    step,
                    "pr_review requires passed QA evidence for this workflow run.",
                    payload={"evidencePackageId": body.evidence_package_id},
                )
            metadata = {
                **step["metadata"],
                "gateState": "evidence_satisfied",
                "evidencePackageId": package["id"],
                "advancedAt": utc_now(),
            }
            updated = repo.update_workflow_step(
                step_id,
                status="completed",
                metadata=metadata,
                output={"advanced": True, "evidencePackageId": package["id"], "reason": reason},
            )
            record_gate_result(
                updated,
                suffix="advanced",
                payload={"evidencePackageId": package["id"], "reason": reason},
            )
            return {"workflowStep": updated, "advanced": True, "gateState": metadata["gateState"], "reason": reason}

        if step["name"] == "release_gate":
            pending_actions = pending_release_actions_for_step(step)
            if pending_actions:
                block_gate(
                    step,
                    "release_gate requires approved production release action requests.",
                    payload={"pendingActionRequestIds": [item["id"] for item in pending_actions]},
                )
            metadata = {
                **step["metadata"],
                "gateState": "approval_satisfied",
                "advancedAt": utc_now(),
            }
            updated = repo.update_workflow_step(
                step_id,
                status="completed",
                metadata=metadata,
                output={"advanced": True, "reason": reason},
            )
            record_gate_result(updated, suffix="advanced", payload={"reason": reason})
            return {"workflowStep": updated, "advanced": True, "gateState": metadata["gateState"], "reason": reason}

        if not has_retro_governance_records(step):
            block_gate(
                step,
                "retro requires governance records seeded for this workflow step.",
                payload={},
            )
        metadata = {
            **step["metadata"],
            "gateState": "retro_recorded",
            "advancedAt": utc_now(),
        }
        updated = repo.update_workflow_step(
            step_id,
            status="completed",
            metadata=metadata,
            output={"advanced": True, "reason": reason},
        )
        record_gate_result(updated, suffix="advanced", payload={"reason": reason})
        return {"workflowStep": updated, "advanced": True, "gateState": metadata["gateState"], "reason": reason}

    @router.post("/api/v1/workflows/{workflow_id}/pause", status_code=202, response_model=WorkflowResponse)
    async def pause_workflow(workflow_id: str, body: WorkflowStatusChangeRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        workflow = repository().update_workflow_status(workflow_id, status="paused", reason=body.reason)
        event_bus().record_event(project_id=workflow["projectId"], event_type="workflow.paused", payload={"workflowId": workflow_id})
        return {"workflow": workflow}

    @router.post("/api/v1/workflows/{workflow_id}/resume", status_code=202, response_model=WorkflowResponse)
    async def resume_workflow(workflow_id: str, body: WorkflowStatusChangeRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        workflow = repository().update_workflow_status(workflow_id, status="running", reason=body.reason)
        event_bus().record_event(project_id=workflow["projectId"], event_type="workflow.resumed", payload={"workflowId": workflow_id})
        return {"workflow": workflow}

    @router.post("/api/v1/workflows/{workflow_id}/cancel", status_code=202, response_model=WorkflowResponse)
    async def cancel_workflow(workflow_id: str, body: WorkflowStatusChangeRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        workflow = repository().update_workflow_status(workflow_id, status="cancelled", reason=body.reason)
        event_bus().record_event(project_id=workflow["projectId"], event_type="workflow.cancelled", payload={"workflowId": workflow_id})
        risk = record_governance_risk(
            platform.connection,
            project_id=workflow["projectId"],
            title=f"Workflow cancelled: {workflow['title']}",
            source_type="workflow_status",
            source_id=workflow["id"],
            severity="medium",
            description=body.reason or "Workflow was cancelled before completion.",
            mitigation="Review workflow events, open approvals, and evidence gaps before retrying.",
            owner="technical_lead",
            metadata={"status": workflow["status"]},
        )
        if risk:
            event_bus().record_event(
                project_id=risk["projectId"],
                event_type="risk.created",
                payload={"riskId": risk["id"], "sourceType": "workflow_status"},
            )
        return {"workflow": workflow}

    return router
