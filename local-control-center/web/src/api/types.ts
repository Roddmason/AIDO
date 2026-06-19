/**
 * Domain-facing aliases over the generated OpenAPI record types.
 *
 * Maps verbose generated names (e.g. `ProjectRecord`) to the short vocabulary the
 * UI uses (`Project`), and narrows a few enum-like unions from request shapes so
 * components depend on intent-named types instead of the raw generated surface.
 */
import type {
	ActionRequestRecord,
	AgentProfileRecord,
	AgentProfileUpsertRequest,
	ArchitectureDecisionCreateRequest,
	ArtifactRecord,
	BudgetRuleRecord,
	ChatRecord,
	CliRuntimeRecord,
	CliSessionRecord,
	EventRecord as GeneratedEventRecord,
	RiskRecord as GeneratedRiskRecord,
	JobRecord,
	McpServerRegisterRequest,
	ModelBenchmarkOutcomeRecord,
	ModelBenchmarkRecord,
	ModelCatalogRecord,
	ModelGatewayOverviewRecord,
	ModelProviderRecord,
	NextStepCreateRequest,
	OverviewResponse,
	PipelineRecord,
	PolicyRevisionRecord,
	ProjectCreateRequest,
	ProjectRecord,
	ProjectResponse,
	ProjectTemplateRecord,
	ProviderAccountRecord,
	ProviderLimitRecord,
	ProviderRecord,
	RetrievalStatusResponse,
	RiskCreateRequest,
	RolePolicyRecord,
	RoutingDecisionRecord,
	RoutingProfileRecord,
	RuntimeProviderConfigurationRecord,
	RuntimeProviderStatus,
	RuntimeProvidersResponse,
	SessionRecord,
	TeamRecord,
	UsageLedgerRecord,
	UsageSummaryRecord,
	WorkflowCreateRequest,
	WorkflowRecord,
	WorkflowStepRecord,
} from './generated/openapi';

export type Dictionary = Record<string, unknown>;

export type Project = ProjectRecord;
export type ProjectCreate = ProjectCreateRequest;
export type ProjectCreateResult = ProjectResponse;
export type ProjectTemplate = ProjectTemplateRecord;
export type Provider = ProviderRecord;
export type Team = TeamRecord;
export type Job = JobRecord;
export type ActionRequest = ActionRequestRecord;
export type EventRecord = GeneratedEventRecord & { severity?: string };
export type Workflow = WorkflowRecord;
export type WorkflowStep = WorkflowStepRecord;
export type AgentProfile = AgentProfileRecord;
export type ModelProvider = ModelProviderRecord;
export type Session = SessionRecord;
export type Chat = ChatRecord;
export type Pipeline = PipelineRecord;
export type Artifact = ArtifactRecord;
export type PolicyRevision = PolicyRevisionRecord;
export type Overview = OverviewResponse;
export type RuntimeProviders = RuntimeProvidersResponse;
export type RuntimeProvider = RuntimeProviderStatus;
export type RuntimeProviderConfiguration = RuntimeProviderConfigurationRecord;
export type RetrievalStatus = RetrievalStatusResponse;
export type ModelGatewayOverview = ModelGatewayOverviewRecord;
export type ModelGatewayProviderAccount = ProviderAccountRecord;
export type ModelGatewayModel = ModelCatalogRecord;
export type ModelGatewayRoutingProfile = RoutingProfileRecord;
export type ModelGatewayRolePolicy = RolePolicyRecord;
export type ModelGatewayUsage = UsageLedgerRecord;
export type ModelGatewayUsageSummary = UsageSummaryRecord;
export type ModelGatewayRoutingDecision = RoutingDecisionRecord;
export type ModelGatewayProviderLimit = ProviderLimitRecord;
export type ModelGatewayBudgetRule = BudgetRuleRecord;
export type ModelGatewayCliRuntime = CliRuntimeRecord;
export type ModelGatewayCliSession = CliSessionRecord;
export type ModelGatewayBenchmark = ModelBenchmarkRecord;
export type ModelGatewayBenchmarkOutcome = ModelBenchmarkOutcomeRecord;
export type AgentRole = NonNullable<AgentProfileUpsertRequest['role']>;
export type AgentRuntimeMode = NonNullable<AgentProfileUpsertRequest['runtimeMode']>;
export type PermissionProfile = NonNullable<AgentProfileUpsertRequest['permissionProfile']>;
export type WorkflowKind = NonNullable<WorkflowCreateRequest['kind']>;
export type RiskSeverity = NonNullable<RiskCreateRequest['severity']>;
export type RiskStatus = GeneratedRiskRecord['status'];
export type ArchitectureDecisionStatus = NonNullable<ArchitectureDecisionCreateRequest['status']>;
export type NextStepPriority = NonNullable<NextStepCreateRequest['priority']>;
export type McpTransport = NonNullable<McpServerRegisterRequest['transport']>;
