from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from local_control_center.shared.event_bus import EventBus

from . import commands
from .models import (
    ApprovalReasonRequest,
    ApprovalsListResponse,
    JobCreateRequest,
    JobMutationResponse,
    JobsListResponse,
    OptionalReasonRequest,
)
from .repository import JobsRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def jobs() -> JobsRepository:
        return JobsRepository(platform.connection)

    def events() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/jobs", response_model=JobsListResponse)
    async def list_jobs() -> dict[str, Any]:
        return commands.list_jobs(jobs(), events())

    @router.post("/api/v1/jobs", status_code=202, response_model=JobMutationResponse)
    async def create_job(body: JobCreateRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.create_job(jobs(), body.model_dump(by_alias=True))

    @router.post("/api/v1/jobs/{job_id}/approve", status_code=202, response_model=JobMutationResponse)
    async def approve_job(job_id: str, body: ApprovalReasonRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.approve_job(jobs(), job_id, body.model_dump())

    @router.post("/api/v1/jobs/{job_id}/cancel", status_code=202, response_model=JobMutationResponse)
    async def cancel_job(job_id: str, body: OptionalReasonRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.cancel_job(jobs(), job_id, body.model_dump())

    @router.post("/api/v1/jobs/{job_id}/retry", status_code=202, response_model=JobMutationResponse)
    async def retry_job(job_id: str, body: OptionalReasonRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.retry_job(jobs(), job_id, body.model_dump())

    @router.get("/api/v1/approvals", response_model=ApprovalsListResponse)
    async def approvals() -> dict[str, Any]:
        return commands.list_approvals(jobs())

    @router.post("/api/v1/jobs/{job_id}/actions/{action_id}/approve", status_code=202, response_model=JobMutationResponse)
    async def approve_action(job_id: str, action_id: str, body: ApprovalReasonRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.approve_action(jobs(), job_id, action_id, body.model_dump())

    @router.post("/api/v1/jobs/{job_id}/actions/{action_id}/deny", status_code=202, response_model=JobMutationResponse)
    async def deny_action(job_id: str, action_id: str, body: OptionalReasonRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.deny_action(jobs(), job_id, action_id, body.model_dump())

    return router
