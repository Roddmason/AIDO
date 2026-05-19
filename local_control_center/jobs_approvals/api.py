from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from local_control_center.shared.event_bus import EventBus

from . import commands
from .repository import JobsRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def jobs() -> JobsRepository:
        return JobsRepository(platform.connection)

    def events() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/jobs")
    async def list_jobs() -> dict[str, Any]:
        return commands.list_jobs(jobs(), events())

    @router.post("/api/v1/jobs", status_code=202)
    async def create_job(request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.create_job(jobs(), await request.json())

    @router.post("/api/v1/jobs/{job_id}/approve", status_code=202)
    async def approve_job(job_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.approve_job(jobs(), job_id, await request.json())

    @router.post("/api/v1/jobs/{job_id}/cancel", status_code=202)
    async def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.cancel_job(jobs(), job_id, await request.json())

    @router.post("/api/v1/jobs/{job_id}/retry", status_code=202)
    async def retry_job(job_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.retry_job(jobs(), job_id, await request.json())

    @router.get("/api/v1/approvals")
    async def approvals() -> dict[str, Any]:
        return commands.list_approvals(jobs())

    @router.post("/api/v1/jobs/{job_id}/actions/{action_id}/approve", status_code=202)
    async def approve_action(job_id: str, action_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.approve_action(jobs(), job_id, action_id, await request.json())

    @router.post("/api/v1/jobs/{job_id}/actions/{action_id}/deny", status_code=202)
    async def deny_action(job_id: str, action_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.deny_action(jobs(), job_id, action_id, await request.json())

    return router
