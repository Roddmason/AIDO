from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AgentRole = Literal[
    "analyst",
    "product_owner",
    "technical_lead",
    "technical_lead_shadow",
    "developer",
    "backend_engineer",
    "frontend_engineer",
    "implementer",
    "devops",
    "qa",
    "qa_reviewer",
    "security_reviewer",
    "release_manager",
]
PermissionProfile = Literal["plan", "dev_safe", "qa", "release"]
RuntimeMode = Literal["api", "cli", "ollama", "hybrid", "manual"]
PolicyStatus = Literal["active", "disabled"]
RuntimeProviderKind = Literal["api", "gateway", "local", "cli", "manual"]
RuntimeProviderNetworkPolicy = Literal[
    "blocked_by_default",
    "runtime_policy_gated",
    "remote_calls_disabled_by_default",
    "local_only",
]
AgentRunStatus = Literal[
    "queued",
    "running",
    "completed",
    "approved",
    "failed",
    "blocked",
    "runtime_unavailable",
    "qa_failed",
    "evidence_ready",
    "approval_required",
    "awaiting_permission",
    "cancelled",
]
AgentToolCallStatus = Literal[
    "pending",
    "allowed",
    "denied",
    "requires_approval",
    "approval_required",
    "completed",
    "failed",
    "blocked",
]
ModelCallStatus = Literal["planned", "completed", "failed", "blocked", "unavailable"]


class ModelProviderCandidate(BaseModel):
    provider: str
    model: str


class AgentProfileUpsertRequest(BaseModel):
    id: str
    name: str | None = None
    role: AgentRole = "implementer"
    runtime_mode: RuntimeMode = Field(default="hybrid", alias="runtimeMode")
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
    status: AgentRunStatus
    input: dict[str, Any]
    output: dict[str, Any]
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class AgentToolCallRecord(BaseModel):
    id: str
    agent_run_id: str = Field(alias="agentRunId")
    tool_name: str = Field(alias="toolName")
    status: AgentToolCallStatus
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
    status: ModelCallStatus
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


class RuntimeProviderSafety(BaseModel):
    workspace_bound: bool = Field(default=True, alias="workspaceBound")
    shell: bool = False
    structured_argv: bool = Field(default=True, alias="structuredArgv")
    network: RuntimeProviderNetworkPolicy = "blocked_by_default"


class RuntimeProviderStatus(BaseModel):
    id: str
    kind: RuntimeProviderKind
    display_name: str = Field(alias="displayName")
    detected: bool = False
    configured: bool
    available: bool
    executable: bool
    requires_approval: bool = Field(default=True, alias="requiresApproval")
    reason: str
    version: str | None = None
    detected_command: str | None = Field(default=None, alias="detectedCommand")
    health_status: str = Field(default="unknown", alias="healthStatus")
    health_checked_at: str | None = Field(default=None, alias="healthCheckedAt")
    last_error: str = Field(default="", alias="lastError")
    capabilities: list[str] = Field(default_factory=list)
    required_configuration: list[str] = Field(default_factory=list, alias="requiredConfiguration")
    safety: RuntimeProviderSafety = Field(default_factory=RuntimeProviderSafety)


class DeveloperAgentContract(BaseModel):
    id: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    allowed_tools: list[str] = Field(alias="allowedTools")
    required_runtime_capabilities: list[str] = Field(alias="requiredRuntimeCapabilities")
    required_workspace: bool = Field(alias="requiredWorkspace")
    required_evidence: bool = Field(alias="requiredEvidence")


class DeveloperAgentStatus(BaseModel):
    id: str
    executable: bool
    status: str
    reason: str
    selected_runtime_id: str | None = Field(default=None, alias="selectedRuntimeId")
    candidate_runtime_ids: list[str] = Field(default_factory=list, alias="candidateRuntimeIds")
    contract: DeveloperAgentContract


class DeveloperAgentStatusResponse(BaseModel):
    developer_agent: DeveloperAgentStatus = Field(alias="developerAgent")


class DeveloperAgentRunRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    task_id: str = Field(default="developer_agent", alias="taskId")
    instruction: str
    preferred_runtime: str | None = Field(default=None, alias="preferredRuntime")
    qa_commands: list[list[str]] = Field(default_factory=list, alias="qaCommands")
    require_approval: bool = Field(default=True, alias="requireApproval")
    max_cost_usd: float | None = Field(default=None, alias="maxCostUsd")
    approval_grant_id: str | None = Field(default=None, alias="approvalGrantId")
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeveloperAgentRunResponse(BaseModel):
    status: str
    reason: str
    developer_agent: DeveloperAgentStatus = Field(alias="developerAgent")
    workspace: dict[str, Any]
    job: dict[str, Any]
    agent_run: AgentRunRecord = Field(alias="agentRun")
    evidence_package: dict[str, Any] = Field(alias="evidencePackage")
    runtime: dict[str, Any]
    runtime_result: dict[str, Any] = Field(alias="runtimeResult")
    qa_results: list[dict[str, Any]] = Field(alias="qaResults")
    diff_summary: dict[str, Any] = Field(alias="diffSummary")


class QAAgentCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    argv: list[str]
    critical: bool = True
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds")


class QAAgentContract(BaseModel):
    id: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    allowed_tools: list[str] = Field(alias="allowedTools")
    required_runtime_capabilities: list[str] = Field(alias="requiredRuntimeCapabilities")
    required_workspace: bool = Field(alias="requiredWorkspace")
    required_evidence: bool = Field(alias="requiredEvidence")
    verdict_source: str = Field(alias="verdictSource")


class QAAgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    task_id: str = Field(default="qa_agent", alias="taskId")
    commands: list[QAAgentCommandRequest] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class QAAgentRunResponse(BaseModel):
    status: str
    verdict: str
    reason: str
    contract: QAAgentContract
    workspace: dict[str, Any]
    job: dict[str, Any]
    agent_run: AgentRunRecord = Field(alias="agentRun")
    evidence_package: dict[str, Any] = Field(alias="evidencePackage")
    results: list[dict[str, Any]]


class DevOpsAgentContract(BaseModel):
    id: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    allowed_tools: list[str] = Field(alias="allowedTools")
    required_runtime_capabilities: list[str] = Field(alias="requiredRuntimeCapabilities")
    required_workspace: bool = Field(alias="requiredWorkspace")
    required_evidence: bool = Field(alias="requiredEvidence")
    verdict_source: str = Field(alias="verdictSource")


class DevOpsAgentStatus(BaseModel):
    id: str
    executable: bool
    status: str
    reason: str
    contract: DevOpsAgentContract


class DevOpsAgentStatusResponse(BaseModel):
    devops_agent: DevOpsAgentStatus = Field(alias="devopsAgent")


class DevOpsAgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    task_id: str = Field(default="devops_agent", alias="taskId")
    build_scripts: list[str] = Field(default_factory=list, alias="buildScripts")
    quality_scripts: list[str] | None = Field(default=None, alias="qualityScripts")
    docker_healthcheck: bool = Field(default=False, alias="dockerHealthcheck")
    metadata: dict[str, Any] = Field(default_factory=dict)


class DevOpsAgentRunResponse(BaseModel):
    status: str
    verdict: str
    reason: str
    contract: DevOpsAgentContract
    workspace: dict[str, Any]
    job: dict[str, Any]
    agent_run: AgentRunRecord = Field(alias="agentRun")
    evidence_package: dict[str, Any] = Field(alias="evidencePackage")
    commands: list[dict[str, Any]]
    versions: dict[str, Any]
    config_findings: list[dict[str, Any]] = Field(alias="configFindings")
    files_scanned: list[dict[str, Any]] = Field(alias="filesScanned")
    docker: dict[str, Any]
    config_artifact: dict[str, Any] = Field(alias="configArtifact")


class SecurityAgentCommandCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    argv: list[str]


class SecurityAgentContract(BaseModel):
    id: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    allowed_tools: list[str] = Field(alias="allowedTools")
    required_runtime_capabilities: list[str] = Field(alias="requiredRuntimeCapabilities")
    required_workspace: bool = Field(alias="requiredWorkspace")
    required_evidence: bool = Field(alias="requiredEvidence")
    verdict_source: str = Field(alias="verdictSource")


class SecurityAgentStatus(BaseModel):
    id: str
    executable: bool
    status: str
    reason: str
    selected_runtime_id: str | None = Field(default=None, alias="selectedRuntimeId")
    candidate_runtime_ids: list[str] = Field(default_factory=list, alias="candidateRuntimeIds")
    contract: SecurityAgentContract


class SecurityAgentStatusResponse(BaseModel):
    security_agent: SecurityAgentStatus = Field(alias="securityAgent")


class SecurityAgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    task_id: str = Field(default="security_agent", alias="taskId")
    diff_artifact_id: str | None = Field(default=None, alias="diffArtifactId")
    command_candidates: list[SecurityAgentCommandCandidateRequest] = Field(default_factory=list, alias="commandCandidates")
    paths_to_check: list[str] = Field(default_factory=list, alias="pathsToCheck")
    run_model_analysis: bool = Field(default=False, alias="runModelAnalysis")
    preferred_runtime: str | None = Field(default=None, alias="preferredRuntime")
    approval_grant_id: str | None = Field(default=None, alias="approvalGrantId")
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecurityAgentRunResponse(BaseModel):
    status: str
    verdict: str
    reason: str
    contract: SecurityAgentContract
    workspace: dict[str, Any]
    job: dict[str, Any]
    agent_run: AgentRunRecord = Field(alias="agentRun")
    evidence_package: dict[str, Any] = Field(alias="evidencePackage")
    findings: list[dict[str, Any]]
    files_scanned: list[dict[str, Any]] = Field(alias="filesScanned")
    dependency_files: list[dict[str, Any]] = Field(alias="dependencyFiles")
    external_scanners: list[dict[str, Any]] = Field(default_factory=list, alias="externalScanners")
    findings_artifact: dict[str, Any] = Field(alias="findingsArtifact")
    model_analysis: dict[str, Any] | None = Field(default=None, alias="modelAnalysis")


class ArchitectAgentContract(BaseModel):
    id: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    allowed_tools: list[str] = Field(alias="allowedTools")
    required_runtime_capabilities: list[str] = Field(alias="requiredRuntimeCapabilities")
    required_workspace: bool = Field(alias="requiredWorkspace")
    required_evidence: bool = Field(alias="requiredEvidence")
    verdict_source: str = Field(alias="verdictSource")


class ArchitectAgentStatus(BaseModel):
    id: str
    executable: bool
    status: str
    reason: str
    selected_runtime_id: str | None = Field(default=None, alias="selectedRuntimeId")
    candidate_runtime_ids: list[str] = Field(default_factory=list, alias="candidateRuntimeIds")
    contract: ArchitectAgentContract


class ArchitectAgentStatusResponse(BaseModel):
    architect_agent: ArchitectAgentStatus = Field(alias="architectAgent")


class ArchitectAgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    task_id: str = Field(default="architect_agent", alias="taskId")
    diff_artifact_id: str = Field(alias="diffArtifactId")
    workflow_context: dict[str, Any] = Field(default_factory=dict, alias="workflowContext")
    relevant_docs: list[dict[str, Any]] = Field(default_factory=list, alias="relevantDocs")
    test_results: list[dict[str, Any]] = Field(default_factory=list, alias="testResults")
    risk_register: list[dict[str, Any]] = Field(default_factory=list, alias="riskRegister")
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs")
    preferred_runtime: str | None = Field(default=None, alias="preferredRuntime")
    approval_grant_id: str | None = Field(default=None, alias="approvalGrantId")
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArchitectAgentRunResponse(BaseModel):
    status: str
    reason: str
    architect_agent: ArchitectAgentStatus = Field(alias="architectAgent")
    workspace: dict[str, Any]
    job: dict[str, Any]
    agent_run: AgentRunRecord = Field(alias="agentRun")
    evidence_package: dict[str, Any] = Field(alias="evidencePackage")
    runtime: dict[str, Any]
    runtime_result: dict[str, Any] = Field(alias="runtimeResult")
    output: dict[str, Any] | None = None
    architecture_decision: dict[str, Any] | None = Field(default=None, alias="architectureDecision")
    risk_entries: list[dict[str, Any]] = Field(default_factory=list, alias="riskEntries")


class RuntimeProviderConfigurationVariable(BaseModel):
    key: str
    name: str
    required: bool
    secret: bool
    configured: bool
    fingerprint: str | None = None


class RuntimeProviderConfigurationRecord(BaseModel):
    id: str
    display_name: str = Field(alias="displayName")
    kind: RuntimeProviderKind
    configured: bool
    status: Literal["configured", "configuration_required"]
    reason: str
    missing: list[str]
    variables: list[RuntimeProviderConfigurationVariable]


class RuntimeProviderConfigurationResponse(BaseModel):
    providers: list[RuntimeProviderConfigurationRecord]


class RuntimeProvidersResponse(BaseModel):
    runtime_modes: list[RuntimeMode] = Field(alias="runtimeModes")
    ollama: OllamaRuntimeProviderStatus
    cli: CliRuntimeProviderStatus
    api: ApiRuntimeProviderStatus
    developer_agent: DeveloperAgentStatus = Field(alias="developerAgent")
    providers: list[RuntimeProviderStatus]
