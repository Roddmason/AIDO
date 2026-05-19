from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.evidence.artifacts import promote_large_git_patches
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.event_bus import EventBus

from .cleanup import capture_workspace_snapshot
from .git_worktrees import capture_git_diff
from .repository import WorkspaceConflictError, WorkspacesRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> WorkspacesRepository:
        return WorkspacesRepository(platform.connection, root=platform.cwd)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

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
        event_bus().record_event(
            project_id=workspace["projectId"],
            event_type="workspace.created",
            payload={"workspaceId": workspace["id"], "taskId": workspace["taskId"], "agentId": workspace["ownerAgentId"]},
        )
        return {"workspace": workspace}

    @router.post("/api/v1/workspaces/{workspace_id}/archive", status_code=202)
    async def archive_workspace(workspace_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        repo = repository()
        pre_archive = repo.get_workspace(workspace_id)
        diff_refs = []
        if pre_archive["isolationType"] == "git_worktree":
            diff_refs.append(capture_git_diff(Path(pre_archive["path"])))
        diff_refs.append(capture_workspace_snapshot(pre_archive["path"]))
        workspace = repo.archive_workspace(workspace_id, reason=body.get("reason", ""))
        diff_refs, artifact_specs = promote_large_git_patches(root=platform.cwd, diff_refs=diff_refs)
        evidence_repo = EvidenceRepository(platform.connection)
        evidence = evidence_repo.create_evidence_package(
            project_id=workspace["projectId"],
            workflow_run_id=workspace.get("workflowRunId"),
            agent_id=workspace.get("ownerAgentId"),
            task_id=workspace["taskId"],
            test_plan="Workspace archive snapshot",
            diff_refs=diff_refs,
            logs=[{"event": "workspace.archived", "reason": body.get("reason", "")}],
            qa_verdict="evidence_collected",
        )
        for artifact in artifact_specs:
            evidence_repo.create_artifact(
                project_id=workspace["projectId"],
                evidence_package_id=evidence["id"],
                kind=artifact["kind"],
                path=artifact["path"],
                content_hash=artifact["hash"],
                metadata=artifact["metadata"],
                artifact_id=artifact["id"],
            )
        event_bus().record_event(
            project_id=workspace["projectId"],
            event_type="workspace.archived",
            payload={
                "workspaceId": workspace["id"],
                "reason": body.get("reason", ""),
                "evidencePackageId": evidence["id"],
            },
        )
        event_bus().record_event(
            project_id=workspace["projectId"],
            event_type="qa.evidence.created",
            payload={"evidencePackageId": evidence["id"], "qaVerdict": evidence["qaVerdict"]},
        )
        return {"workspace": workspace, "evidencePackage": evidence}

    return router
