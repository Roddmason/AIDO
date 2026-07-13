"""Modelos Pydantic del Model Gateway: cuentas, catálogo, precios, ruteo, uso y benchmarks.

Define el contrato de datos de la API del gateway de modelos: records persistidos, requests de
upsert/patch (que aceptan campos extra vía GatewayFlexibleModel) y las respuestas envoltorio. Es solo
esquema, sin lógica de negocio; los Field con alias fijan el camelCase con que viajan por HTTP.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .providers.base import ProviderHealth

BenchmarkProvenance = Literal["operator_reported", "automated_run", "release_validation"]


class GatewayFlexibleModel(BaseModel):
    """Base Pydantic del gateway que admite campos extra y poblar por nombre o alias."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class ProviderAccountRecord(BaseModel):
    """Cuenta de proveedor persistida con su credential ref, salud y metadatos."""

    id: str
    provider_id: str = Field(alias="providerId")
    display_name: str = Field(alias="displayName")
    provider_type: str = Field(alias="providerType")
    api_format: str = Field(alias="apiFormat")
    base_url: str | None = Field(default=None, alias="baseUrl")
    credential_ref: str | None = Field(default=None, alias="credentialRef")
    credential_status: str = Field(alias="credentialStatus")
    enabled: bool
    quota_mode: str = Field(alias="quotaMode")
    health_status: str = Field(alias="healthStatus")
    last_health_check_at: str | None = Field(default=None, alias="lastHealthCheckAt")
    last_error: str = Field(alias="lastError")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ProviderAccountUpsertRequest(GatewayFlexibleModel):
    """Payload para crear o reemplazar una cuenta de proveedor."""

    provider_id: str = Field(alias="providerId")
    display_name: str | None = Field(default=None, alias="displayName")
    provider_type: str = Field(default="api", alias="providerType")
    api_format: str = Field(default="openai_compatible", alias="apiFormat")
    base_url: str = Field(default="", alias="baseUrl")
    credential_ref: str = Field(default="", alias="credentialRef")
    enabled: bool = False
    quota_mode: str = Field(default="none", alias="quotaMode")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderAccountPatchRequest(GatewayFlexibleModel):
    """Payload para modificar parcialmente una cuenta de proveedor."""

    display_name: str | None = Field(default=None, alias="displayName")
    provider_type: str | None = Field(default=None, alias="providerType")
    api_format: str | None = Field(default=None, alias="apiFormat")
    base_url: str | None = Field(default=None, alias="baseUrl")
    credential_ref: str | None = Field(default=None, alias="credentialRef")
    enabled: bool | None = None
    quota_mode: str | None = Field(default=None, alias="quotaMode")
    metadata: dict[str, Any] | None = None


class ProviderAccountResponse(BaseModel):
    """Respuesta con una única cuenta de proveedor."""

    provider: ProviderAccountRecord


class ProviderAccountsListResponse(BaseModel):
    """Respuesta con el listado de cuentas de proveedor."""

    providers: list[ProviderAccountRecord]


