"""Modelos Pydantic y literales de tipos del API de agentes (request/response y estados).

Centraliza el contrato de datos del slice de agentes: roles, perfiles, runs, tool calls, políticas de
modelo y los DTO de cada agente (developer/qa/devops/security/architect). Define los Literal de estados
permitidos y los alias camelCase con que viajan hacia/desde la API; no contiene lógica de negocio.
"""

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
    "configuration_required",
    "unavailable",
]
ModelCallStatus = Literal["planned", "completed", "failed", "blocked", "unavailable"]


class ModelProviderCandidate(BaseModel):
    """Par proveedor/modelo candidato dentro de una política de ruteo."""

    provider: str
    model: str


class AgentProfileUpsertRequest(BaseModel):
    """Payload para crear o actualizar un perfil de agente con sus políticas y límites."""

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
    """Perfil de agente persistido, con timestamps y todos los campos resueltos."""

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
    """Respuesta con un único perfil de agente."""

    agent_profile: AgentProfileRecord = Field(alias="agentProfile")


class AgentProfilesListResponse(BaseModel):
    """Respuesta con el listado de perfiles de agente."""

    agent_profiles: list[AgentProfileRecord] = Field(alias="agentProfiles")


class AgentRunCreateRequest(BaseModel):
    """Payload para encolar la ejecución de un agente sobre una tarea de un proyecto."""

    project_id: str = Field(alias="projectId")
    agent_profile_id: str = Field(alias="agentProfileId")
    task_id: str = Field(default="task", alias="taskId")
    input: dict[str, Any] = Field(default_factory=dict)
    job_id: str | None = Field(default=None, alias="jobId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")


class AgentRunRecord(BaseModel):
    """Ejecución de agente persistida, con su estado, input/output y metadatos."""

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
    """Invocación de tool dentro de un run, con su estado de autorización y payload."""

    id: str
    agent_run_id: str = Field(alias="agentRunId")
    tool_name: str = Field(alias="toolName")
    status: AgentToolCallStatus
    payload: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class AgentRunResponse(BaseModel):
    """Respuesta con un único run de agente."""

    agent_run: AgentRunRecord = Field(alias="agentRun")


class AgentRunsListResponse(BaseModel):
    """Respuesta con el listado de runs de agente."""

    agent_runs: list[AgentRunRecord] = Field(alias="agentRuns")


class SkillsSyncRequest(BaseModel):
    """Payload para sincronizar el catálogo de skills desde un directorio."""

    skills_path: str = Field(default="skills", alias="skillsPath")


class SkillRecord(BaseModel):
    """Skill catalogada con su licencia, compatibilidad y nivel de riesgo."""

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
    """Resultado de una sincronización de skills: cuántas se sincronizaron y el catálogo resultante."""

    synced: int
    skills: list[SkillRecord]


class SkillsListResponse(BaseModel):
    """Respuesta con el catálogo de skills disponibles."""

    skills: list[SkillRecord]


class ModelPolicyRecord(BaseModel):
    """Política de modelo persistida: candidatos preferidos/fallback y límites de costo/tokens."""

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
    """Proveedor de modelo registrado, con su política de uso remoto y metadatos."""

    id: str
    provider: str
    label: str
    status: str
    allow_remote: bool = Field(alias="allowRemote")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ModelCallRecord(BaseModel):
    """Llamada a modelo registrada con su consumo de tokens y costo estimado."""

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
    """Registro de costo acumulado por ámbito (proyecto/agente/etc.)."""

    id: str
    project_id: str = Field(alias="projectId")
    scope: str
    amount_usd: float = Field(alias="amountUsd")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class OllamaRuntimeProviderStatus(BaseModel):
    """Estado del runtime Ollama: disponibilidad y modelos locales detectados."""

    provider: str
    available: bool
    models: list[str]
    reason: str = ""


class CliAdaptersStatus(BaseModel):
    """Presencia de los adaptadores CLI de código (codex y claude)."""

    cli_codex: bool
    cli_claude: bool


