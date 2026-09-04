"""API operacional de procesos y solicitudes durables de cancelación.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.event_bus import EventBus
from local_control_center.workers.leadership import WorkerControlRepository

from .repository import ManagedProcessRepository, _record


class ProcessRecordResponse(BaseModel):
    """Estado observable y evidencia del árbol administrado."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    managed_process_id: str
    execution_id: str
    root_pid: int
    workload_class: str
    command_fingerprint: str
    started_at: str
    finished_at: str | None
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    peak_memory_bytes: int
    cpu_time_seconds: float
    stdout_artifact_id: str | None
    stderr_artifact_id: str | None
    termination_reason: str
    cancel_requested_at: str | None
    released_at: str | None
    resource_lease_id: str | None


class ProcessesResponse(BaseModel):
    """Lista acotada de procesos observados."""

    processes: list[ProcessRecordResponse]


class StopReasonRequest(BaseModel):
    """Motivo humano obligatorio para cancelación y emergency stop."""

    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        """Rechaza un motivo compuesto exclusivamente por espacios."""
        if not value.strip():
            raise ValueError("A non-empty human reason is required.")
        return value.strip()


class EmergencyStopResponse(BaseModel):
    """Confirma solicitudes durables; no afirma terminación antes de observarla."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    status: str = "cancel_requested"
    managed_process_ids: list[str]


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Crea controles que siguen respondiendo aunque el worker esté ocupado."""
    router = APIRouter()

    @router.get("/api/v1/operations/processes", response_model=ProcessesResponse)
    async def processes(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        rows = platform.connection.execute(
            "SELECT * FROM managed_processes ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"processes": [asdict(_record(row)) for row in rows]}

    @router.post(
        "/api/v1/operations/processes/{process_id}/cancel",
        status_code=202,
        response_model=ProcessRecordResponse,
    )
    async def cancel(process_id: str, body: StopReasonRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        with immediate_transaction(platform.connection):
            record = ManagedProcessRepository(platform.connection).request_cancel(
                process_id, reason=body.reason
            )
            if record is None:
                raise HTTPException(404, "Managed process not found.")
        return asdict(record)

    @router.post("/api/v1/workers/emergency-stop", status_code=202, response_model=EmergencyStopResponse)
    async def emergency_stop(body: StopReasonRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        with immediate_transaction(platform.connection):
            WorkerControlRepository(platform.connection).request_state(
                "emergency_stopped", reason=body.reason
            )
            repository = ManagedProcessRepository(platform.connection)
            active = repository.active()
            for record in active:
                repository.request_cancel(record.managed_process_id, reason=body.reason)
            ids = [record.managed_process_id for record in active]
            EventBus(platform.connection).record_audit(
                action="worker.emergency_stop",
                target="local",
                actor="operator",
                payload={"reason": body.reason, "managedProcessIds": ids},
            )
        return {"status": "cancel_requested", "managedProcessIds": ids}

    return router
