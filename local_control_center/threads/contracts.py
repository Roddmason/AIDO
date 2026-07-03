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

from local_control_center.shared.schemas import AuditEventRecord

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
    "deleted",
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
    "deleted",
]

THREAD_MESSAGE_KINDS = (
    "user",
    "aido_lead",
    "agent_summary",
    "decision_request",
    "artifact",
    "error",
    "system_event",
    "operator_note",
)
ThreadMessageKind = Literal[
    "user",
    "aido_lead",
    "agent_summary",
    "decision_request",
    "artifact",
    "error",
    "system_event",
    "operator_note",
]

THREAD_DECISION_STATUSES = ("pending", "resolved", "dismissed")
ThreadDecisionStatus = Literal["pending", "resolved", "dismissed"]

THREAD_SIMILARITY_ACTIONS = (
    "continue_existing",
    "improve_existing",
    "performance_pass",
    "create_new_anyway",
)
ThreadSimilarityAction = Literal[
    "continue_existing",
    "improve_existing",
    "performance_pass",
    "create_new_anyway",
]


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
    archived_at: str | None = Field(default=None, alias="archivedAt")
    archived_by: str | None = Field(default=None, alias="archivedBy")
    deleted_at: str | None = Field(default=None, alias="deletedAt")
    deleted_by: str | None = Field(default=None, alias="deletedBy")
    lifecycle_reason: str | None = Field(default=None, alias="lifecycleReason")
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


class ThreadUpdateRequest(BaseModel):
    """Cuerpo para mutar el header editable de un hilo."""

    title: str | None = None


class ThreadArchiveRequest(BaseModel):
    """Cuerpo para archivar o reabrir un hilo con motivo auditable."""

    reason: str
    actor: str = "operator"


class ThreadDeleteRequest(BaseModel):
    """Cuerpo para borrar logicamente un hilo sin eliminar mensajes ni artifacts."""

    reason: str
    actor: str = "operator"


class ThreadArchiveResponse(BaseModel):
    """Resultado de archivar o reabrir un hilo junto con su evento de auditoria."""

    thread: ThreadRecord
    audit_event: AuditEventRecord = Field(alias="auditEvent")


class ThreadDeleteResponse(BaseModel):
    """Resultado de borrar logicamente un hilo junto con su evento de auditoria."""

    thread: ThreadRecord
    audit_event: AuditEventRecord = Field(alias="auditEvent")


class ThreadSimilarityArtifactRef(BaseModel):
    """Referencia mínima a artifact usada por el índice de similitud."""

    artifact_id: str = Field(alias="artifactId")
    kind: str
    title: str


class ThreadSimilarityCandidateRecord(BaseModel):
    """Candidato similar devuelto por el índice lexical."""

    thread_id: str = Field(alias="threadId")
    project_id: str = Field(alias="projectId")
    title: str
    summary: str
    status: ThreadStatus
    score: float
    reason: str
    keywords: list[str]
    artifact_refs: list[ThreadSimilarityArtifactRef] = Field(alias="artifactRefs")
    updated_at: str = Field(alias="updatedAt")


class ThreadSimilarityResponse(BaseModel):
    """Respuesta de búsqueda de threads similares."""

    candidates: list[ThreadSimilarityCandidateRecord]


class ThreadSimilarityMarkRequest(BaseModel):
    """Cuerpo para marcar la decisión operacional tomada sobre un candidato similar."""

    action: ThreadSimilarityAction
    score: float = 0.0
    reason: str = ""


class ThreadSimilarityEventRecord(BaseModel):
    """Evento persistido de decisión de similitud entre dos threads."""

    id: str
    project_id: str = Field(alias="projectId")
    source_thread_id: str = Field(alias="sourceThreadId")
    candidate_thread_id: str = Field(alias="candidateThreadId")
    score: float
    reason: str
    action: ThreadSimilarityAction
    functionality_id: str = Field(default="", alias="functionalityId")
    created_at: str = Field(alias="createdAt")


class ThreadSimilarityMarkResponse(BaseModel):
    """Respuesta de marcar un candidato similar."""

    event: ThreadSimilarityEventRecord


class ProjectFunctionalityRecord(BaseModel):
    """Funcionalidad existente registrada desde threads resueltos o decisiones de similitud."""

    id: str
    project_id: str = Field(alias="projectId")
    name: str
    summary: str
    normalized_name: str = Field(alias="normalizedName")
    fingerprint: str
    source_thread_id: str = Field(alias="sourceThreadId")
    status: ThreadStatus
    file_paths: list[str] = Field(alias="filePaths")
    performance_notes: list[dict[str, Any]] = Field(alias="performanceNotes")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    score: float | None = None
    reason: str | None = None