class ModelCatalogRecord(BaseModel):
    """Modelo catalogado con sus capacidades, límites y precios por millón de tokens."""

    id: str
    provider_id: str = Field(alias="providerId")
    model: str
    display_name: str = Field(alias="displayName")
    model_family: str = Field(alias="modelFamily")
    context_window: int = Field(alias="contextWindow")
    max_output_tokens: int = Field(alias="maxOutputTokens")
    supports_tools: bool = Field(alias="supportsTools")
    supports_json: bool = Field(alias="supportsJson")
    supports_streaming: bool = Field(alias="supportsStreaming")
    supports_vision: bool = Field(alias="supportsVision")
    supports_embeddings: bool = Field(alias="supportsEmbeddings")
    supports_rerank: bool = Field(alias="supportsRerank")
    supports_reasoning: bool = Field(alias="supportsReasoning")
    supports_thinking: bool = Field(alias="supportsThinking")
    effort_levels: list[str] = Field(alias="effortLevels")
    input_price_per_mtok: float | None = Field(default=None, alias="inputPricePerMtok")
    cached_input_price_per_mtok: float | None = Field(default=None, alias="cachedInputPricePerMtok")
    output_price_per_mtok: float | None = Field(default=None, alias="outputPricePerMtok")
    reasoning_price_per_mtok: float | None = Field(default=None, alias="reasoningPricePerMtok")
    free_tier: bool = Field(alias="freeTier")
    free_tier_notes: str = Field(alias="freeTierNotes")
    enabled: bool
    source: str
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ModelCatalogUpsertRequest(GatewayFlexibleModel):
    """Payload para crear o reemplazar una entrada del catálogo de modelos."""

    provider_id: str = Field(alias="providerId")
    model: str
    display_name: str | None = Field(default=None, alias="displayName")
    model_family: str = Field(default="", alias="modelFamily")
    context_window: int = Field(default=0, alias="contextWindow")
    max_output_tokens: int = Field(default=0, alias="maxOutputTokens")
    supports_tools: bool = Field(default=False, alias="supportsTools")
    supports_json: bool = Field(default=False, alias="supportsJson")
    supports_streaming: bool = Field(default=False, alias="supportsStreaming")
    supports_vision: bool = Field(default=False, alias="supportsVision")
    supports_embeddings: bool = Field(default=False, alias="supportsEmbeddings")
    supports_rerank: bool = Field(default=False, alias="supportsRerank")
    supports_reasoning: bool = Field(default=False, alias="supportsReasoning")
    supports_thinking: bool = Field(default=False, alias="supportsThinking")
    effort_levels: list[str] = Field(default_factory=list, alias="effortLevels")
    input_price_per_mtok: float | None = Field(default=None, alias="inputPricePerMtok")
    cached_input_price_per_mtok: float | None = Field(default=None, alias="cachedInputPricePerMtok")
    output_price_per_mtok: float | None = Field(default=None, alias="outputPricePerMtok")
    reasoning_price_per_mtok: float | None = Field(default=None, alias="reasoningPricePerMtok")
    free_tier: bool = Field(default=False, alias="freeTier")
    free_tier_notes: str = Field(default="", alias="freeTierNotes")
    enabled: bool = True
    source: str = "manual"


class ModelCatalogPatchRequest(GatewayFlexibleModel):
    """Payload para modificar parcialmente una entrada del catálogo de modelos."""

    display_name: str | None = Field(default=None, alias="displayName")
    model_family: str | None = Field(default=None, alias="modelFamily")
    context_window: int | None = Field(default=None, alias="contextWindow")
    max_output_tokens: int | None = Field(default=None, alias="maxOutputTokens")
    supports_tools: bool | None = Field(default=None, alias="supportsTools")
    supports_json: bool | None = Field(default=None, alias="supportsJson")
    supports_streaming: bool | None = Field(default=None, alias="supportsStreaming")
    supports_vision: bool | None = Field(default=None, alias="supportsVision")
    supports_embeddings: bool | None = Field(default=None, alias="supportsEmbeddings")
    supports_rerank: bool | None = Field(default=None, alias="supportsRerank")
    supports_reasoning: bool | None = Field(default=None, alias="supportsReasoning")
    supports_thinking: bool | None = Field(default=None, alias="supportsThinking")
    effort_levels: list[str] | None = Field(default=None, alias="effortLevels")
    input_price_per_mtok: float | None = Field(default=None, alias="inputPricePerMtok")
    cached_input_price_per_mtok: float | None = Field(default=None, alias="cachedInputPricePerMtok")
    output_price_per_mtok: float | None = Field(default=None, alias="outputPricePerMtok")
    reasoning_price_per_mtok: float | None = Field(default=None, alias="reasoningPricePerMtok")
    free_tier: bool | None = Field(default=None, alias="freeTier")
    free_tier_notes: str | None = Field(default=None, alias="freeTierNotes")
    enabled: bool | None = None
    source: str | None = None


class ModelCatalogResponse(BaseModel):
    """Respuesta con un único modelo del catálogo."""

    model: ModelCatalogRecord


class ModelCatalogListResponse(BaseModel):
    """Respuesta con el listado de modelos del catálogo."""

    models: list[ModelCatalogRecord]


