from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..agents.model_router import ModelRouter, RoutingRequest
from ..agents.routing_profiles import RoutingProfileStore
from ..agents.repository import AgentsRepository
from ..evidence.repository import EvidenceRepository
from ..governance.signals import record_governance_risk
from ..jobs_approvals.repository import JobsRepository
from ..shared.event_bus import EventBus
from ..workspaces_projects.repository import WorkspacesRepository
from .models import (
    WorkflowCreateRequest,
    WorkflowDetailResponse,
    WorkflowResponse,
    WorkflowsListResponse,
    WorkflowStartResponse,
    WorkflowStatusChangeRequest,
)
from .repository import WorkflowsRepository


ALLOWED_WORKFLOW_KINDS = {
    "idea_to_pr",
    "project_discovery",
    "issue_to_pr",
    "qa_validation",
    "release_candidate",
}


CODE_EDIT_STEPS = {"implementation", "pr_creation"}
TOOL_STEPS = {"workspace_create", "implementation", "local_tests", "qa_validation", "technical_review", "pr_creation"}
SEARCH_STEPS = {"project_discovery", "backlog_generation"}
REASONING_STEPS = {"architecture_review", "technical_review", "release_candidate"}


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
    return {"project_id": payload["projectId"], "kind": kind, "title": title, "metadata": metadata}


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

    @router.get("/api/v1/workflows/{workflow_id}", response_model=WorkflowDetailResponse)
    async def get_workflow(workflow_id: str) -> dict[str, Any]:
        repo = repository()
        try:
            workflow_runs = repo.list_workflow_runs(workflow_id=workflow_id)
            workflow_run_ids = [run["id"] for run in workflow_runs]
            return {
                "workflow": repo.get_workflow(workflow_id),
                "workflowRuns": workflow_runs,
                "workflowSteps": [
                    step
                    for run_id in workflow_run_ids
                    for step in repo.list_workflow_steps(workflow_run_id=run_id)
                ],
                "workspaces": workspaces().list_workspaces_for_workflow(workflow_run_ids),
                "evidencePackages": evidence().list_evidence_for_workflow_runs(workflow_run_ids),
                "jobs": jobs().list_jobs_for_workflow_runs(workflow_run_ids),
                "agentRuns": agents().list_agent_runs_for_workflow_runs(workflow_run_ids),
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
