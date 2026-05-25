from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .providers.base import ProviderHealth


class GatewayFlexibleModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class ProviderAccountRecord(BaseModel):
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
    provider_id: str = Field(alias="providerId")
    display_name: str | None = Field(default=None, alias="displayName")
    provider_type: str = Field(default="api", alias="providerType")
    api_format: str = Field(default="openai_compatible", alias="apiFormat")
    base_url: str = Field(default="", alias="baseUrl")
    credential_ref: str = Field(default="", alias="credentialRef")
    enabled: bool = False
    quota_mode: str = Field(default="none", alias="quotaMode")
    health_status: str = Field(default="unknown", alias="healthStatus")
    last_health_check_at: str | None = Field(default=None, alias="lastHealthCheckAt")
    last_error: str = Field(default="", alias="lastError")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderAccountPatchRequest(GatewayFlexibleModel):
    display_name: str | None = Field(default=None, alias="displayName")
    provider_type: str | None = Field(default=None, alias="providerType")
    api_format: str | None = Field(default=None, alias="apiFormat")
    base_url: str | None = Field(default=None, alias="baseUrl")
    credential_ref: str | None = Field(default=None, alias="credentialRef")
    enabled: bool | None = None
    quota_mode: str | None = Field(default=None, alias="quotaMode")
    health_status: str | None = Field(default=None, alias="healthStatus")
    last_health_check_at: str | None = Field(default=None, alias="lastHealthCheckAt")
    last_error: str | None = Field(default=None, alias="lastError")
    metadata: dict[str, Any] | None = None


class ProviderAccountResponse(BaseModel):
    provider: ProviderAccountRecord


class ProviderAccountsListResponse(BaseModel):
    providers: list[ProviderAccountRecord]


class ModelCatalogRecord(BaseModel):
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
    model: ModelCatalogRecord


class ModelCatalogListResponse(BaseModel):
    models: list[ModelCatalogRecord]


class PricingSnapshotRecord(BaseModel):
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
    pricing_snapshot: PricingSnapshotRecord = Field(alias="pricingSnapshot")


class PricingSnapshotsListResponse(BaseModel):
    pricing_snapshots: list[PricingSnapshotRecord] = Field(alias="pricingSnapshots")


class RoutingProfileRecord(BaseModel):
    id: str
    name: str
    mode: str
    objective: str
    rules: dict[str, Any]
    enabled: bool
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class RoutingProfileUpsertRequest(GatewayFlexibleModel):
    id: str | None = None
    name: str
    mode: str | None = None
    objective: str = ""
    rules: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class RoutingProfilePatchRequest(GatewayFlexibleModel):
    name: str | None = None
    mode: str | None = None
    objective: str | None = None
    rules: dict[str, Any] | None = None
    enabled: bool | None = None


class RoutingProfileResponse(BaseModel):
    routing_profile: RoutingProfileRecord = Field(alias="routingProfile")


class RoutingProfilesListResponse(BaseModel):
    routing_profiles: list[RoutingProfileRecord] = Field(alias="routingProfiles")


class RolePolicyRecord(BaseModel):
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
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class RolePolicyUpsertRequest(GatewayFlexibleModel):
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


class RolePolicyPatchRequest(RolePolicyUpsertRequest):
    role: str | None = None


class RolePolicyResponse(BaseModel):
    role_policy: RolePolicyRecord = Field(alias="rolePolicy")


class RolePoliciesListResponse(BaseModel):
    role_policies: list[RolePolicyRecord] = Field(alias="rolePolicies")


class RoutingSelection(BaseModel):
    provider: str
    model: str
    runtime: str
    effort: str | None = None


class RoutingCandidateRecord(RoutingSelection):
    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    pricing_source: str = Field(default="unknown", alias="pricingSource")
    pricing_staleness: str = Field(default="unknown", alias="pricingStaleness")
    price_known: bool = Field(default=False, alias="priceKnown")
    free_tier: bool = Field(default=False, alias="freeTier")
    score: float
    score_breakdown: dict[str, float] = Field(alias="scoreBreakdown")


class RoutingRejectedRecord(BaseModel):
    provider: str
    model: str | None = None
    runtime: str | None = None
    reason: str


class RoutingPolicyResult(BaseModel):
    requires_approval: bool = Field(alias="requiresApproval")
    role_policy_id: str = Field(alias="rolePolicyId")
    max_cost_per_task_usd: float | None = Field(default=None, alias="maxCostPerTaskUsd")


class RoutingPreviewRequest(GatewayFlexibleModel):
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
    input_tokens: int = Field(alias="inputTokens")
    cached_input_tokens: int = Field(alias="cachedInputTokens")
    output_tokens: int = Field(alias="outputTokens")
    reasoning_tokens: int = Field(alias="reasoningTokens")
    tool_tokens: int = Field(alias="toolTokens")
    total_tokens: int = Field(alias="totalTokens")
    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    actual_cost_usd: float | None = Field(default=None, alias="actualCostUsd")
    currency: str
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    usage_source: str = Field(alias="usageSource")
    raw_usage: dict[str, Any] = Field(alias="rawUsage")
    created_at: str = Field(alias="createdAt")


class UsageLedgerListResponse(BaseModel):
    usage_ledger: list[UsageLedgerRecord] = Field(alias="usageLedger")


class UsageSummaryProvider(BaseModel):
    provider_id: str = Field(alias="providerId")
    total_tokens: int = Field(alias="totalTokens")
    estimated_cost_usd: float = Field(alias="estimatedCostUsd")