class PricingSnapshotRecord(BaseModel):
    """Snapshot de precios de un modelo en un momento dado, con su fuente."""

    id: str
    provider_id: str = Field(alias="providerId")
    model: str
    input_price_per_mtok: float | None = Field(default=None, alias="inputPricePerMtok")
    cached_input_price_per_mtok: float | None = Field(default=None, alias="cachedInputPricePerMtok")
    output_price_per_mtok: float | None = Field(default=None, alias="outputPricePerMtok")
    reasoning_price_per_mtok: float | None = Field(default=None, alias="reasoningPricePerMtok")
    free_tier: bool = Field(alias="freeTier")
    source_ref: str = Field(alias="sourceRef")
    effective_at: str | None = Field(default=None, alias="effectiveAt")
    metadata: dict[str, Any]
    apply_to_catalog: bool = Field(alias="applyToCatalog")
    created_at: str = Field(alias="createdAt")


class PricingSnapshotCreateRequest(GatewayFlexibleModel):
    """Payload para registrar un snapshot de precios, opcionalmente aplicado al catálogo."""

    provider_id: str = Field(alias="providerId")
    model: str
    input_price_per_mtok: float | None = Field(default=None, alias="inputPricePerMtok")
    cached_input_price_per_mtok: float | None = Field(default=None, alias="cachedInputPricePerMtok")
    output_price_per_mtok: float | None = Field(default=None, alias="outputPricePerMtok")
    reasoning_price_per_mtok: float | None = Field(default=None, alias="reasoningPricePerMtok")
    free_tier: bool = Field(default=False, alias="freeTier")
    source_ref: str = Field(default="manual", alias="sourceRef")
    effective_at: str | None = Field(default=None, alias="effectiveAt")
    metadata: dict[str, Any] = Field(default_factory=dict)
    apply_to_catalog: bool = Field(default=False, alias="applyToCatalog")


class PricingSnapshotResponse(BaseModel):
    """Respuesta con un único snapshot de precios."""

    pricing_snapshot: PricingSnapshotRecord = Field(alias="pricingSnapshot")


class PricingSnapshotsListResponse(BaseModel):
    """Respuesta con el listado de snapshots de precios."""

    pricing_snapshots: list[PricingSnapshotRecord] = Field(alias="pricingSnapshots")


class RoutingProfileRecord(BaseModel):
    """Perfil de ruteo persistido con su modo, objetivo y reglas."""

    id: str
    name: str
    mode: str
    objective: str
    rules: dict[str, Any]
    enabled: bool
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class RoutingProfileUpsertRequest(GatewayFlexibleModel):
    """Payload para crear o reemplazar un perfil de ruteo."""

    id: str | None = None
    name: str
    mode: str | None = None
    objective: str = ""
    rules: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class RoutingProfilePatchRequest(GatewayFlexibleModel):
    """Payload para modificar parcialmente un perfil de ruteo."""

    name: str | None = None
    mode: str | None = None
    objective: str | None = None
    rules: dict[str, Any] | None = None
    enabled: bool | None = None


class RoutingProfileResponse(BaseModel):
    """Respuesta con un único perfil de ruteo."""

    routing_profile: RoutingProfileRecord = Field(alias="routingProfile")


class RoutingProfilesListResponse(BaseModel):
    """Respuesta con el listado de perfiles de ruteo."""

    routing_profiles: list[RoutingProfileRecord] = Field(alias="routingProfiles")


