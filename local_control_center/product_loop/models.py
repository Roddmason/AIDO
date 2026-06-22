"""Esquemas Pydantic de respuesta del slice product loop: el estado agregado del ciclo de producto.

Definen el contrato HTTP de solo lectura (forma de salida) del endpoint que agrega, por proyecto, el
loop durable y su bitácora, las preguntas de clarificación, el brief vivo, los supuestos, las
decisiones y el backlog (épicas, historias, tareas e iteraciones). Solo modelan datos: no contienen
lógica de negocio ni acceso a la base; traducen ``snake_case`` de Python a ``camelCase`` del frontend
vía alias de campo, reflejando exactamente las claves de los mapeadores ``row_to_*`` de cada slice.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
    questions: list[ClarificationQuestionRecord]
    brief: ProductBriefRecord | None = None
    assumptions: list[AssumptionRecord]
    decisions: list[ProductDecisionRecord]
    epics: list[EpicRecord]
    stories: list[UserStoryRecord]
    tasks: list[AgentTaskRecord]
    iterations: list[IterationRecord]
