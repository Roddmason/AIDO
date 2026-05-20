import type {
	ActionRequestRecord,
	AgentProfileRecord,
	ChatRecord,
	EventRecord as GeneratedEventRecord,
	JobRecord,
	ModelProviderRecord,
	OverviewResponse,
	PipelineRecord,
	ProjectRecord,
	RuntimeProvidersResponse,
	SessionRecord,
	WorkflowRecord,
	WorkflowStepRecord,
} from './generated/openapi';

export type Dictionary = Record<string, unknown>;

export type Project = ProjectRecord;
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
export type Overview = OverviewResponse;
export type RuntimeProviders = RuntimeProvidersResponse;
