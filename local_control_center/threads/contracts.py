"""Contratos del slice threads: literales acotados y esquemas Pydantic de request/response.

Definen la forma HTTP estable de los hilos reales (header, mensajes, artifacts, eventos y
decisiones). Los literales acotados mantienen el contrato tipado de extremo a extremo y evitan el
"Literal drift" que degrada el cliente generado a ``string`` o produce 500 al serializar. Solo modelan
datos: la lógica vive en el repositorio y el coordinator.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

THREAD_OWNER_TYPES = ("workspace", "loop", "story", "agent_task", "review")
ThreadOwnerType = Literal["workspace", "loop", "story", "agent_task", "review"]

THREAD_STATUSES = (
    "open",
    "queued",
    "running",
    "waiting_decision",
    "awaiting_approval",
    "blocked",
    "resolved",
    "archived",
)
ThreadStatus = Literal[
    "open",
    "queued",
    "running",
    "waiting_decision",
    "awaiting_approval",
    "blocked",
    "resolved",
    "archived",
]

THREAD_MESSAGE_KINDS = (
    "user",
    "aido_lead",
    "agent_summary",
    "decision_request",
    "artifact",
    "error",
    "system_event",
)
ThreadMessageKind = Literal[
    "user",
    "aido_lead",
    "agent_summary",
    "decision_request",
    "artifact",
    "error",
    "system_event",
]

THREAD_DECISION_STATUSES = ("pending", "resolved", "dismissed")
ThreadDecisionStatus = Literal["pending", "resolved", "dismissed"]


class ThreadRecord(BaseModel):
    """Header de un hilo con ownership polimórfico, tal como se expone al cliente."""

    id: str
    project_id: str = Field(alias="projectId")
    owner_type: ThreadOwnerType = Field(alias="ownerType")
    owner_id: str = Field(alias="ownerId")
    title: str
    status: ThreadStatus
    summary: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ThreadMessageRecord(BaseModel):
    """Un mensaje del timeline del hilo (append-only, ordenado por ``sequence``)."""

    id: str
    thread_id: str = Field(alias="threadId")
    project_id: str = Field(alias="projectId")
    sequence: int
    kind: ThreadMessageKind
    author: str
    content: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class ThreadArtifactRecord(BaseModel):
    """Artifact surgido en el hilo (enlace, no copia)."""

    id: str
    thread_id: str = Field(alias="threadId")
    project_id: str = Field(alias="projectId")
    message_id: str | None = Field(default=None, alias="messageId")
    artifact_id: str = Field(alias="artifactId")
    kind: str
    title: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class ThreadAgentEventRecord(BaseModel):
    """Evento de ejecución del coordinator/agentes (bitácora append-only del hilo)."""

    id: str
    thread_id: str = Field(alias="threadId")
    project_id: str = Field(alias="projectId")
    sequence: int
    type: str
    agent_role: str | None = Field(default=None, alias="agentRole")
    payload: dict[str, Any]
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class ThreadDecisionRecord(BaseModel):
    """Solicitud de decisión que el coordinator levanta al bloquear y su resolución."""

    id: str
    thread_id: str = Field(alias="threadId")
    project_id: str = Field(alias="projectId")
    message_id: str | None = Field(default=None, alias="messageId")
    title: str
    status: ThreadDecisionStatus
    prompt: str
    options: list[str]
    resolution: str | None = None
    decided_by: str | None = Field(default=None, alias="decidedBy")
    decided_at: str | None = Field(default=None, alias="decidedAt")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ThreadListResponse(BaseModel):
    """Listado de hilos de un proyecto (headers, sin timeline)."""

    threads: list[ThreadRecord]


class ThreadDetailResponse(BaseModel):
    """Un hilo con su timeline completo: mensajes, artifacts, decisiones y eventos."""

    thread: ThreadRecord
    messages: list[ThreadMessageRecord]
    artifacts: list[ThreadArtifactRecord]
    decisions: list[ThreadDecisionRecord]
    events: list[ThreadAgentEventRecord]


class ThreadEventsResponse(BaseModel):
    """Ventana incremental de eventos del hilo para polling tipo consola."""

    events: list[ThreadAgentEventRecord]
    last_seq: int = Field(alias="lastSeq")
    thread_status: ThreadStatus = Field(alias="threadStatus")
    running: bool


class ThreadCreateRequest(BaseModel):
    """Cuerpo para crear un hilo enlazado a una entidad dueña del proyecto."""

    project_id: str = Field(alias="projectId")
    owner_type: ThreadOwnerType = Field(alias="ownerType")
    owner_id: str = Field(alias="ownerId")
    title: str


class ThreadMessageRequest(BaseModel):
    """Cuerpo para publicar un mensaje de usuario que dispara la ejecución del coordinator."""

    content: str
    author: str | None = None


class ThreadRunSummary(BaseModel):
    """Resumen del run asincrónico levantado por el mensaje, si corresponde."""

    status: Literal["queued", "running", "blocked", "awaiting_approval", "completed", "failed"]
    job_id: str | None = Field(default=None, alias="jobId")
    loop_id: str | None = Field(default=None, alias="loopId")
    reason: str | None = None


class ThreadMessageResultResponse(BaseModel):
    """Resultado de ejecutar el coordinator sobre un mensaje: responde u (bloquea con decisión)."""

    thread: ThreadRecord
    blocked: bool
    run: ThreadRunSummary
    messages: list[ThreadMessageRecord]
    decision: ThreadDecisionRecord | None = None
    artifacts: list[ThreadArtifactRecord]
    events: list[ThreadAgentEventRecord]


class ThreadDecisionResolveRequest(BaseModel):
    """Cuerpo para resolver una solicitud de decisión pendiente del hilo."""

    resolution: str
    decided_by: str | None = Field(default=None, alias="decidedBy")


class ThreadDecisionResolveResponse(BaseModel):
    """Resultado de resolver una decisión: el hilo reabierto y la decisión resuelta."""

    thread: ThreadRecord
    decision: ThreadDecisionRecord
