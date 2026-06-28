"""Router HTTP del slice Git Workspace.

Expone estado Git, ramas, checkout, diff y gitleaks por proyecto. Las mutaciones exigen el
token local y la ejecucion real se delega a ``GitWorkspaceService``.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .models import (
    GitBranchCreateRequest,
    GitBranchesResponse,
    GitBranchMutationResponse,
    GitCheckoutRequest,
    GitCheckoutResponse,
    GitDiffResponse,
    GitGitleaksScanResponse,
    GitStatusResponse,
)
from .service import GitWorkspaceService


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Arma el router Git por proyecto."""
    router = APIRouter()

    def service() -> GitWorkspaceService:
        return GitWorkspaceService(platform.connection, root=Path(platform.cwd))

    @router.get("/api/v1/projects/{project_id}/git/status", response_model=GitStatusResponse)
    async def git_status(project_id: str) -> dict[str, Any]:
        try:
            return service().status(project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/api/v1/projects/{project_id}/git/branches", response_model=GitBranchesResponse)
    async def git_branches(project_id: str) -> dict[str, Any]:
        try:
            return service().branches(project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/projects/{project_id}/git/branches",
        status_code=201,
        response_model=GitBranchMutationResponse,
    )
    async def create_git_branch(
        project_id: str, body: GitBranchCreateRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return service().create_branch(project_id, name=body.name, base=body.base)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/api/v1/projects/{project_id}/git/checkout", response_model=GitCheckoutResponse)
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
        try:
            return service().diff(project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/projects/{project_id}/git/gitleaks/scan",
        response_model=GitGitleaksScanResponse,
    )
    async def git_gitleaks_scan(project_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return service().gitleaks_scan(project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
