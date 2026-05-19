from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from . import commands


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/jobs")
    async def list_jobs() -> dict[str, Any]:
        return commands.list_jobs(platform)

    @router.post("/api/v1/jobs", status_code=202)
    async def create_job(request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.create_job(platform, await request.json())

    @router.post("/api/v1/jobs/{job_id}/approve", status_code=202)
    async def approve_job(job_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.approve_job(platform, job_id, await request.json())

    @router.post("/api/v1/jobs/{job_id}/cancel", status_code=202)
    async def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.cancel_job(platform, job_id, await request.json())

    @router.post("/api/v1/jobs/{job_id}/retry", status_code=202)
    async def retry_job(job_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.retry_job(platform, job_id, await request.json())

    @router.get("/api/v1/approvals")
    async def approvals() -> dict[str, Any]:
        return commands.list_approvals(platform)

    @router.post("/api/v1/jobs/{job_id}/actions/{action_id}/approve", status_code=202)
    async def approve_action(job_id: str, action_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.approve_action(platform, job_id, action_id, await request.json())

    @router.post("/api/v1/jobs/{job_id}/actions/{action_id}/deny", status_code=202)
    async def deny_action(job_id: str, action_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.deny_action(platform, job_id, action_id, await request.json())

    return router
