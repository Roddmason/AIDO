from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .repository import WorkspaceConflictError, WorkspacesRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> WorkspacesRepository:
        return WorkspacesRepository(platform.connection, root=platform.cwd)

    @router.get("/api/v1/workspaces")
    async def list_workspaces() -> dict[str, Any]:
        return {"workspaces": repository().list_workspaces()}

    @router.post("/api/v1/workspaces", status_code=201)
    async def allocate_workspace(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        try:
            workspace = repository().allocate_workspace(
                project_id=body["projectId"],
                task_id=body["taskId"],
                agent_id=body["agentId"],
                reason=body.get("reason", ""),
                isolation_type=body.get("isolationType", "directory"),
                workflow_run_id=body.get("workflowRunId"),
                workflow_step_id=body.get("workflowStepId"),
                base_branch=body.get("baseBranch", "HEAD"),
            )
        except WorkspaceConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        platform.record_event(
            project_id=workspace["projectId"],
            event_type="workspace.created",
            payload={"workspaceId": workspace["id"], "taskId": workspace["taskId"], "agentId": workspace["ownerAgentId"]},
        )
        return {"workspace": workspace}

    @router.post("/api/v1/workspaces/{workspace_id}/archive", status_code=202)
    async def archive_workspace(workspace_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        workspace = repository().archive_workspace(workspace_id, reason=body.get("reason", ""))
        platform.record_event(
            project_id=workspace["projectId"],
            event_type="workspace.archived",
            payload={"workspaceId": workspace["id"], "reason": body.get("reason", "")},
        )
        return {"workspace": workspace}

    return router