class RolePolicyRecord(BaseModel):
    """Política de un rol: candidatos preferidos/fallback/escalación, bloqueos y límites de costo."""

    id: str
    role: str
    routing_profile_id: str = Field(alias="routingProfileId")
    preferred: list[dict[str, Any]]
    fallback: list[dict[str, Any]]
    escalation: list[dict[str, Any]]
    blocked: list[dict[str, Any]]
    max_cost_per_task_usd: float = Field(alias="maxCostPerTaskUsd")
    max_tokens_per_run: int = Field(alias="maxTokensPerRun")
    requires_approval_over_usd: float | None = Field(default=None, alias="requiresApprovalOverUsd")
    requires_approval_for_reasoning_max: bool = Field(alias="requiresApprovalForReasoningMax")
    allow_remote: bool = Field(alias="allowRemote")
    allow_local: bool = Field(alias="allowLocal")
    allow_cli: bool = Field(alias="allowCli")
    allow_api: bool = Field(alias="allowApi")
    allow_unknown_cost: bool = Field(alias="allowUnknownCost")
    require_approval_for_unknown_cost: bool = Field(alias="requireApprovalForUnknownCost")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class RolePolicyUpsertRequest(GatewayFlexibleModel):
    """Payload para crear o reemplazar la política de un rol."""

    id: str | None = None
    role: str
    routing_profile_id: str | None = Field(default=None, alias="routingProfileId")
    preferred: list[dict[str, Any]] = Field(default_factory=list)
    fallback: list[dict[str, Any]] = Field(default_factory=list)
    escalation: list[dict[str, Any]] = Field(default_factory=list)
    blocked: list[dict[str, Any]] = Field(default_factory=list)
    max_cost_per_task_usd: float = Field(default=0, alias="maxCostPerTaskUsd")
    max_tokens_per_run: int = Field(default=0, alias="maxTokensPerRun")
    requires_approval_over_usd: float | None = Field(default=None, alias="requiresApprovalOverUsd")
    requires_approval_for_reasoning_max: bool = Field(default=False, alias="requiresApprovalForReasoningMax")
    allow_remote: bool = Field(default=True, alias="allowRemote")
    allow_local: bool = Field(default=True, alias="allowLocal")
    allow_cli: bool = Field(default=True, alias="allowCli")
    allow_api: bool = Field(default=True, alias="allowApi")
    allow_unknown_cost: bool = Field(default=True, alias="allowUnknownCost")
    require_approval_for_unknown_cost: bool = Field(default=True, alias="requireApprovalForUnknownCost")


class RolePolicyPatchRequest(RolePolicyUpsertRequest):
    """Payload para modificar parcialmente la política de un rol."""

    role: str | None = None


class RolePolicyResponse(BaseModel):
    """Respuesta con una única política de rol."""

    role_policy: RolePolicyRecord = Field(alias="rolePolicy")


class RolePoliciesListResponse(BaseModel):
    """Respuesta con el listado de políticas de rol."""

    role_policies: list[RolePolicyRecord] = Field(alias="rolePolicies")


class RoutingSelection(BaseModel):
    """Selección de ruteo: proveedor, modelo, runtime y esfuerzo elegidos."""

    provider: str
    model: str
    runtime: str
    effort: str | None = None


class RoutingCandidateRecord(RoutingSelection):
    """Candidato de ruteo con su costo estimado, origen de precio y desglose de puntaje."""

    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    pricing_source: str = Field(default="unknown", alias="pricingSource")
    pricing_staleness: str = Field(default="unknown", alias="pricingStaleness")
    price_known: bool = Field(default=False, alias="priceKnown")
    free_tier: bool = Field(default=False, alias="freeTier")
    score: float
    score_breakdown: dict[str, float] = Field(alias="scoreBreakdown")


class RoutingRejectedRecord(BaseModel):
    """Candidato descartado durante el ruteo, con el motivo del rechazo."""

    provider: str
    model: str | None = None
    runtime: str | None = None
    reason: str


class RoutingPolicyResult(BaseModel):
    """Resultado de política del ruteo: si requiere aprobación y la política de costo desconocido."""

    requires_approval: bool = Field(alias="requiresApproval")
    role_policy_id: str = Field(alias="rolePolicyId")
    max_cost_per_task_usd: float | None = Field(default=None, alias="maxCostPerTaskUsd")
    allow_unknown_cost: bool = Field(default=True, alias="allowUnknownCost")
    require_approval_for_unknown_cost: bool = Field(default=True, alias="requireApprovalForUnknownCost")
    unknown_cost_policy: dict[str, Any] = Field(default_factory=dict, alias="unknownCostPolicy")


class RoutingPreviewRequest(GatewayFlexibleModel):
    """Payload de vista previa de ruteo con los requisitos y restricciones de la solicitud."""

    project_id: str | None = Field(default=None, alias="projectId")
    role: str = "developer"
    task_type: str = Field(default="task", alias="taskType")
    mode: str = "balanced_best_value"
    risk_level: str = Field(default="medium", alias="riskLevel")
    context_tokens_estimate: int = Field(default=0, alias="contextTokensEstimate")
    requires_tools: bool = Field(default=False, alias="requiresTools")
    requires_code_edit: bool = Field(default=False, alias="requiresCodeEdit")
    requires_search: bool = Field(default=False, alias="requiresSearch")
    requires_reasoning: bool = Field(default=False, alias="requiresReasoning")
    requires_vision: bool = Field(default=False, alias="requiresVision")
    requires_json: bool = Field(default=False, alias="requiresJson")
    privacy_level: str = Field(default="remote_allowed", alias="privacyLevel")
    budget_remaining_usd: float | None = Field(default=None, alias="budgetRemainingUsd")
    manual_provider: str | None = Field(default=None, alias="manualProvider")
    manual_model: str | None = Field(default=None, alias="manualModel")
    manual_runtime: str | None = Field(default=None, alias="manualRuntime")
    allow_fallback: bool = Field(default=True, alias="allowFallback")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    agent_id: str | None = Field(default=None, alias="agentId")
    job_id: str | None = Field(default=None, alias="jobId")
    task_id: str | None = Field(default=None, alias="taskId")


