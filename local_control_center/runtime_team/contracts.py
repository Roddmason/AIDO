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
    http_status: int | None = Field(default=None, alias="httpStatus")


class RuntimeValidationResultRecord(BaseModel):
    """Resultado redactado de una prueba de ida y vuelta de un runtime.

    ``attempts`` lista cada modelo probado cuando el runtime eligió los candidatos (API/gateway sin
    modelo pedido); ``None`` cuando se probó un único modelo fijado. ``httpStatus`` es el estado HTTP con
    que respondió el proveedor en una falla (``None`` si la petición no llegó o no hubo respuesta HTTP).
    """

    provider_id: str = Field(alias="providerId")
    kind: str
    status: RuntimeValidationOutcome
    model: str | None = None
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    reason: str | None = None
    evidence: str | None = None
    http_status: int | None = Field(default=None, alias="httpStatus")
    checked_at: str = Field(alias="checkedAt")
    attempts: list[RuntimeValidationAttemptRecord] | None = None


class RuntimeValidationResponse(BaseModel):
    """Respuesta de ``models.validate_runtime``."""

    validation: RuntimeValidationResultRecord


RuntimeTeamRole = Literal["product_owner", "developer", "architect", "security"]
RuntimeValidationStatus = Literal["validated", "stale", "failed", "never", "policy_denied"]
RuntimeTeamKind = Literal["cli", "api", "gateway", "local"]


class RuntimeTeamValidationRecord(BaseModel):
    """Estado de validación reciente de un runtime; ``policy_denied`` si el proyecto lo veta.

    ``httpStatus`` acompaña a una falla registrada con el estado HTTP que devolvió el proveedor.
    """

    status: RuntimeValidationStatus
    checked_at: str | None = Field(default=None, alias="checkedAt")
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    model: str | None = None
    reason: str | None = None
    http_status: int | None = Field(default=None, alias="httpStatus")


#: Tope de ids por corrida explícita (mismo margen que el bulk PATCH de modelos).
MAX_CATALOG_VALIDATION_MODELS = 10_000
ModelValidationOutcomeStatus = Literal["ok", "failed", "skipped"]
ModelValidationRunStatus = Literal[
    "running", "completed", "cancelled", "budget_exhausted", "aborted", "interrupted"
]


class CatalogValidationRequest(BaseModel):
    """Cuerpo de ``models.validate_all_models``: qué modelos probar y con qué límites.

    Sin ``models`` prueba todos los modelos habilitados (o solo los que no tienen un resultado de las
    últimas 24 h con ``onlyUntested``); ``models`` son ids del catálogo (``provider:model``) y puede
    incluir modelos descartados para volver a probarlos. La concurrencia, el tope por modelo y el
    presupuesto total quedan acotados para no pasar el sobre de 900 s de una ejecución.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    models: list[str] | None = Field(default=None, max_length=MAX_CATALOG_VALIDATION_MODELS)
    only_untested: bool = Field(default=False, alias="onlyUntested")
    concurrency: int = Field(default=4, ge=1, le=8)
    model_timeout_seconds: float = Field(default=20.0, ge=3, le=60, alias="modelTimeoutSeconds")
    budget_seconds: float = Field(default=600.0, ge=10, le=840, alias="budgetSeconds")


class ModelValidationOutcomeRecord(BaseModel):
    """Último resultado de un modelo con su causa breve y si quedó descartado.

    ``ok``: validado. ``failed``: falla definitiva, descartado si el proveedor tiene otro modelo que pasó.
    ``skipped``: falla transitoria o del endpoint, ni validado ni descartado.
    """

    model_config = ConfigDict(populate_by_name=True)

    model_id: str = Field(alias="modelId")
    model: str
    status: ModelValidationOutcomeStatus
    http_status: int | None = Field(default=None, alias="httpStatus")
    reason: str | None = None
    detail: str | None = None
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    run_id: str | None = Field(default=None, alias="runId")
    tested_at: str = Field(alias="testedAt")
    enabled: bool
    discarded: bool


class ModelValidationRunRecord(BaseModel):
    """Avance de una corrida: hechos/total, ok, fallidos, omitidos y descartados."""

    model_config = ConfigDict(populate_by_name=True)

    run_id: str = Field(alias="runId")
    provider_id: str = Field(alias="providerId")
    status: ModelValidationRunStatus
    reason: str = ""
    total: int
    done: int
    ok: int
    failed: int
    skipped: int
    discarded: int
    concurrency: int
    model_timeout_seconds: float = Field(alias="modelTimeoutSeconds")
    budget_seconds: float = Field(alias="budgetSeconds")
    started_at: str = Field(alias="startedAt")
    updated_at: str = Field(alias="updatedAt")
    finished_at: str | None = Field(default=None, alias="finishedAt")


class CatalogValidationResponse(BaseModel):
    """Resultado de ``models.validate_all_models``: la corrida, sus modelos y el estado del proveedor."""

    run: ModelValidationRunRecord
    outcomes: list[ModelValidationOutcomeRecord]
    provider_validation: RuntimeTeamValidationRecord = Field(alias="providerValidation")


class ModelValidationStatusResponse(BaseModel):
    """Lectura sin red del avance: última corrida del proveedor y último resultado por modelo."""

    run: ModelValidationRunRecord | None = None
    outcomes: list[ModelValidationOutcomeRecord]
    untested: int
    provider_validation: RuntimeTeamValidationRecord = Field(alias="providerValidation")


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