class ProjectFunctionalityResponse(BaseModel):
    """Listado project-scoped de funcionalidad existente."""

    functionality: list[ProjectFunctionalityRecord]


class ThreadMemoryReindexResponse(BaseModel):
    """Resultado de reindexar memoria de thread y, si aplica, funcionalidad existente."""

    index: dict[str, Any]
    functionality: ProjectFunctionalityRecord | None = None


class ThreadMessageRequest(BaseModel):
    """Cuerpo para publicar un mensaje de usuario que dispara la ejecución del coordinator."""

    content: str
    author: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ThreadNoteRequest(BaseModel):
    """Cuerpo para adjuntar una nota del operador a un hilo con ejecución en curso.

    A diferencia de ``ThreadMessageRequest``, una nota nunca dispara el coordinator ni encola un job:
    solo deja constancia auditable en el timeline para evitar iniciar loops concurrentes por accidente.
    """

    content: str
    author: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ThreadNoteResponse(BaseModel):
    """Resultado de adjuntar una nota del operador: el hilo (sin cambio de estado) y la nota creada."""

    thread: ThreadRecord
    message: ThreadMessageRecord


class ThreadCancelRequest(BaseModel):
    """Cuerpo para cancelar la ejecución en curso de un hilo con un motivo auditable."""

    reason: str | None = None
    actor: str | None = None


class ThreadCancelResponse(BaseModel):
    """Resultado de cancelar la ejecución: el hilo reabierto y los jobs efectivamente cancelados."""

    thread: ThreadRecord
    cancelled_job_ids: list[str] = Field(alias="cancelledJobIds")


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


class ThreadMemoryDecisionRecord(BaseModel):
    """Decisión ya resuelta en un hilo similar, recordada por el recall de memoria."""

    id: str
    thread_id: str = Field(alias="threadId")
    thread_title: str = Field(alias="threadTitle")
    title: str
    prompt: str
    resolution: str
    decided_at: str | None = Field(default=None, alias="decidedAt")
    created_at: str = Field(alias="createdAt")


class ThreadMemoryEvidenceRecord(BaseModel):
    """Artifact de un hilo similar expuesto como evidencia relacionada."""

    id: str
    thread_id: str = Field(alias="threadId")
    thread_title: str = Field(alias="threadTitle")
    artifact_id: str = Field(alias="artifactId")
    kind: str
    title: str
    created_at: str = Field(alias="createdAt")


class ThreadMemoryLessonRecord(BaseModel):
    """Lección vigente del proyecto (``memory_items`` kind='lesson') con su relevancia lexical."""

    id: str
    content: str
    source_ref: str = Field(alias="sourceRef")
    matched_keywords: list[str] = Field(alias="matchedKeywords")
    created_at: str = Field(alias="createdAt")


class ThreadMemoryPerformanceRecord(BaseModel):
    """Pasada de performance ya pedida sobre trabajo similar (evento o mensaje real)."""

    id: str
    thread_id: str = Field(alias="threadId")
    thread_title: str = Field(alias="threadTitle")
    note: str
    source: Literal["similarity_event", "thread_message"]
    created_at: str = Field(alias="createdAt")


class ThreadMemoryImplementedRecord(BaseModel):
    """Hilo similar ya resuelto: funcionalidad que el proyecto ya implementó antes."""

    thread_id: str = Field(alias="threadId")
    title: str
    summary: str
    score: float
    updated_at: str = Field(alias="updatedAt")


class ThreadMemoryRecallResponse(BaseModel):
    """Memoria agregada de un hilo: las seis categorías del panel de recall."""

    source_thread_id: str = Field(alias="sourceThreadId")
    generated_at: str = Field(alias="generatedAt")
    similar_threads: list[ThreadSimilarityCandidateRecord] = Field(alias="similarThreads")
    previous_decisions: list[ThreadMemoryDecisionRecord] = Field(alias="previousDecisions")
    related_evidence: list[ThreadMemoryEvidenceRecord] = Field(alias="relatedEvidence")
    lessons_learned: list[ThreadMemoryLessonRecord] = Field(alias="lessonsLearned")
    performance_issues: list[ThreadMemoryPerformanceRecord] = Field(alias="performanceIssues")
    implemented_functionality: list[ThreadMemoryImplementedRecord] = Field(alias="implementedFunctionality")
