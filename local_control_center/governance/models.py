"""Esquemas Pydantic del API de gobernanza (ADRs, riesgos y next steps).

Define los contratos de request, los registros persistidos y las envolturas de
respuesta para los tres recursos. Los Literal fijan los enums permitidos y los
validadores `before` normalizan a minúsculas para que el alias camelCase del API
sea estable frente a entradas con mayúsculas mixtas.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

DecisionStatus = Literal["proposed", "accepted", "rejected", "superseded", "deprecated"]
RiskSeverity = Literal["low", "medium", "high", "critical"]
RiskStatus = Literal["open", "monitoring", "mitigating", "mitigated", "accepted", "closed"]
NextStepPriority = Literal["low", "medium", "high", "urgent"]
NextStepStatus = Literal["planned", "in_progress", "blocked", "completed", "cancelled"]


class ArchitectureDecisionCreateRequest(BaseModel):
    """Payload entrante para registrar una decisión de arquitectura (ADR)."""

    project_id: str = Field(alias="projectId")
    title: str
    status: DecisionStatus = "proposed"
    context: str = ""
    decision: str = ""
    consequences: list[Any] | str = Field(default_factory=list)
    linked_risk_ids: list[str] = Field(default_factory=list, alias="linkedRiskIds")
    next_step_ids: list[str] = Field(default_factory=list, alias="nextStepIds")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> Any:
        """Baja a minúsculas el status para tolerar entradas con mayúsculas mixtas."""
        return value.lower() if isinstance(value, str) else value


class ArchitectureDecisionRecord(BaseModel):
    """ADR persistida, con id y marcas temporales, tal como la devuelve el API."""

    id: str
    project_id: str = Field(alias="projectId")
    title: str
    status: DecisionStatus
    context: str
    decision: str
    consequences: list[Any] | str = Field(default_factory=list)
    linked_risk_ids: list[str] = Field(alias="linkedRiskIds")
    next_step_ids: list[str] = Field(alias="nextStepIds")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ArchitectureDecisionResponse(BaseModel):
    """Envoltura de respuesta para una sola ADR (create / detalle)."""

    architecture_decision: ArchitectureDecisionRecord = Field(alias="architectureDecision")


class GovernanceResponse(BaseModel):
    """Vista agregada de gobernanza: ADRs, riesgos y next steps en una sola carga."""

    architecture_decisions: list[ArchitectureDecisionRecord] = Field(alias="architectureDecisions")
    risks: list[RiskRecord]
    next_steps: list[NextStepRecord] = Field(alias="nextSteps")


class ArchitectureDecisionsListResponse(BaseModel):
    """Envoltura de respuesta para el listado de ADRs."""

    architecture_decisions: list[ArchitectureDecisionRecord] = Field(alias="architectureDecisions")


class RiskCreateRequest(BaseModel):
    """Payload entrante para dar de alta un riesgo en el registro."""

    project_id: str = Field(alias="projectId")
    title: str
    severity: RiskSeverity = "medium"
    status: RiskStatus = "open"
    description: str = ""
    mitigation: str = ""
    owner: str = ""
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("severity", "status", mode="before")
    @classmethod
    def normalize_choices(cls, value: Any) -> Any:
        """Baja a minúsculas severidad y status antes de validar el enum."""
        return value.lower() if isinstance(value, str) else value


class RiskUpdateRequest(BaseModel):
    """Payload de actualización parcial de un riesgo; campos no enviados quedan intactos."""

    severity: RiskSeverity | None = None
    status: RiskStatus | None = None
    mitigation: str | None = None
    owner: str | None = None
    evidence_refs: list[str] | None = Field(default=None, alias="evidenceRefs")
    metadata: dict[str, Any] | None = None

    @field_validator("severity", "status", mode="before")
    @classmethod
    def normalize_choices(cls, value: Any) -> Any:
        """Baja a minúsculas severidad y status enviados antes de validar el enum."""
        return value.lower() if isinstance(value, str) else value


class RiskRecord(BaseModel):
    """Riesgo persistido del registro, tal como lo devuelve el API."""

    id: str
    project_id: str = Field(alias="projectId")
    title: str
    severity: RiskSeverity
    status: RiskStatus
    description: str
    mitigation: str
    owner: str
    evidence_refs: list[str] = Field(alias="evidenceRefs")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class RiskResponse(BaseModel):
    """Envoltura de respuesta para un solo riesgo (create / update)."""

    risk: RiskRecord


class RisksListResponse(BaseModel):
    """Envoltura de respuesta para el listado de riesgos."""

    risks: list[RiskRecord]


class NextStepCreateRequest(BaseModel):
    """Payload entrante para crear un next step, opcionalmente ligado a un riesgo o ADR."""

    project_id: str = Field(alias="projectId")
    title: str
    status: NextStepStatus = "planned"
    priority: NextStepPriority = "medium"
    source_risk_id: str | None = Field(default=None, alias="sourceRiskId")
    source_decision_id: str | None = Field(default=None, alias="sourceDecisionId")
    owner: str = ""
    due_at: str | None = Field(default=None, alias="dueAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status", "priority", mode="before")
    @classmethod
    def normalize_choices(cls, value: Any) -> Any:
        """Baja a minúsculas status y prioridad antes de validar el enum."""
        return value.lower() if isinstance(value, str) else value


class NextStepUpdateRequest(BaseModel):
    """Payload de actualización parcial de un next step; campos no enviados quedan intactos."""

    status: NextStepStatus | None = None
    priority: NextStepPriority | None = None
    owner: str | None = None
    due_at: str | None = Field(default=None, alias="dueAt")
    metadata: dict[str, Any] | None = None

    @field_validator("status", "priority", mode="before")
    @classmethod
    def normalize_choices(cls, value: Any) -> Any:
        """Baja a minúsculas status y prioridad enviados antes de validar el enum."""
        return value.lower() if isinstance(value, str) else value


class NextStepRecord(BaseModel):
    """Next step persistido, tal como lo devuelve el API."""

    id: str
    project_id: str = Field(alias="projectId")
    title: str
    status: NextStepStatus
    priority: NextStepPriority
    source_risk_id: str | None = Field(default=None, alias="sourceRiskId")
    source_decision_id: str | None = Field(default=None, alias="sourceDecisionId")
    owner: str
    due_at: str | None = Field(default=None, alias="dueAt")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class NextStepResponse(BaseModel):
    """Envoltura de respuesta para un solo next step (create / update)."""

    next_step: NextStepRecord = Field(alias="nextStep")


class NextStepsListResponse(BaseModel):
    """Envoltura de respuesta para el listado de next steps."""

    next_steps: list[NextStepRecord] = Field(alias="nextSteps")
