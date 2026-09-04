"""Contratos OpenAPI de aceptación, estado y eventos de una ejecución durable.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

ExecutionStatus = Literal[
    "queued",
    "resource_wait",
    "running",
    "cancel_requested",
    "cancelled",
    "completed",
    "failed",
    "blocked",
    "interrupted",
]
TERMINAL_STATUSES = {"cancelled", "completed", "failed", "blocked", "interrupted"}
EXECUTION_JOB_KIND = "operation.execute"


class ExecutionAccepted(BaseModel):
    """Confirma persistencia en la cola, nunca el resultado futuro de la operación."""

    executionId: str
    jobId: str
    operation: str
    status: ExecutionStatus


class ExecutionResponse(ExecutionAccepted):
    """Estado backend, resultado terminal y razón de bloqueo/cancelación."""

    projectId: str | None
    workloadClass: str
    createdAt: str
    startedAt: str | None
    finishedAt: str | None
    cancelRequestedAt: str | None
    reason: str
    result: Any = None
    resultStatusCode: int | None = None


class ExecutionEvent(BaseModel):
    """Evento incremental identificado por secuencia durable, no por memoria del worker."""

    seq: int
    type: str
    payload: dict[str, Any]
    createdAt: str


class ExecutionEventsResponse(BaseModel):
    """Página acotada de eventos ordenados de una ejecución."""

    executionId: str
    events: list[ExecutionEvent]
    nextSeq: int
