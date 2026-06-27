"""Esquemas Pydantic de respuesta del slice product loop: el estado agregado del ciclo de producto.

Definen el contrato HTTP de solo lectura (forma de salida) del endpoint que agrega, por proyecto, el
loop durable y su bitácora, las preguntas de clarificación, el brief vivo, los supuestos, las
decisiones y el backlog (épicas, historias, tareas e iteraciones). Solo modelan datos: no contienen
lógica de negocio ni acceso a la base; traducen ``snake_case`` de Python a ``camelCase`` del frontend
vía alias de campo, reflejando exactamente las claves de los mapeadores ``row_to_*`` de cada slice.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

FEEDBACK_ACTION_VALUES = (
    "accept",
    "request_changes",
    "change_scope",
    "reprioritize",
    "reject_decision",
    "reopen_story",
    "pause_loop",
    "cancel_loop",
)
FeedbackAction = Literal[
    "accept",
    "request_changes",
    "change_scope",
    "reprioritize",
    "reject_decision",
    "reopen_story",
    "pause_loop",
    "cancel_loop",
]

FEEDBACK_CLASSIFICATION_VALUES = (
    "rework_task",
    "new_story",
    "new_epic",
    "brief_revision",
    "architecture_revision",
)
FeedbackClassification = Literal[
    "rework_task",
    "new_story",
    "new_epic",
    "brief_revision",
    "architecture_revision",
]


class ProductLoopRecord(BaseModel):
    """Estado durable de un product loop tal como se expone al cliente."""

    id: str
    project_id: str = Field(alias="projectId")
    initiative_id: str | None = Field(default=None, alias="initiativeId")
    title: str
    state: str
    previous_state: str | None = Field(default=None, alias="previousState")
    status: str
    context: dict[str, Any]
    version: int
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ProductLoopTransitionRecord(BaseModel):
    """Una transición de estado del loop (bitácora append-only)."""

    id: str
    loop_id: str = Field(alias="loopId")
    project_id: str = Field(alias="projectId")
    from_state: str = Field(alias="fromState")
    to_state: str = Field(alias="toState")
    reason: str
    actor: str
    trigger: str
    version: int
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class ProductLoopFeedbackRecord(BaseModel):
    """Feedback del usuario clasificado por impacto y enlazado a sus efectos aplicados."""

    id: str
    loop_id: str = Field(alias="loopId")
    project_id: str = Field(alias="projectId")
    action: FeedbackAction
    classification: FeedbackClassification
    feedback: str
    actor: str
    target_type: str = Field(alias="targetType")
    target_id: str = Field(alias="targetId")
    status: str
    effects: list[Any]
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ClarificationQuestionRecord(BaseModel):
    """Pregunta de clarificación pendiente o respondida de la discovery."""

    id: str
    project_id: str = Field(alias="projectId")
    initiative_id: str | None = Field(default=None, alias="initiativeId")
    session_id: str | None = Field(default=None, alias="sessionId")
    sequence: int
    question: str
    status: str
    priority: str
    asked_by: str | None = Field(default=None, alias="askedBy")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ProductBriefRecord(BaseModel):
    """Brief de producto vivo (última versión) del proyecto."""

    id: str
    project_id: str = Field(alias="projectId")
    initiative_id: str | None = Field(default=None, alias="initiativeId")
    title: str
    status: str
    summary: str | None = None
    problem_statement: str | None = Field(default=None, alias="problemStatement")
    goals: list[Any]
    target_users: list[Any] = Field(alias="targetUsers")
    success_metrics: list[Any] = Field(alias="successMetrics")
    scope: str | None = None
    out_of_scope: str | None = Field(default=None, alias="outOfScope")
    version: int
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class AssumptionRecord(BaseModel):
    """Supuesto capturado durante la discovery, con su confianza y validación."""

    id: str
    project_id: str = Field(alias="projectId")
    initiative_id: str | None = Field(default=None, alias="initiativeId")
    brief_id: str | None = Field(default=None, alias="briefId")
    source_question_id: str | None = Field(default=None, alias="sourceQuestionId")
    statement: str
    status: str
    confidence: str | None = None
    validation: str | None = None
    owner: str | None = None
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ProductDecisionRecord(BaseModel):
    """Decisión de producto/arquitectura con alternativas, consecuencias y reversibilidad."""

    id: str
    project_id: str = Field(alias="projectId")
    initiative_id: str | None = Field(default=None, alias="initiativeId")
    brief_id: str | None = Field(default=None, alias="briefId")
    supersedes_id: str | None = Field(default=None, alias="supersedesId")
    title: str
    status: str
    context: str | None = None
    decision: str | None = None
    rationale: str | None = None
    consequences: list[Any]
    linked_assumption_ids: list[Any] = Field(alias="linkedAssumptionIds")
    linked_question_ids: list[Any] = Field(alias="linkedQuestionIds")
    decided_by: str | None = Field(default=None, alias="decidedBy")
    decided_at: str | None = Field(default=None, alias="decidedAt")
    version: int
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class EpicRecord(BaseModel):
    """Épica del backlog."""

    id: str
    project_id: str = Field(alias="projectId")
    title: str
    description: str | None = None
    status: str
    priority: str
    owner: str | None = None
    version: int
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class UserStoryRecord(BaseModel):
    """Historia de usuario del backlog (valor para el usuario)."""

    id: str
    project_id: str = Field(alias="projectId")
    epic_id: str | None = Field(default=None, alias="epicId")
    title: str
    as_a: str | None = Field(default=None, alias="asA")
    i_want: str | None = Field(default=None, alias="iWant")
    so_that: str | None = Field(default=None, alias="soThat")
    description: str | None = None
    status: str
    priority: str
    business_value: str | None = Field(default=None, alias="businessValue")
    story_points: int | None = Field(default=None, alias="storyPoints")
    owner: str | None = None
    version: int
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class AgentTaskRecord(BaseModel):
    """Tarea técnica de un agente derivada de una historia."""

    id: str
    project_id: str = Field(alias="projectId")
    story_id: str | None = Field(default=None, alias="storyId")
    title: str
    description: str | None = None
    role: str
    category: str
    status: str
    priority: str
    estimate_hours: float | None = Field(default=None, alias="estimateHours")
    version: int
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class AgentAssignmentRecord(BaseModel):
    """Asignación de agente con contrato estructurado y enlaces de colaboración."""

    id: str
    project_id: str = Field(alias="projectId")
    task_id: str = Field(alias="taskId")
    agent_id: str = Field(alias="agentId")
    role: str
    status: str
    assigned_by: str = Field(alias="assignedBy")
    assigned_at: str = Field(alias="assignedAt")
    released_at: str | None = Field(default=None, alias="releasedAt")
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    canonical_artifact_id: str = Field(alias="canonicalArtifactId")
    handoff_id: str = Field(alias="handoffId")
    review_required: bool = Field(alias="reviewRequired")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class AssignmentHandoffRecord(BaseModel):
    """Handoff durable de una asignación hacia el agente responsable."""

    id: str
    project_id: str = Field(alias="projectId")
    assignment_id: str = Field(alias="assignmentId")
    artifact_id: str = Field(alias="artifactId")
    from_agent_id: str = Field(alias="fromAgentId")
    to_agent_id: str = Field(alias="toAgentId")
    status: str
    review_required: bool = Field(alias="reviewRequired")
    blocked_reason: str = Field(alias="blockedReason")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class AssignmentReviewRecord(BaseModel):
    """Review policy-driven de un handoff, con hallazgos persistidos."""

    id: str
    project_id: str = Field(alias="projectId")
    assignment_id: str = Field(alias="assignmentId")
    handoff_id: str = Field(alias="handoffId")
    reviewer_agent_id: str = Field(alias="reviewerAgentId")
    policy_required: bool = Field(alias="policyRequired")
    status: str
    decision: str
    findings: list[Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    resolved_at: str | None = Field(default=None, alias="resolvedAt")


class AssignmentConflictRecord(BaseModel):
    """Desacuerdo entre agentes/reviewers y su resolución final."""

    id: str
    project_id: str = Field(alias="projectId")
    assignment_id: str = Field(alias="assignmentId")
    handoff_id: str = Field(alias="handoffId")
    status: str
    raised_by: str = Field(alias="raisedBy")
    disagreement: str
    final_resolution: str = Field(alias="finalResolution")
    resolved_by: str | None = Field(default=None, alias="resolvedBy")
    resolved_at: str | None = Field(default=None, alias="resolvedAt")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class IterationRecord(BaseModel):
    """Iteración planificada: estrategia de workspace, gates de calidad/seguridad y costo estimado."""

    id: str
    project_id: str = Field(alias="projectId")
    brief_id: str | None = Field(default=None, alias="briefId")
    title: str
    goal: str | None = None
    status: str
    story_ids: list[Any] = Field(alias="storyIds")
    workspace_strategy: str | None = Field(default=None, alias="workspaceStrategy")
    quality_gates: list[Any] = Field(alias="qualityGates")
    security_gates: list[Any] = Field(alias="securityGates")
    estimated_cost: dict[str, Any] = Field(alias="estimatedCost")
    runtimes: list[Any]
    task_count: int = Field(alias="taskCount")
    assignment_count: int = Field(alias="assignmentCount")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ProductLoopStateResponse(BaseModel):
    """Estado agregado del product loop de un proyecto, consumido por el Workbench en una sola lectura."""

    loops: list[ProductLoopRecord]
    transitions: list[ProductLoopTransitionRecord]
    feedback: list[ProductLoopFeedbackRecord]
    questions: list[ClarificationQuestionRecord]
    brief: ProductBriefRecord | None = None
    assumptions: list[AssumptionRecord]
    decisions: list[ProductDecisionRecord]
    epics: list[EpicRecord]
    stories: list[UserStoryRecord]
    tasks: list[AgentTaskRecord]
    assignments: list[AgentAssignmentRecord]
    assignment_handoffs: list[AssignmentHandoffRecord] = Field(alias="assignmentHandoffs")
    assignment_reviews: list[AssignmentReviewRecord] = Field(alias="assignmentReviews")
    assignment_conflicts: list[AssignmentConflictRecord] = Field(alias="assignmentConflicts")
    iterations: list[IterationRecord]


class ProductLoopStartRequest(BaseModel):
    """Cuerpo para arrancar un product loop en ``goal_received``, con su política de gobierno opcional."""

    title: str
    initiative_id: str | None = Field(default=None, alias="initiativeId")
    context: dict[str, Any] | None = None
    correlation_id: str | None = Field(default=None, alias="correlationId")
    budget: dict[str, Any] | None = None
    timeouts: dict[str, Any] | None = None
    max_rework_rounds: int | None = Field(default=None, alias="maxReworkRounds")
    deadline: str | None = None


class ProductLoopTransitionRequest(BaseModel):
    """Cuerpo para avanzar un loop a un estado destino permitido por la FSM."""

    to_state: str = Field(alias="toState")
    reason: str | None = None
    trigger: str | None = None
    correlation_id: str | None = Field(default=None, alias="correlationId")
    expected_version: int | None = Field(default=None, alias="expectedVersion")


class ProductLoopFeedbackRequest(BaseModel):
    """Comando de feedback del usuario aplicado por el coordinador del Product Loop."""

    action: FeedbackAction
    feedback: str
    actor: str | None = None
    target_type: str | None = Field(default=None, alias="targetType")
    target_id: str | None = Field(default=None, alias="targetId")
    payload: dict[str, Any] | None = None
    correlation_id: str | None = Field(default=None, alias="correlationId")
    expected_version: int | None = Field(default=None, alias="expectedVersion")


class ProductLoopResumeResponse(BaseModel):
    """Estado del loop tras arrancar o transicionar, con las transiciones que admite ahora."""

    loop: ProductLoopRecord
    resumable: bool
    allowed_next_states: list[str] = Field(alias="allowedNextStates")
    transitions: list[ProductLoopTransitionRecord]


class ProductLoopFeedbackApplyResponse(ProductLoopResumeResponse):
    """Respuesta de una acción de feedback aplicada, incluyendo su registro trazable."""

    feedback: ProductLoopFeedbackRecord
