"""HTTP API for thread remediation actions.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from local_control_center.executions.models import ExecutionAccepted
from local_control_center.executions.router import (
    ExecutionRouter,
    OperationSpec,
    enqueue_registered_operation,
)
from local_control_center.remediations.contracts import (
    RemediationDismissResponse,
    RemediationExecuteRequest,
    RemediationExecuteResponse,
    RemediationListResponse,
)
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.shared.time import utc_now
from local_control_center.workers.leadership import WorkerControlRepository, WorkerLeadershipRepository

REMEDIATION_EXECUTE_BODY = Body(default=None)


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Build remediation routes bound to the platform connection and worker runtime."""
    router = ExecutionRouter(platform=platform, require_write=require_write)

    def service() -> BlockerRemediationService:
        return BlockerRemediationService(platform.connection, root=getattr(platform, "cwd", None))

    def worker_status() -> dict[str, Any]:
        lease = WorkerLeadershipRepository(platform.connection).current()
        control = WorkerControlRepository(platform.connection).get()
        connected = bool(lease and lease["expires_at"] > utc_now())
        return {
            "status": control["desiredState"] if connected else "stopped",
            "running": connected and control["desiredState"] == "running",
        }

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
        responses={202: {"model": ExecutionAccepted}},
    )
    async def execute_remediation(
        remediation_id: str,
        request: Request,
        body: RemediationExecuteRequest | None = REMEDIATION_EXECUTE_BODY,
    ) -> dict[str, Any]:
        require_write(request)
        try:
            request_body = body or RemediationExecuteRequest()
            action = service().repository.get(remediation_id)
            # Un comando de control no puede depender del worker que debe despertar.
            if action["actionType"] == "run_worker_once":
                return service().execute(remediation_id, platform=platform, payload=request_body.payload)
            accepted = enqueue_registered_operation(
                platform,
                operation_spec,
                {"remediation_id": remediation_id, "body": request_body},
                project_id=action["projectId"],
            )
            return JSONResponse(status_code=202, content=accepted)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    async def perform_remediation(
        remediation_id: str, body: RemediationExecuteRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return service().execute(remediation_id, platform=platform, payload=body.payload)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    operation_spec = OperationSpec("remediations.execute", "agent_cli", RemediationExecuteResponse)
    platform.execution_handlers[operation_spec.name] = (operation_spec, perform_remediation)
    execute_remediation._aido_operation = operation_spec

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