class RoutingPreviewResponse(BaseModel):
    """Respuesta de vista previa de ruteo: elegido, candidatos, rechazos y resultados de política."""

    selected: RoutingSelection | None
    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    estimated_tokens: int = Field(alias="estimatedTokens")
    decision_reason: str = Field(alias="decisionReason")
    candidates: list[RoutingCandidateRecord]
    rejected: list[RoutingRejectedRecord]
    score_breakdown: dict[str, float] = Field(alias="scoreBreakdown")
    policy_result: RoutingPolicyResult = Field(alias="policyResult")
    budget_result: dict[str, Any] = Field(default_factory=dict, alias="budgetResult")
    quota_result: dict[str, Any] = Field(default_factory=dict, alias="quotaResult")


class UsageLedgerRecord(BaseModel):
    """Asiento del ledger de uso: tokens, costo, latencia y trazabilidad de una llamada."""

    id: str
    provider_id: str = Field(alias="providerId")
    model: str
    runtime_type: str = Field(alias="runtimeType")
    agent_id: str | None = Field(default=None, alias="agentId")
    role: str | None = None
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    job_id: str | None = Field(default=None, alias="jobId")
    task_id: str | None = Field(default=None, alias="taskId")
    request_id: str | None = Field(default=None, alias="requestId")
    session_id: str | None = Field(default=None, alias="sessionId")
    input_tokens: int | None = Field(alias="inputTokens")
    cached_input_tokens: int | None = Field(alias="cachedInputTokens")
    output_tokens: int | None = Field(alias="outputTokens")
    reasoning_tokens: int | None = Field(alias="reasoningTokens")
    tool_tokens: int | None = Field(alias="toolTokens")
    total_tokens: int | None = Field(alias="totalTokens")
    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    actual_cost_usd: float | None = Field(default=None, alias="actualCostUsd")
    currency: str
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    usage_source: str = Field(alias="usageSource")
    token_status: str = Field(default="unknown", alias="tokenStatus")
    cost_status: str = Field(default="unknown", alias="costStatus")
    raw_usage: dict[str, Any] = Field(alias="rawUsage")
    created_at: str = Field(alias="createdAt")


class UsageLedgerListResponse(BaseModel):
    """Respuesta con el listado de asientos del ledger de uso."""

    usage_ledger: list[UsageLedgerRecord] = Field(alias="usageLedger")


class UsageSummaryProvider(BaseModel):
    """Totales de uso agregados por proveedor."""

    provider_id: str = Field(alias="providerId")
    total_tokens: int | None = Field(alias="totalTokens")
    estimated_cost_usd: float | None = Field(alias="estimatedCostUsd")


class UsageSummaryRecord(BaseModel):
    """Resumen de uso: totales globales de tokens y costo y su desglose por proveedor."""

    total_tokens: int | None = Field(alias="totalTokens")
    estimated_cost_usd: float | None = Field(alias="estimatedCostUsd")
    actual_cost_usd: float | None = Field(default=None, alias="actualCostUsd")
    by_provider: list[UsageSummaryProvider] = Field(alias="byProvider")


class UsageSummaryResponse(BaseModel):
    """Respuesta con el resumen de uso agregado."""

    summary: UsageSummaryRecord


class RouteExecuteResponse(BaseModel):
    """Respuesta de ejecución de ruteo: decisión, uso registrado y contenido generado."""

    routing: RoutingPreviewResponse
    usage: UsageLedgerRecord | None = None
    content: str | None = None