class CliRuntimeProviderStatus(BaseModel):
    """Estado del runtime CLI con el detalle de sus adaptadores detectados."""

    provider: str
    available: bool
    adapters: CliAdaptersStatus


class ApiRuntimeProviderStatus(BaseModel):
    """Estado del runtime API con la lista de adaptadores disponibles."""

    provider: str
    available: bool
    adapters: list[str]


class RuntimeProviderSafety(BaseModel):
    """Postura de seguridad de un runtime: confinamiento a workspace, shell, argv y red."""

    workspace_bound: bool = Field(default=True, alias="workspaceBound")
    shell: bool = False
    structured_argv: bool = Field(default=True, alias="structuredArgv")
    network: RuntimeProviderNetworkPolicy = "blocked_by_default"


class RuntimeProviderStatus(BaseModel):
    """Estado completo de un runtime provider: detección, salud, capacidades y postura de seguridad."""

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
    """Contrato del DeveloperAgent expuesto por la API (esquemas, tools y capacidades requeridas)."""

    id: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    allowed_tools: list[str] = Field(alias="allowedTools")
    required_runtime_capabilities: list[str] = Field(alias="requiredRuntimeCapabilities")
    required_workspace: bool = Field(alias="requiredWorkspace")
    required_evidence: bool = Field(alias="requiredEvidence")


class DeveloperAgentStatus(BaseModel):
    """Readiness del DeveloperAgent: si es ejecutable, el runtime elegido y los candidatos."""

    id: str
    executable: bool
    status: str
    reason: str
    selected_runtime_id: str | None = Field(default=None, alias="selectedRuntimeId")
    candidate_runtime_ids: list[str] = Field(default_factory=list, alias="candidateRuntimeIds")
    contract: DeveloperAgentContract


class DeveloperAgentStatusResponse(BaseModel):
    """Respuesta con el estado de readiness del DeveloperAgent."""

    developer_agent: DeveloperAgentStatus = Field(alias="developerAgent")


class DeveloperAgentRunRequest(BaseModel):
    """Payload para ejecutar el DeveloperAgent sobre un workspace con una instrucción concreta."""

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
    """Resultado de un run del DeveloperAgent: diff, resultados de QA y paquete de evidencia."""

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
    """Comando de QA a ejecutar (argv), si es crítico para el veredicto y su timeout."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    argv: list[str]
    critical: bool = True
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds")


class QAAgentContract(BaseModel):
    """Contrato del QAAgent expuesto por la API, incluida la fuente de su veredicto."""

    id: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    allowed_tools: list[str] = Field(alias="allowedTools")
    required_runtime_capabilities: list[str] = Field(alias="requiredRuntimeCapabilities")
    required_workspace: bool = Field(alias="requiredWorkspace")
    required_evidence: bool = Field(alias="requiredEvidence")
    verdict_source: str = Field(alias="verdictSource")


class QAAgentRunRequest(BaseModel):
    """Payload para ejecutar el QAAgent con la lista de comandos a correr en el workspace."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    task_id: str = Field(default="qa_agent", alias="taskId")
    commands: list[QAAgentCommandRequest] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class QAAgentRunResponse(BaseModel):
    """Resultado de un run del QAAgent: veredicto, resultados por comando y evidencia."""

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
    """Contrato del DevOpsAgent expuesto por la API, incluida la fuente de su veredicto."""

    id: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    allowed_tools: list[str] = Field(alias="allowedTools")
    required_runtime_capabilities: list[str] = Field(alias="requiredRuntimeCapabilities")
    required_workspace: bool = Field(alias="requiredWorkspace")
    required_evidence: bool = Field(alias="requiredEvidence")
    verdict_source: str = Field(alias="verdictSource")


class DevOpsAgentStatus(BaseModel):
    """Readiness del DevOpsAgent: si sus checks deterministas son ejecutables."""

    id: str
    executable: bool
    status: str
    reason: str
    contract: DevOpsAgentContract


