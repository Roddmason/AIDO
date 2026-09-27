"""Contratos HTTP del equipo de runtimes por hilo: prueba de runtime, candidatos y roles.

Literales acotados para que el cliente generado no degrade a ``string`` (drift de response_model).

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RuntimeValidationOutcome = Literal["validated", "failed", "deferred"]


class RuntimeValidationRequest(BaseModel):
    """Cuerpo de la prueba: el proyecto es obligatorio para CLI; ``model`` fija el modelo a validar."""

    project_id: str | None = Field(default=None, alias="projectId")
    model: str | None = Field(default=None, min_length=1, max_length=160)


class RuntimeValidationAttemptRecord(BaseModel):
    """Un modelo probado durante una validación sin modelo pedido, con su resultado y causa."""

    model: str | None = None
    status: RuntimeValidationOutcome
    reason: str | None = None
    evidence: str | None = None
    latency_ms: int | None = Field(default=None, alias="latencyMs")


class RuntimeValidationResultRecord(BaseModel):
    """Resultado redactado de una prueba de ida y vuelta de un runtime.

    ``attempts`` lista cada modelo probado cuando el runtime eligió los candidatos (API/gateway sin
    modelo pedido); ``None`` cuando se probó un único modelo fijado.
    """

    provider_id: str = Field(alias="providerId")
    kind: str
    status: RuntimeValidationOutcome
    model: str | None = None
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    reason: str | None = None
    evidence: str | None = None
    checked_at: str = Field(alias="checkedAt")
    attempts: list[RuntimeValidationAttemptRecord] | None = None


class RuntimeValidationResponse(BaseModel):
    """Respuesta de ``models.validate_runtime``."""

    validation: RuntimeValidationResultRecord


RuntimeTeamRole = Literal["product_owner", "developer", "architect", "security"]
RuntimeValidationStatus = Literal["validated", "stale", "failed", "never", "policy_denied"]
RuntimeTeamKind = Literal["cli", "api", "gateway", "local"]


class RuntimeTeamValidationRecord(BaseModel):
    """Estado de validación reciente de un runtime; ``policy_denied`` si el proyecto lo veta."""

    status: RuntimeValidationStatus
    checked_at: str | None = Field(default=None, alias="checkedAt")
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    model: str | None = None
    reason: str | None = None


class RuntimeTeamCandidateRecord(BaseModel):
    """Runtime configurado y habilitado que el operador puede sumar al equipo del hilo."""

    provider_id: str = Field(alias="providerId")
    label: str
    kind: RuntimeTeamKind
    validation: RuntimeTeamValidationRecord
    eligible_roles: list[RuntimeTeamRole] = Field(alias="eligibleRoles")
    loaded_models: list[str] = Field(default_factory=list, alias="loadedModels")


class RoleRuntimesRecord(BaseModel):
    """Runtime asignado por rol del equipo; un rol ausente queda sin asignación."""

    model_config = ConfigDict(extra="forbid")

    product_owner: str | None = None
    developer: str | None = None
    architect: str | None = None
    security: str | None = None


class RuntimeTeamCandidatesResponse(BaseModel):
    """Candidatos del equipo con la ventana de frescura y el reparto sugerido por el backend."""

    candidates: list[RuntimeTeamCandidateRecord]
    freshness_seconds: int = Field(alias="freshnessSeconds")
    suggested_role_runtimes: RoleRuntimesRecord = Field(alias="suggestedRoleRuntimes")
    suggested_role_models: dict[str, str] = Field(default_factory=dict, alias="suggestedRoleModels")


GlobalTeamRole = Literal[
    "product_owner", "developer", "architect", "security", "technical_lead", "researcher"
]
GlobalTeamSource = Literal["project", "general", "automatic", "automatic_fallback", "inherited"]


class GlobalTeamRoleRecord(BaseModel):
    """Un rol del equipo de IA global: orden configurado, orden efectivo, asignado y procedencia."""

    role: GlobalTeamRole
    required: bool
    configured: list[str] = Field(default_factory=list)
    effective: list[str] = Field(default_factory=list)
    assigned: str | None = None
    source: GlobalTeamSource
    invalid: list[str] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)


class ThreadSealedRuntimeTeamResponse(BaseModel):
    """Equipo sellado en el último run del hilo (``thread``: su propio equipo; ``global``: el global)."""

    job_id: str | None = Field(default=None, alias="jobId")
    sealed_at: str | None = Field(default=None, alias="sealedAt")
    source: Literal["thread", "global", "none"]
    role_runtimes: dict[str, str | None] = Field(default_factory=dict, alias="roleRuntimes")
    role_runtime_order: dict[str, list[str]] = Field(default_factory=dict, alias="roleRuntimeOrder")
    role_sources: dict[str, str] = Field(default_factory=dict, alias="roleSources")


class RuntimeTeamResponse(BaseModel):
    """Equipo de IA global efectivo de un proyecto (o general) con los candidatos activos."""

    roles: list[GlobalTeamRoleRecord]
    allowed_runtimes: list[str] = Field(alias="allowedRuntimes")
    active_providers: int = Field(alias="activeProviders")
    candidates: list[RuntimeTeamCandidateRecord]