class RoutingDecisionRecord(BaseModel):
    """Decisión de ruteo registrada, con lo seleccionado, candidatos, rechazos y política aplicada."""

    id: str
    role: str
    task_type: str = Field(alias="taskType")
    mode: str
    selected_provider: str | None = Field(default=None, alias="selectedProvider")
    selected_model: str | None = Field(default=None, alias="selectedModel")
    selected_runtime: str | None = Field(default=None, alias="selectedRuntime")
    selected_effort: str | None = Field(default=None, alias="selectedEffort")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    agent_id: str | None = Field(default=None, alias="agentId")
    job_id: str | None = Field(default=None, alias="jobId")
    task_id: str | None = Field(default=None, alias="taskId")
    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    estimated_tokens: int | None = Field(default=None, alias="estimatedTokens")
    candidates: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    decision_reason: str = Field(alias="decisionReason")
    score_breakdown: dict[str, Any] = Field(alias="scoreBreakdown")
    policy_result: dict[str, Any] = Field(alias="policyResult")
    created_at: str = Field(alias="createdAt")


class RoutingDecisionsListResponse(BaseModel):
    """Respuesta con el historial de decisiones de ruteo."""

    routing_decisions: list[RoutingDecisionRecord] = Field(alias="routingDecisions")


class ProviderLimitRecord(BaseModel):
    """Límites de un proveedor/modelo (rpm/tpm, cuotas y presupuesto) y su ventana actual."""

    id: str
    provider_id: str = Field(alias="providerId")
    model: str
    rpm: int | None = None
    tpm: int | None = None
    daily_requests: int | None = Field(default=None, alias="dailyRequests")
    daily_tokens: int | None = Field(default=None, alias="dailyTokens")
    monthly_requests: int | None = Field(default=None, alias="monthlyRequests")
    monthly_tokens: int | None = Field(default=None, alias="monthlyTokens")
    monthly_budget_usd: float | None = Field(default=None, alias="monthlyBudgetUsd")
    current_window: dict[str, Any] = Field(alias="currentWindow")
    cooldown_until: str | None = Field(default=None, alias="cooldownUntil")
    last_429_at: str | None = Field(default=None, alias="last429At")
    last_limit_error_at: str | None = Field(default=None, alias="lastLimitErrorAt")
    unknown_limit_strategy: str = Field(alias="unknownLimitStrategy")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ProviderLimitsListResponse(BaseModel):
    """Respuesta con el listado de límites por proveedor."""

    provider_limits: list[ProviderLimitRecord] = Field(alias="providerLimits")


class ProviderLimitResponse(BaseModel):
    """Respuesta con los límites de un único proveedor."""

    provider_limit: ProviderLimitRecord = Field(alias="providerLimit")


class ProviderLimitPatchRequest(GatewayFlexibleModel):
    """Payload para modificar parcialmente los límites de un proveedor."""

    rpm: int | None = None
    tpm: int | None = None
    daily_requests: int | None = Field(default=None, alias="dailyRequests")
    daily_tokens: int | None = Field(default=None, alias="dailyTokens")
    monthly_requests: int | None = Field(default=None, alias="monthlyRequests")
    monthly_tokens: int | None = Field(default=None, alias="monthlyTokens")
    monthly_budget_usd: float | None = Field(default=None, alias="monthlyBudgetUsd")
    current_window: dict[str, Any] | None = Field(default=None, alias="currentWindow")
    cooldown_until: str | None = Field(default=None, alias="cooldownUntil")
    last_429_at: str | None = Field(default=None, alias="last429At")
    last_limit_error_at: str | None = Field(default=None, alias="lastLimitErrorAt")
    unknown_limit_strategy: str | None = Field(default=None, alias="unknownLimitStrategy")


class BudgetRuleRecord(BaseModel):
    """Regla de presupuesto persistida: ámbito, topes, período y acción al excederse."""

    id: str
    scope_type: str = Field(alias="scopeType")
    scope_id: str | None = Field(default=None, alias="scopeId")
    max_cost_usd: float | None = Field(default=None, alias="maxCostUsd")
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    period: str
    action_on_exceed: str = Field(alias="actionOnExceed")
    enabled: bool
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class BudgetRuleUpsertRequest(GatewayFlexibleModel):
    """Payload para crear o reemplazar una regla de presupuesto."""

    id: str | None = None
    scope_type: str = Field(default="global", alias="scopeType")
    scope_id: str | None = Field(default=None, alias="scopeId")
    max_cost_usd: float | None = Field(default=None, alias="maxCostUsd")
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    period: str = "month"
    action_on_exceed: str = Field(default="require_approval", alias="actionOnExceed")
    enabled: bool = True