class DevOpsAgentStatusResponse(BaseModel):
    """Respuesta con el estado de readiness del DevOpsAgent."""

    devops_agent: DevOpsAgentStatus = Field(alias="devopsAgent")


class DevOpsAgentRunRequest(BaseModel):
    """Payload para ejecutar el DevOpsAgent: scripts de build/quality y healthcheck opcional."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    task_id: str = Field(default="devops_agent", alias="taskId")
    build_scripts: list[str] = Field(default_factory=list, alias="buildScripts")
    quality_scripts: list[str] | None = Field(default=None, alias="qualityScripts")
    docker_healthcheck: bool = Field(default=False, alias="dockerHealthcheck")
    metadata: dict[str, Any] = Field(default_factory=dict)


class DevOpsAgentRunResponse(BaseModel):
    """Resultado de un run del DevOpsAgent: comandos, hallazgos de config y artefacto de evidencia."""

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
    """Comando candidato (argv) que el SecurityAgent podría ejecutar como escáner externo."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    argv: list[str]


class SecurityAgentContract(BaseModel):
    """Contrato del SecurityAgent expuesto por la API, incluida la fuente de su veredicto."""

    id: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    allowed_tools: list[str] = Field(alias="allowedTools")
    required_runtime_capabilities: list[str] = Field(alias="requiredRuntimeCapabilities")
    required_workspace: bool = Field(alias="requiredWorkspace")
    required_evidence: bool = Field(alias="requiredEvidence")
    verdict_source: str = Field(alias="verdictSource")


class SecurityAgentStatus(BaseModel):
    """Readiness del SecurityAgent: si es ejecutable, el runtime elegido y los candidatos."""

    id: str
    executable: bool
    status: str
    reason: str
    selected_runtime_id: str | None = Field(default=None, alias="selectedRuntimeId")
    candidate_runtime_ids: list[str] = Field(default_factory=list, alias="candidateRuntimeIds")
    contract: SecurityAgentContract


class SecurityAgentStatusResponse(BaseModel):
    """Respuesta con el estado de readiness del SecurityAgent."""

    security_agent: SecurityAgentStatus = Field(alias="securityAgent")


