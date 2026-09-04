"""Consulta y cancela ejecuciones sin depender del proceso worker ni esperar su trabajo.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from local_control_center.process_supervision.api import StopReasonRequest

from .models import ExecutionEventsResponse, ExecutionResponse
from .repository import ExecutionRepository


def create_router(*, platform: Any, require_write: Callable) -> APIRouter:
    """Expone estado y eventos incrementales con cancelación autenticada e idempotente."""
    router = APIRouter(prefix="/api/v1/executions")

    @router.get("/{execution_id}", response_model=ExecutionResponse)
    async def execution(execution_id: str):
        try:
            return ExecutionRepository(platform.connection).get(execution_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @router.get("/{execution_id}/events", response_model=ExecutionEventsResponse)
    async def events(
        execution_id: str, afterSeq: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)
    ):
        try:
            return ExecutionRepository(platform.connection).events(
                execution_id, after_seq=afterSeq, limit=limit
            )
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @router.post("/{execution_id}/cancel", status_code=202, response_model=ExecutionResponse)
    async def cancel(execution_id: str, body: StopReasonRequest, request: Request):
        require_write(request)
        try:
            return ExecutionRepository(platform.connection).request_cancel(execution_id, reason=body.reason)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    return router
