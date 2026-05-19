from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..agents.repository import AgentsRepository
from ..evidence.repository import EvidenceRepository
from ..governance.signals import record_governance_risk
from ..jobs_approvals.repository import JobsRepository
from ..shared.event_bus import EventBus
from ..workspaces_projects.repository import WorkspacesRepository
from .repository import WorkflowsRepository


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

    @router.get("/api/v1/workflows")
    async def list_workflows() -> dict[str, Any]:
        repo = repository()
        return {
            "workflows": repo.list_workflows(),
            "workflowRuns": repo.list_workflow_runs(),
            "workflowSteps": repo.list_workflow_steps(),
        }

    @router.post("/api/v1/workflows", status_code=201)
    async def create_workflow(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        workflow = repository().create_workflow(
            project_id=body["projectId"],
            kind=body.get("kind", "idea_to_pr"),
            title=body.get("title") or body.get("idea") or "Untitled workflow",
            metadata=body.get("metadata") or {},
        )
        event_bus().record_event(project_id=workflow["projectId"], event_type="workflow.created", payload={"workflowId": workflow["id"]})
        return {"workflow": workflow}

    @router.get("/api/v1/workflows/{workflow_id}")
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

    @router.post("/api/v1/workflows/{workflow_id}/start", status_code=202)
    async def start_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        result = repository().start_workflow(workflow_id, reason=body.get("reason", ""))
        event_bus().record_event(
            project_id=result["workflow"]["projectId"],
            event_type="workflow.started",
            payload={"workflowId": workflow_id, "workflowRunId": result["workflowRun"]["id"]},
        )
        return result

    @router.post("/api/v1/workflows/{workflow_id}/pause", status_code=202)
    async def pause_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        workflow = repository().update_workflow_status(workflow_id, status="paused", reason=body.get("reason", ""))
        event_bus().record_event(project_id=workflow["projectId"], event_type="workflow.paused", payload={"workflowId": workflow_id})
        return {"workflow": workflow}

    @router.post("/api/v1/workflows/{workflow_id}/resume", status_code=202)
    async def resume_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        workflow = repository().update_workflow_status(workflow_id, status="running", reason=body.get("reason", ""))
        event_bus().record_event(project_id=workflow["projectId"], event_type="workflow.resumed", payload={"workflowId": workflow_id})
        return {"workflow": workflow}

    @router.post("/api/v1/workflows/{workflow_id}/cancel", status_code=202)
    async def cancel_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        workflow = repository().update_workflow_status(workflow_id, status="cancelled", reason=body.get("reason", ""))
        event_bus().record_event(project_id=workflow["projectId"], event_type="workflow.cancelled", payload={"workflowId": workflow_id})
        risk = record_governance_risk(
            platform.connection,
            project_id=workflow["projectId"],
            title=f"Workflow cancelled: {workflow['title']}",
            source_type="workflow_status",
            source_id=workflow["id"],
            severity="medium",
            description=body.get("reason", "Workflow was cancelled before completion."),
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