class UsageSummaryRecord(BaseModel):
    total_tokens: int = Field(alias="totalTokens")
    estimated_cost_usd: float = Field(alias="estimatedCostUsd")
    actual_cost_usd: float = Field(alias="actualCostUsd")
    by_provider: list[UsageSummaryProvider] = Field(alias="byProvider")


class UsageSummaryResponse(BaseModel):
    summary: UsageSummaryRecord


class RouteExecuteMockResponse(BaseModel):
    routing: RoutingPreviewResponse
    usage: UsageLedgerRecord


class RouteExecuteResponse(BaseModel):
    routing: RoutingPreviewResponse
    usage: UsageLedgerRecord | None = None
    content: str | None = None


class RoutingDecisionRecord(BaseModel):
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
    routing_decisions: list[RoutingDecisionRecord] = Field(alias="routingDecisions")


class ProviderLimitRecord(BaseModel):
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
    provider_limits: list[ProviderLimitRecord] = Field(alias="providerLimits")


class ProviderLimitResponse(BaseModel):
    provider_limit: ProviderLimitRecord = Field(alias="providerLimit")


class ProviderLimitPatchRequest(GatewayFlexibleModel):
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
    id: str | None = None
    scope_type: str = Field(default="global", alias="scopeType")
    scope_id: str | None = Field(default=None, alias="scopeId")
    max_cost_usd: float | None = Field(default=None, alias="maxCostUsd")
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    period: str = "month"
    action_on_exceed: str = Field(default="require_approval", alias="actionOnExceed")
    enabled: bool = True


class BudgetRulePatchRequest(BudgetRuleUpsertRequest):
    scope_type: str | None = Field(default=None, alias="scopeType")


class BudgetRulesListResponse(BaseModel):
    budget_rules: list[BudgetRuleRecord] = Field(alias="budgetRules")


class BudgetRuleResponse(BaseModel):
    budget_rule: BudgetRuleRecord = Field(alias="budgetRule")


class CliRuntimeRecord(BaseModel):
    id: str | None = None
    runtime: str
    status: str
    executable: str | None = None
    version: str | None = None
    message: str = ""


class CliRuntimesListResponse(BaseModel):
    cli_runtimes: list[CliRuntimeRecord] = Field(alias="cliRuntimes")


class RuntimeDetectionResponse(BaseModel):
    detection: CliRuntimeRecord


class RuntimeHealthRecord(BaseModel):
    runtime: str
    status: str
    message: str = ""


class RuntimeHealthResponse(BaseModel):
    health: RuntimeHealthRecord


class CliSessionRecord(BaseModel):
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
    cli_sessions: list[CliSessionRecord] = Field(alias="cliSessions")


class CliSessionResponse(BaseModel):
    cli_session: CliSessionRecord = Field(alias="cliSession")


class ProviderHealthResponse(BaseModel):
    health: ProviderHealth


class DiscoverModelsResponse(BaseModel):
    models: list[ModelCatalogRecord]


class ModelGatewayOverviewRecord(BaseModel):
    providers_enabled: int = Field(alias="providersEnabled")
    api_providers: int = Field(alias="apiProviders")
    cli_runtimes: int = Field(alias="cliRuntimes")
    local_providers: int = Field(alias="localProviders")
    healthy: int
    degraded: int
    offline: int
    total_tokens_today: int = Field(alias="totalTokensToday")
    estimated_cost_today: float = Field(alias="estimatedCostToday")
    actual_cost_today: float = Field(alias="actualCostToday")
    pending_model_approvals: int = Field(alias="pendingModelApprovals")
    providers_in_cooldown: int = Field(alias="providersInCooldown")
    active_cli_sessions: int = Field(alias="activeCliSessions")


class ModelGatewayOverviewResponse(BaseModel):
    overview: ModelGatewayOverviewRecord


class ModelBenchmarkRecord(BaseModel):
    id: str
    provider_id: str = Field(alias="providerId")
    model: str
    role: str | None = None
    tasks_attempted: int = Field(alias="tasksAttempted")
    success_rate: float | None = Field(default=None, alias="successRate")
    qa_pass_rate: float | None = Field(default=None, alias="qaPassRate")
    avg_cost: float | None = Field(default=None, alias="avgCost")
    avg_latency_ms: int | None = Field(default=None, alias="avgLatencyMs")
    rework_rate: float | None = Field(default=None, alias="reworkRate")
    last_used_at: str | None = Field(default=None, alias="lastUsedAt")
    insufficient_data: bool = Field(alias="insufficientData")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ModelBenchmarksListResponse(BaseModel):
    benchmarks: list[ModelBenchmarkRecord]


class ModelBenchmarkOutcomeRecord(BaseModel):
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
    success: bool | None = None
    qa_pass: bool | None = Field(default=None, alias="qaPass")
    rework: bool | None = None
    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    actual_cost_usd: float | None = Field(default=None, alias="actualCostUsd")
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class ModelBenchmarkOutcomeCreateRequest(GatewayFlexibleModel):
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
    success: bool | None = None
    qa_pass: bool | None = Field(default=None, alias="qaPass")
    rework: bool | None = None
    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    actual_cost_usd: float | None = Field(default=None, alias="actualCostUsd")
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelBenchmarkOutcomeResponse(BaseModel):
    outcome: ModelBenchmarkOutcomeRecord


class ModelBenchmarkOutcomesListResponse(BaseModel):
    outcomes: list[ModelBenchmarkOutcomeRecord]
