"""Router HTTP del slice Git Workspace.

Expone estado Git, ramas, checkout, diff y gitleaks por proyecto. Las mutaciones exigen el
token local y la ejecucion real se delega a ``GitWorkspaceService`` en el worker. Los GET
sólo leen el último snapshot durable y explicitan cuándo requiere renovación.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.executions.router import ExecutionRouter, queued_operation
from local_control_center.shared.serialization import json_loads

from .branch_manager import GitBranchManager
from .branch_manager_models import (
    BranchActionResponse,
    BranchDeleteRequest,
    BranchInventoryResponse,
    BranchPruneRequest,
    BranchRenameRequest,
    BranchScanRequest,
    BranchWorktreeRemoveRequest,
)
from .models import (
    GitBranchCreateRequest,
    GitBranchesResponse,
    GitBranchMutationResponse,
    GitBranchPolicyApplyRequest,
    GitBranchPolicyApplyResponse,
    GitCheckoutRequest,
    GitCheckoutResponse,
    GitDiffResponse,
    GitGitleaksScanResponse,
    GitInitRequest,
    GitInitResponse,
    GitRemoteAddRequest,
    GitRemoteMutationResponse,
    GitRemoteTestRequest,
    GitRemoteTestResponse,
    GitStatusResponse,
)
from .service import GitWorkspaceService, branches_view_from_status

GIT_SNAPSHOT_PATH_PATTERN = re.compile(r"^/api/v1/projects/[^/]+/git/(?:status|branches)$")
GIT_SNAPSHOT_TTL_SECONDS = 30
BRANCH_SCAN_OPERATION = "git_branches.scan"


def is_git_snapshot_request(method: str, path: str) -> bool:
    """Indica si el request es un GET de snapshot Git (status/branches)."""
    return method.upper() == "GET" and GIT_SNAPSHOT_PATH_PATTERN.match(path) is not None


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Arma lecturas durables y mutaciones encoladas por proyecto, sin Git en el proceso API."""
    router = ExecutionRouter(
        platform=platform,
        require_write=require_write,
    )

    def service() -> GitWorkspaceService:
        return GitWorkspaceService(platform.connection, root=Path(platform.cwd))

    def cached_snapshot(project_id: str, key: str) -> dict[str, Any]:
        project = platform.connection.execute(
            "SELECT path FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if project is None:
            raise HTTPException(404, "Project not found.")
        row = platform.connection.execute(
            """SELECT result_json, finished_at FROM operational_executions
            WHERE project_id=? AND operation='git.refresh' AND status='completed'
            ORDER BY finished_at DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
        if row:
            result = json_loads(row["result_json"], {}).get(key)
            if isinstance(result, dict):
                age = (
                    datetime.now(UTC) - datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00"))
                ).total_seconds()
                changed = platform.connection.execute(
                    """SELECT 1 FROM operational_executions WHERE project_id=?
                    AND operation LIKE 'git.%' AND operation!='git.refresh'
                    AND created_at>=? LIMIT 1""",
                    (project_id, row["finished_at"]),
                ).fetchone()
                return {
                    **result,
                    "snapshotAt": row["finished_at"],
                    "refreshRequired": age < 0 or age > GIT_SNAPSHOT_TTL_SECONDS or changed is not None,
                }
        return {
            "status": "configuration_required",
            "reason": "Git snapshot requires POST /git/refresh.",
            "projectId": project_id,
            "workspaceId": "",
            "root": project["path"],
            "diff": "",
            "snapshotAt": None,
            "refreshRequired": True,
        }

    @router.post("/api/v1/projects/{project_id}/git/refresh")
    @queued_operation("git.refresh", workload_class="control_plane")
    async def refresh_git(project_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return {"snapshot": service().status(project_id), "diff": service().diff(project_id)}
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @router.get("/api/v1/projects/{project_id}/git/status", response_model=GitStatusResponse)
    def git_status(project_id: str) -> dict[str, Any]:
        return cached_snapshot(project_id, "snapshot")

    @router.get("/api/v1/projects/{project_id}/git/branches", response_model=GitBranchesResponse)
    async def git_branches(project_id: str) -> dict[str, Any]:
        snapshot = cached_snapshot(project_id, "snapshot")
        return {
            **branches_view_from_status(snapshot, project_id=project_id),
            "snapshotAt": snapshot["snapshotAt"],
            "refreshRequired": snapshot["refreshRequired"],
        }

    @router.post("/api/v1/projects/{project_id}/git/init", response_model=GitInitResponse)
    @queued_operation("git.init_git_repository", workload_class="qa_light")
    async def init_git_repository(project_id: str, body: GitInitRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return service().init_repository(project_id, default_branch=body.default_branch)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/api/v1/projects/{project_id}/git/remotes", response_model=GitRemoteMutationResponse)
    @queued_operation("git.add_git_remote", workload_class="qa_light")
    async def add_git_remote(project_id: str, body: GitRemoteAddRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return service().add_remote(project_id, name=body.name, url=body.url)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/projects/{project_id}/git/remotes/{name}/test",
        response_model=GitRemoteTestResponse,
    )
    @queued_operation("git.test_git_remote", workload_class="qa_light")
    async def test_git_remote(
        project_id: str, name: str, body: GitRemoteTestRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return service().test_remote(project_id, name=name, allow_network=body.allow_network)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/projects/{project_id}/git/branches",
        status_code=201,
        response_model=GitBranchMutationResponse,
    )
    @queued_operation("git.create_git_branch", workload_class="qa_light")
    async def create_git_branch(
        project_id: str, body: GitBranchCreateRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return service().create_branch(project_id, name=body.name, base=body.base)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/projects/{project_id}/git/branch-policy/apply",
        response_model=GitBranchPolicyApplyResponse,
    )
    @queued_operation("git.apply_git_branch_policy", workload_class="qa_light")
    async def apply_git_branch_policy(
        project_id: str, body: GitBranchPolicyApplyRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return service().apply_branch_policy(
                project_id,
                intent=body.intent,
                selected_base=body.selected_base,
                branch_name=body.branch_name,
                create_branch=body.create_branch,
                allow_protected=body.allow_protected,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/api/v1/projects/{project_id}/git/checkout", response_model=GitCheckoutResponse)
    @queued_operation("git.checkout_git_branch", workload_class="qa_light")
    async def checkout_git_branch(
        project_id: str, body: GitCheckoutRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return service().checkout(project_id, branch=body.branch, allow_dirty=body.allow_dirty)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/api/v1/projects/{project_id}/git/diff", response_model=GitDiffResponse)
    async def git_diff(project_id: str) -> dict[str, Any]:
        return cached_snapshot(project_id, "diff")

    @router.post(
        "/api/v1/projects/{project_id}/git/gitleaks/scan",
        response_model=GitGitleaksScanResponse,
    )
    @queued_operation("git.git_gitleaks_scan", workload_class="qa_light")
    async def git_gitleaks_scan(project_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return service().gitleaks_scan(project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    def branch_manager() -> GitBranchManager:
        return GitBranchManager(service())

    @router.post(
        "/api/v1/projects/{project_id}/git/branch-manager/scan",
        response_model=BranchInventoryResponse,
    )
    @queued_operation(BRANCH_SCAN_OPERATION, workload_class="qa_light")
    async def scan_git_branches(project_id: str, body: BranchScanRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return branch_manager().inventory(
                project_id, stale_days=body.stale_days, far_behind_commits=body.far_behind_commits
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get(
        "/api/v1/projects/{project_id}/git/branch-manager",
        response_model=BranchInventoryResponse,
    )
    def git_branch_inventory(project_id: str) -> dict[str, Any]:
        """Último inventario durable del gestor de ramas; nunca ejecuta Git en el proceso API."""
        project = platform.connection.execute(
            "SELECT path FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if project is None:
            raise HTTPException(404, "Project not found.")
        row = platform.connection.execute(
            """SELECT result_json, finished_at FROM operational_executions
            WHERE project_id=? AND operation=? AND status='completed'
            ORDER BY finished_at DESC LIMIT 1""",
            (project_id, BRANCH_SCAN_OPERATION),
        ).fetchone()
        result = json_loads(row["result_json"], {}) if row else None
        if not isinstance(result, dict) or "branches" not in result:
            return {
                "status": "configuration_required",
                "reason": "Branch inventory requires POST /git/branch-manager/scan.",
                "projectId": project_id,
                "root": project["path"],
                "snapshotAt": None,
                "refreshRequired": True,
            }
        changed = platform.connection.execute(
            """SELECT 1 FROM operational_executions WHERE project_id=?
            AND operation LIKE 'git.%' AND operation!='git.refresh' AND created_at>=? LIMIT 1""",
            (project_id, row["finished_at"]),
        ).fetchone()
        return {**result, "snapshotAt": row["finished_at"], "refreshRequired": changed is not None}

    @router.post(
        "/api/v1/projects/{project_id}/git/branch-manager/delete",
        response_model=BranchActionResponse,
    )
    @queued_operation("git.branch_manager_delete", workload_class="qa_light")
    async def delete_git_branches(
        project_id: str, body: BranchDeleteRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return branch_manager().delete_branches(
                project_id,
                branches=body.branches,
                force_branches=body.force_branches,
                delete_remote=body.delete_remote,
                remote_branches=body.remote_branches,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/projects/{project_id}/git/branch-manager/rename",
        response_model=BranchActionResponse,
    )
    @queued_operation("git.branch_manager_rename", workload_class="qa_light")
    async def rename_git_branch(
        project_id: str, body: BranchRenameRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return branch_manager().rename_branch(project_id, branch=body.branch, new_name=body.new_name)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/projects/{project_id}/git/branch-manager/prune",
        response_model=BranchActionResponse,
    )
    @queued_operation("git.branch_manager_prune", workload_class="qa_light")
    async def prune_git_remote_refs(
        project_id: str, body: BranchPruneRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return branch_manager().prune_remotes(project_id, remote=body.remote)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/projects/{project_id}/git/branch-manager/worktrees/remove",
        response_model=BranchActionResponse,
    )
    @queued_operation("git.branch_manager_remove_worktrees", workload_class="qa_light")
    async def remove_merged_git_worktrees(
        project_id: str, body: BranchWorktreeRemoveRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return branch_manager().remove_merged_worktrees(
                project_id, workspace_ids=body.workspace_ids, root=Path(platform.cwd)
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
