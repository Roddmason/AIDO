from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


AgentRole = Literal[
    "analyst",
    "product_owner",
    "technical_lead",
    "developer",
    "implementer",
    "qa",
    "qa_reviewer",
    "security_reviewer",
    "release_manager",
]
PermissionProfile = Literal["plan", "dev_safe", "qa", "release"]
RuntimeMode = Literal["api", "cli", "ollama", "hybrid", "manual", "internal_mock"]
PolicyStatus = Literal["active", "disabled"]


class ModelProviderCandidate(BaseModel):
    provider: str
    model: str


class AgentProfileUpsertRequest(BaseModel):
    id: str
    name: str | None = None
    role: AgentRole = "implementer"
    runtime_mode: RuntimeMode = Field(default="internal_mock", alias="runtimeMode")
    runtime_type: RuntimeMode | None = Field(default=None, alias="runtimeType")
    model_policy_id: str | None = Field(default=None, alias="modelPolicyId")
    routing_profile_id: str | None = Field(default=None, alias="routingProfileId")
    role_model_policy_id: str | None = Field(default=None, alias="roleModelPolicyId")
    allowed_providers: list[str] = Field(default_factory=list, alias="allowedProviders")
    allowed_runtimes: list[str] = Field(default_factory=list, alias="allowedRuntimes")
    allowed_skills: list[str] = Field(default_factory=list, alias="allowedSkills")
    allowed_tools: list[str] = Field(default_factory=list, alias="allowedTools")
    permission_profile: PermissionProfile = Field(default="plan", alias="permissionProfile")
    memory_scope: str = Field(default="project", alias="memoryScope")
    max_cost_per_run: float = Field(default=0, alias="maxCostPerRun")
    max_tokens_per_run: int = Field(default=0, alias="maxTokensPerRun")
    max_runtime_seconds: int = Field(default=900, alias="maxRuntimeSeconds")
    allow_remote: bool = Field(default=True, alias="allowRemote")
    allow_cli: bool = Field(default=True, alias="allowCli")
    allow_api: bool = Field(default=True, alias="allowApi")
    requires_approval_over_usd: float | None = Field(default=None, alias="requiresApprovalOverUsd")
    output_schema: dict[str, Any] = Field(default_factory=dict, alias="outputSchema")
    quality_gates: list[Any] = Field(default_factory=list, alias="qualityGates")
    status: PolicyStatus = "active"


class AgentProfileRecord(BaseModel):
    id: str
    name: str
    role: AgentRole
    runtime_type: RuntimeMode = Field(alias="runtimeType")
    runtime_mode: RuntimeMode = Field(alias="runtimeMode")
    model_policy_id: str | None = Field(default=None, alias="modelPolicyId")
    routing_profile_id: str | None = Field(default=None, alias="routingProfileId")
    role_model_policy_id: str | None = Field(default=None, alias="roleModelPolicyId")
    allowed_providers: list[str] = Field(alias="allowedProviders")
    allowed_runtimes: list[str] = Field(alias="allowedRuntimes")
    allowed_skills: list[str] = Field(alias="allowedSkills")
    allowed_tools: list[str] = Field(alias="allowedTools")
    permission_profile: PermissionProfile = Field(alias="permissionProfile")
    memory_scope: str = Field(alias="memoryScope")
    max_cost_per_run: float = Field(alias="maxCostPerRun")
    max_tokens_per_run: int = Field(alias="maxTokensPerRun")
    max_runtime_seconds: int = Field(alias="maxRuntimeSeconds")
    allow_remote: bool = Field(alias="allowRemote")
    allow_cli: bool = Field(alias="allowCli")
    allow_api: bool = Field(alias="allowApi")
    requires_approval_over_usd: float | None = Field(default=None, alias="requiresApprovalOverUsd")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    quality_gates: list[Any] = Field(alias="qualityGates")
    status: PolicyStatus
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class AgentProfileResponse(BaseModel):
    agent_profile: AgentProfileRecord = Field(alias="agentProfile")


class AgentProfilesListResponse(BaseModel):
    agent_profiles: list[AgentProfileRecord] = Field(alias="agentProfiles")


class AgentRunCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    agent_profile_id: str = Field(alias="agentProfileId")
    task_id: str = Field(default="task", alias="taskId")
    input: dict[str, Any] = Field(default_factory=dict)
    job_id: str | None = Field(default=None, alias="jobId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")


class AgentRunRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    job_id: str | None = Field(default=None, alias="jobId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    status: str
    input: dict[str, Any]
    output: dict[str, Any]
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class AgentToolCallRecord(BaseModel):
    id: str
    agent_run_id: str = Field(alias="agentRunId")
    tool_name: str = Field(alias="toolName")
    status: str
    payload: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class AgentRunResponse(BaseModel):
    agent_run: AgentRunRecord = Field(alias="agentRun")


class AgentRunsListResponse(BaseModel):
    agent_runs: list[AgentRunRecord] = Field(alias="agentRuns")


class SkillsSyncRequest(BaseModel):
    skills_path: str = Field(default="skills", alias="skillsPath")


class SkillRecord(BaseModel):
    id: str
    name: str
    description: str
    license: str
    compatibility: str
    risk_level: str = Field(alias="riskLevel")
    path: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class SkillsSyncResponse(BaseModel):
    synced: int
    skills: list[SkillRecord]


class SkillsListResponse(BaseModel):
    skills: list[SkillRecord]


class ModelPolicyUpsertRequest(BaseModel):
    id: str
    name: str | None = None
    preferred: list[ModelProviderCandidate] = Field(default_factory=list)
    fallback: list[ModelProviderCandidate] = Field(default_factory=list)
    max_cost_usd: float = Field(default=0, alias="maxCostUsd")
    max_tokens: int = Field(default=0, alias="maxTokens")
    temperature: float = 0.2
    allow_remote: bool = Field(default=True, alias="allowRemote")
    allow_local: bool = Field(default=True, alias="allowLocal")
    status: PolicyStatus = "active"


class ModelPolicyRecord(BaseModel):
    id: str
    name: str
    preferred: list[ModelProviderCandidate]
    fallback: list[ModelProviderCandidate]
    max_cost_usd: float = Field(alias="maxCostUsd")
    max_tokens: int = Field(alias="maxTokens")
    temperature: float
    allow_remote: bool = Field(alias="allowRemote")
    allow_local: bool = Field(alias="allowLocal")
    status: PolicyStatus
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ModelProviderRecord(BaseModel):
    id: str
    provider: str
    label: str
    status: str
    allow_remote: bool = Field(alias="allowRemote")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ModelCallRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    agent_run_id: str | None = Field(default=None, alias="agentRunId")
    model_policy_id: str | None = Field(default=None, alias="modelPolicyId")
    provider: str
    model: str
    status: str
    prompt_tokens: int = Field(alias="promptTokens")
    completion_tokens: int = Field(alias="completionTokens")
    cost_usd: float = Field(alias="costUsd")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class CostUsageRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    scope: str
    amount_usd: float = Field(alias="amountUsd")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class ModelPolicyResponse(BaseModel):
    model_policy: ModelPolicyRecord = Field(alias="modelPolicy")


class ModelProvidersListResponse(BaseModel):
    model_providers: list[ModelProviderRecord] = Field(alias="modelProviders")


class OllamaRuntimeProviderStatus(BaseModel):
    provider: str
    available: bool
    models: list[str]
    reason: str = ""


class CliAdaptersStatus(BaseModel):
    cli_codex: bool
    cli_claude: bool


class CliRuntimeProviderStatus(BaseModel):
    provider: str
    available: bool
    adapters: CliAdaptersStatus


class ApiRuntimeProviderStatus(BaseModel):
    provider: str
    available: bool
    adapters: list[str]


class RuntimeProvidersResponse(BaseModel):
    runtime_modes: list[RuntimeMode] = Field(alias="runtimeModes")
    ollama: OllamaRuntimeProviderStatus
    cli: CliRuntimeProviderStatus
    api: ApiRuntimeProviderStatus


class ModelPoliciesListResponse(BaseModel):
    model_policies: list[ModelPolicyRecord] = Field(alias="modelPolicies")