class SecurityAgentRunRequest(BaseModel):
    """Payload para ejecutar el SecurityAgent: diff, escáneres candidatos y rutas a revisar."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    task_id: str = Field(default="security_agent", alias="taskId")
    diff_artifact_id: str | None = Field(default=None, alias="diffArtifactId")
    command_candidates: list[SecurityAgentCommandCandidateRequest] = Field(
        default_factory=list, alias="commandCandidates"
    )
    paths_to_check: list[str] = Field(default_factory=list, alias="pathsToCheck")
    run_model_analysis: bool = Field(default=False, alias="runModelAnalysis")
    preferred_runtime: str | None = Field(default=None, alias="preferredRuntime")
    approval_grant_id: str | None = Field(default=None, alias="approvalGrantId")
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecurityAgentRunResponse(BaseModel):
    """Resultado de un run del SecurityAgent: hallazgos, archivos escaneados y análisis de modelo."""

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


ResearchTrustLevel = Literal[
    "official_documentation",
    "official_repository",
    "standard_rfc",
    "primary_research",
    "reputable_secondary",
    "untrusted",
]
ResearchAgentVerdict = Literal["completed", "blocked", "needs_human_review"]


class ResearchSourceRequest(BaseModel):
    """Fuente investigada por el ResearchAgent, con contenido o URL fetchable y provenance esperado."""

    model_config = ConfigDict(extra="forbid")

    url: str
    publisher: str
    content: str | None = None
    fetched_at: str | None = Field(default=None, alias="fetchedAt")
    trust_level: ResearchTrustLevel | None = Field(default=None, alias="trustLevel")
    related_artifact: str | None = Field(default=None, alias="relatedArtifact")


class ResearchConclusionRequest(BaseModel):
    """Conclusión técnica que debe citar fuentes confiables si proviene de investigación web."""

    model_config = ConfigDict(extra="forbid")

    statement: str
    citations: list[str] = Field(default_factory=list)
    web_based: bool = Field(default=True, alias="webBased")


class ResearchClaimRequest(BaseModel):
    """Claim verificable usado para detectar conflictos entre fuentes sobre un mismo tópico."""

    model_config = ConfigDict(extra="forbid")

    topic: str
    value: str
    source_url: str = Field(alias="sourceUrl")


class ResearchAgentContract(BaseModel):
    """Contrato del ResearchAgent expuesto por la API, incluida su política de fuentes."""

    id: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    allowed_tools: list[str] = Field(alias="allowedTools")
    required_runtime_capabilities: list[str] = Field(alias="requiredRuntimeCapabilities")
    required_workspace: bool = Field(alias="requiredWorkspace")
    required_evidence: bool = Field(alias="requiredEvidence")
    verdict_source: str = Field(alias="verdictSource")
    source_policy: dict[str, Any] = Field(alias="sourcePolicy")


class ResearchAgentStatus(BaseModel):
    """Readiness del ResearchAgent: sus checks deterministas no dependen de runtime de modelo."""

    id: str
    executable: bool
    status: str
    reason: str
    contract: ResearchAgentContract


class ResearchAgentStatusResponse(BaseModel):
    """Respuesta con el estado de readiness del ResearchAgent."""

    research_agent: ResearchAgentStatus = Field(alias="researchAgent")


class ResearchAgentRunRequest(BaseModel):
    """Payload para ejecutar el ResearchAgent con fuentes, conclusiones y claims opcionales."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    task_id: str = Field(default="research_agent", alias="taskId")
    sources: list[ResearchSourceRequest]
    conclusions: list[ResearchConclusionRequest] = Field(default_factory=list)
    claims: list[ResearchClaimRequest] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchAgentRunResponse(BaseModel):
    """Resultado del ResearchAgent: fuentes persistidas, citas, conflictos y evidencia."""

    status: ResearchAgentVerdict
    verdict: ResearchAgentVerdict
    reason: str
    contract: ResearchAgentContract
    workspace: dict[str, Any]
    job: dict[str, Any]
    agent_run: AgentRunRecord = Field(alias="agentRun")
    evidence_package: dict[str, Any] = Field(alias="evidencePackage")
    sources: list[dict[str, Any]]
    conclusions: list[dict[str, Any]]
    citation_check: dict[str, Any] = Field(alias="citationCheck")
    conflict_findings: list[dict[str, Any]] = Field(alias="conflictFindings")
    report_artifact: dict[str, Any] = Field(alias="reportArtifact")


class ArchitectAgentContract(BaseModel):
    """Contrato del ArchitectAgent expuesto por la API, incluida la fuente de su veredicto."""

    id: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    allowed_tools: list[str] = Field(alias="allowedTools")
    required_runtime_capabilities: list[str] = Field(alias="requiredRuntimeCapabilities")
    required_workspace: bool = Field(alias="requiredWorkspace")
    required_evidence: bool = Field(alias="requiredEvidence")
    verdict_source: str = Field(alias="verdictSource")


class ArchitectAgentStatus(BaseModel):
    """Readiness del ArchitectAgent: si es ejecutable, el runtime elegido y los candidatos."""

    id: str
    executable: bool
    status: str
    reason: str
    selected_runtime_id: str | None = Field(default=None, alias="selectedRuntimeId")
    candidate_runtime_ids: list[str] = Field(default_factory=list, alias="candidateRuntimeIds")
    contract: ArchitectAgentContract


class ArchitectAgentStatusResponse(BaseModel):
    """Respuesta con el estado de readiness del ArchitectAgent."""

    architect_agent: ArchitectAgentStatus = Field(alias="architectAgent")


