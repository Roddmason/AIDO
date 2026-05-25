import type {
	ActionRequestRecord,
	AgentProfileRecord,
	AgentProfileUpsertRequest,
	ArchitectureDecisionCreateRequest,
	ArtifactRecord,
	ChatRecord,
	EventRecord as GeneratedEventRecord,
	JobRecord,
	McpServerRegisterRequest,
	ModelProviderRecord,
	NextStepCreateRequest,
	OverviewResponse,
	PipelineRecord,
	PolicyRevisionRecord,
	ProjectCreateRequest,
	ProjectRecord,
	ProjectResponse,
	ProjectTemplateRecord,
	ProviderRecord,
	RetrievalStatusResponse,
	RiskCreateRequest,
	RiskRecord as GeneratedRiskRecord,
	RuntimeProvidersResponse,
	SessionRecord,
	TeamRecord,
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
export type RetrievalStatus = RetrievalStatusResponse;
export type AgentRole = NonNullable<AgentProfileUpsertRequest['role']>;
export type AgentRuntimeMode = NonNullable<AgentProfileUpsertRequest['runtimeMode']>;
export type PermissionProfile = NonNullable<AgentProfileUpsertRequest['permissionProfile']>;
export type WorkflowKind = NonNullable<WorkflowCreateRequest['kind']>;
export type RiskSeverity = NonNullable<RiskCreateRequest['severity']>;
export type RiskStatus = GeneratedRiskRecord['status'];
export type ArchitectureDecisionStatus = NonNullable<ArchitectureDecisionCreateRequest['status']>;
export type NextStepPriority = NonNullable<NextStepCreateRequest['priority']>;
export type McpTransport = NonNullable<McpServerRegisterRequest['transport']>;