class BudgetRulePatchRequest(BudgetRuleUpsertRequest):
    """Payload para modificar parcialmente una regla de presupuesto."""

    scope_type: str | None = Field(default=None, alias="scopeType")


class BudgetRulesListResponse(BaseModel):
    """Respuesta con el listado de reglas de presupuesto."""

    budget_rules: list[BudgetRuleRecord] = Field(alias="budgetRules")


class BudgetRuleResponse(BaseModel):
    """Respuesta con una única regla de presupuesto."""

    budget_rule: BudgetRuleRecord = Field(alias="budgetRule")


class CliRuntimeRecord(BaseModel):
    """Estado de un runtime CLI detectado: ejecutable, versión y mensaje."""

    id: str | None = None
    runtime: str
    status: str
    executable: str | None = None
    version: str | None = None
    message: str = ""


class CliRuntimesListResponse(BaseModel):
    """Respuesta con el listado de runtimes CLI detectados."""

    cli_runtimes: list[CliRuntimeRecord] = Field(alias="cliRuntimes")


class RuntimeDetectionResponse(BaseModel):
    """Respuesta con el resultado de detección de un runtime CLI."""

    detection: CliRuntimeRecord


class RuntimeHealthRecord(BaseModel):
    """Estado de salud de un runtime con su mensaje asociado."""

    runtime: str
    status: str
    message: str = ""


class RuntimeHealthResponse(BaseModel):
    """Respuesta con el estado de salud de un runtime."""

    health: RuntimeHealthRecord


