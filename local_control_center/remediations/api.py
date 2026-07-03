"""HTTP API for thread remediation actions.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from local_control_center.remediations.contracts import (
    RemediationDismissResponse,
    RemediationExecuteRequest,
    RemediationExecuteResponse,
    RemediationListResponse,
)
from local_control_center.remediations.service import BlockerRemediationService

REMEDIATION_EXECUTE_BODY = Body(default=None)


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Build remediation routes bound to the platform connection and worker runtime."""
    router = APIRouter()

    def service() -> BlockerRemediationService:
        return BlockerRemediationService(platform.connection, root=getattr(platform, "cwd", None))

    def worker_status() -> dict[str, Any]:
        worker = getattr(platform, "local_worker_runtime", None)
        if worker is None:
            return {"status": "stopped", "running": False, "reason": "Local worker runtime is unavailable."}
        return worker.status()

    @router.get(
        "/api/v1/threads/{thread_id}/remediations",
        response_model=RemediationListResponse,
    )
    async def list_thread_remediations(thread_id: str) -> dict[str, Any]:
        try:
            return {
                "remediations": service().list_for_thread(
                    thread_id=thread_id,
                    worker_status=worker_status(),
                )
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/remediations/{remediation_id}/execute",
        response_model=RemediationExecuteResponse,
    )
    async def execute_remediation(
        remediation_id: str,
        request: Request,
        body: RemediationExecuteRequest | None = REMEDIATION_EXECUTE_BODY,
    ) -> dict[str, Any]:
        require_write(request)
        try:
            request_body = body or RemediationExecuteRequest()
            return service().execute(remediation_id, platform=platform, payload=request_body.payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post(
        "/api/v1/remediations/{remediation_id}/dismiss",
        response_model=RemediationDismissResponse,
    )
    async def dismiss_remediation(remediation_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return {"remediation": service().dismiss(remediation_id)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