class ArchitectAgentRunRequest(BaseModel):
    """Payload para ejecutar el ArchitectAgent: diff, contexto de workflow y evidencia de soporte."""

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
    """Resultado de un run del ArchitectAgent: decisión de arquitectura, riesgos y evidencia."""

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


class ProductOwnerAgentStatus(BaseModel):
    """Readiness del ProductOwnerAgent: si es ejecutable, el runtime elegido y los candidatos."""

    id: str
    executable: bool
    status: str
    reason: str
    selected_runtime_id: str | None = Field(default=None, alias="selectedRuntimeId")
    candidate_runtime_ids: list[str] = Field(default_factory=list, alias="candidateRuntimeIds")
    contract: dict[str, Any]


class ProductOwnerAgentStatusResponse(BaseModel):
    """Respuesta con el estado de readiness del ProductOwnerAgent."""

    product_owner_agent: ProductOwnerAgentStatus = Field(alias="productOwnerAgent")


class ProductOwnerAgentRunRequest(BaseModel):
    """Payload para ejecutar el ProductOwnerAgent: idea o assessment existente y contexto de workflow."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    task_id: str = Field(default="product_owner_agent", alias="taskId")
    idea: str | None = None
    initiative_id: str | None = Field(default=None, alias="initiativeId")
    completeness_threshold: float | None = Field(default=None, alias="completenessThreshold")
    autonomy: dict[str, Any] | None = None
    workflow_context: dict[str, Any] = Field(default_factory=dict, alias="workflowContext")
    preferred_runtime: str | None = Field(default=None, alias="preferredRuntime")
    approval_grant_id: str | None = Field(default=None, alias="approvalGrantId")
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProductOwnerAgentRunResponse(BaseModel):
    """Resultado de un run del ProductOwnerAgent: completitud, brief, decisiones y backlog."""

    status: str
    reason: str
    product_owner_agent: ProductOwnerAgentStatus = Field(alias="productOwnerAgent")
    workspace: dict[str, Any]
    job: dict[str, Any]
    agent_run: AgentRunRecord = Field(alias="agentRun")
    evidence_package: dict[str, Any] = Field(alias="evidencePackage")
    runtime: dict[str, Any]
    runtime_result: dict[str, Any] = Field(alias="runtimeResult")
    output: dict[str, Any] | None = None
    completeness: dict[str, Any] | None = None
    initiative: dict[str, Any] | None = None
    questions: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: list[dict[str, Any]] = Field(default_factory=list)
    brief: dict[str, Any] | None = None
    blocking_decisions: list[dict[str, Any]] = Field(default_factory=list, alias="blockingDecisions")
    epics: list[dict[str, Any]] = Field(default_factory=list)


class RuntimeProviderConfigurationVariable(BaseModel):
    """Variable de configuración de un runtime: si es requerida, secreta y si está configurada."""

    key: str
    name: str
    required: bool
    secret: bool
    configured: bool
    fingerprint: str | None = None


class RuntimeProviderConfigurationRecord(BaseModel):
    """Estado de configuración de un runtime provider: qué falta y el detalle de sus variables."""

    id: str
    display_name: str = Field(alias="displayName")
    kind: RuntimeProviderKind
    configured: bool
    status: Literal["configured", "configuration_required", "override_unset"]
    reason: str
    missing: list[str]
    variables: list[RuntimeProviderConfigurationVariable]


class RuntimeProviderConfigurationResponse(BaseModel):
    """Respuesta con el estado de configuración de todos los runtime providers."""

    providers: list[RuntimeProviderConfigurationRecord]


class RuntimeProvidersResponse(BaseModel):
    """Vista agregada de runtimes: modos disponibles, estado por tipo y readiness del DeveloperAgent."""

    runtime_modes: list[RuntimeMode] = Field(alias="runtimeModes")
    ollama: OllamaRuntimeProviderStatus
    cli: CliRuntimeProviderStatus
    api: ApiRuntimeProviderStatus
    developer_agent: DeveloperAgentStatus = Field(alias="developerAgent")
    providers: list[RuntimeProviderStatus]