class CliSessionRecord(BaseModel):
    """Sesión CLI persistida con sus artefactos de evidencia y referencia de uso."""

    id: str
    runtime: str
    executable: str
    workspace_id: str = Field(alias="workspaceId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    agent_id: str | None = Field(default=None, alias="agentId")
    command: list[Any]
    env_policy: dict[str, Any] = Field(alias="envPolicy")
    status: str
    started_at: str | None = Field(default=None, alias="startedAt")
    finished_at: str | None = Field(default=None, alias="finishedAt")
    usage_ledger_id: str | None = Field(default=None, alias="usageLedgerId")
    stdout_artifact_id: str | None = Field(default=None, alias="stdoutArtifactId")
    stderr_artifact_id: str | None = Field(default=None, alias="stderrArtifactId")
    logs_artifact_id: str | None = Field(default=None, alias="logsArtifactId")
    error: str | None = None
    created_at: str = Field(alias="createdAt")


class CliSessionsListResponse(BaseModel):
    """Respuesta con el listado de sesiones CLI."""

    cli_sessions: list[CliSessionRecord] = Field(alias="cliSessions")


class CliSessionResponse(BaseModel):
    """Respuesta con una única sesión CLI."""

    cli_session: CliSessionRecord = Field(alias="cliSession")


class ProviderHealthResponse(BaseModel):
    """Respuesta con el estado de salud de un proveedor."""

    health: ProviderHealth


class DiscoverModelsResponse(BaseModel):
    """Respuesta con los modelos descubiertos en un proveedor."""

    models: list[ModelCatalogRecord]


class TestPromptRequest(GatewayFlexibleModel):
    """Payload para probar un proveedor con una completion corta y controlada (modelo opcional)."""

    model: str | None = None


class TestPromptRecord(BaseModel):
    """Resultado de una prueba de prompt: éxito, latencia y una muestra redactada de la respuesta."""

    provider_id: str = Field(alias="providerId")
    model: str
    ok: bool
    latency_ms: int = Field(alias="latencyMs")
    sample: str = ""
    total_tokens: int | None = Field(default=None, alias="totalTokens")
    usage_source: str = Field(default="unknown", alias="usageSource")
    error: str | None = None


class TestPromptResponse(BaseModel):
    """Respuesta con el resultado de una prueba de prompt de proveedor."""

    test: TestPromptRecord


class ModelGatewayOverviewRecord(BaseModel):
    """Resumen del gateway: conteos de proveedores/runtimes, salud, costo del día y pendientes."""

    providers_enabled: int = Field(alias="providersEnabled")
    api_providers: int = Field(alias="apiProviders")
    cli_runtimes: int = Field(alias="cliRuntimes")
    local_providers: int = Field(alias="localProviders")
    healthy: int
    degraded: int
    offline: int
    total_tokens_today: int | None = Field(alias="totalTokensToday")
    estimated_cost_today: float = Field(alias="estimatedCostToday")
    actual_cost_today: float | None = Field(default=None, alias="actualCostToday")
    pending_model_approvals: int = Field(alias="pendingModelApprovals")
    providers_in_cooldown: int = Field(alias="providersInCooldown")
    active_cli_sessions: int = Field(alias="activeCliSessions")


class ModelGatewayOverviewResponse(BaseModel):
    """Respuesta con el resumen general del Model Gateway."""

    overview: ModelGatewayOverviewRecord


class ModelBenchmarkRecord(BaseModel):
    """Benchmark agregado de un modelo/rol con sus tasas y conteos por provenance."""

    id: str
    provider_id: str = Field(alias="providerId")
    model: str
    role: str | None = None
    tasks_attempted: int = Field(alias="tasksAttempted")
    objective_tasks_attempted: int = Field(alias="objectiveTasksAttempted")
    operator_reported_tasks: int = Field(alias="operatorReportedTasks")
    automated_run_tasks: int = Field(alias="automatedRunTasks")
    release_validation_tasks: int = Field(alias="releaseValidationTasks")
    success_rate: float | None = Field(default=None, alias="successRate")
    qa_pass_rate: float | None = Field(default=None, alias="qaPassRate")
    avg_cost: float | None = Field(default=None, alias="avgCost")
    avg_latency_ms: int | None = Field(default=None, alias="avgLatencyMs")
    rework_rate: float | None = Field(default=None, alias="reworkRate")
    last_used_at: str | None = Field(default=None, alias="lastUsedAt")
    insufficient_data: bool = Field(alias="insufficientData")
    provenance_counts: dict[str, int] = Field(alias="provenanceCounts")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ModelBenchmarksListResponse(BaseModel):
    """Respuesta con el listado de benchmarks de modelos."""

    benchmarks: list[ModelBenchmarkRecord]


class ModelBenchmarkOutcomeRecord(BaseModel):
    """Outcome individual de benchmark con su provenance y métricas de éxito/QA/rework."""

    id: str
    provider_id: str = Field(alias="providerId")
    model: str
    runtime_type: str = Field(alias="runtimeType")
    role: str | None = None
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    agent_id: str | None = Field(default=None, alias="agentId")
    job_id: str | None = Field(default=None, alias="jobId")
    task_id: str | None = Field(default=None, alias="taskId")
    usage_ledger_id: str | None = Field(default=None, alias="usageLedgerId")
    provenance: BenchmarkProvenance
    success: bool | None = None
    qa_pass: bool | None = Field(default=None, alias="qaPass")
    rework: bool | None = None
    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    actual_cost_usd: float | None = Field(default=None, alias="actualCostUsd")
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class ModelBenchmarkOutcomeCreateRequest(GatewayFlexibleModel):
    """Payload para registrar un outcome de benchmark."""

    provider_id: str = Field(alias="providerId")
    model: str
    runtime_type: str = Field(default="api", alias="runtimeType")
    role: str | None = None
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    agent_id: str | None = Field(default=None, alias="agentId")
    job_id: str | None = Field(default=None, alias="jobId")
    task_id: str | None = Field(default=None, alias="taskId")
    usage_ledger_id: str | None = Field(default=None, alias="usageLedgerId")
    provenance: BenchmarkProvenance = "operator_reported"
    success: bool | None = None
    qa_pass: bool | None = Field(default=None, alias="qaPass")
    rework: bool | None = None
    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    actual_cost_usd: float | None = Field(default=None, alias="actualCostUsd")
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelBenchmarkOutcomeResponse(BaseModel):
    """Respuesta con un único outcome de benchmark."""

    outcome: ModelBenchmarkOutcomeRecord


class ModelBenchmarkOutcomesListResponse(BaseModel):
    """Respuesta con el listado de outcomes de benchmark."""

    outcomes: list[ModelBenchmarkOutcomeRecord]
