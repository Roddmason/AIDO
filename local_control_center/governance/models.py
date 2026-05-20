from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


DecisionStatus = Literal["proposed", "accepted", "rejected", "superseded", "deprecated"]
RiskSeverity = Literal["low", "medium", "high", "critical"]
RiskStatus = Literal["open", "monitoring", "mitigating", "mitigated", "accepted", "closed"]
NextStepPriority = Literal["low", "medium", "high", "urgent"]
NextStepStatus = Literal["planned", "in_progress", "blocked", "completed", "cancelled"]


class ArchitectureDecisionCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    title: str
    status: DecisionStatus = "proposed"
    context: str = ""
    decision: str = ""
    consequences: list[Any] = Field(default_factory=list)
    linked_risk_ids: list[str] = Field(default_factory=list, alias="linkedRiskIds")
    next_step_ids: list[str] = Field(default_factory=list, alias="nextStepIds")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


class ArchitectureDecisionResponse(BaseModel):
    architecture_decision: dict[str, Any] = Field(alias="architectureDecision")


class RiskCreateRequest(BaseModel):
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
        return value.lower() if isinstance(value, str) else value


class RiskUpdateRequest(BaseModel):
    severity: RiskSeverity | None = None
    status: RiskStatus | None = None
    mitigation: str | None = None
    owner: str | None = None
    evidence_refs: list[str] | None = Field(default=None, alias="evidenceRefs")
    metadata: dict[str, Any] | None = None

    @field_validator("severity", "status", mode="before")
    @classmethod
    def normalize_choices(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


class RiskResponse(BaseModel):
    risk: dict[str, Any]


class NextStepCreateRequest(BaseModel):
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
        return value.lower() if isinstance(value, str) else value


class NextStepUpdateRequest(BaseModel):
    status: NextStepStatus | None = None
    priority: NextStepPriority | None = None
    owner: str | None = None
    due_at: str | None = Field(default=None, alias="dueAt")
    metadata: dict[str, Any] | None = None

    @field_validator("status", "priority", mode="before")
    @classmethod
    def normalize_choices(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


class NextStepResponse(BaseModel):
    next_step: dict[str, Any] = Field(alias="nextStep")
