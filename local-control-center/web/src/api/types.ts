export type Dictionary = Record<string, unknown>;

export type Project = {
	id: string;
	name: string;
	path: string;
	status?: string;
};

export type Job = {
	id: string;
	projectId: string;
	kind: string;
	status: string;
	leaseOwner?: string | null;
	leaseExpiresAt?: string | null;
	payload?: Dictionary;
	createdAt?: string;
	updatedAt?: string;
};

export type ActionRequest = {
	id: string;
	jobId: string;
	projectId: string;
	actionType: string;
	status: string;
	riskLevel: string;
	command: string;
	reason: string;
	requestedAt?: string;
};

export type EventRecord = {
	id: string;
	type: string;
	projectId?: string | null;
	jobId?: string | null;
	payload?: Dictionary;
	severity?: string;
	createdAt?: string;
};

export type Workflow = {
	id: string;
	projectId: string;
	title: string;
	kind?: string;
	status: string;
};

export type WorkflowStep = {
	id: string;
	workflowId: string;
	workflowRunId?: string;
	name: string;
	status: string;
	agentProfileId?: string | null;
};

export type AgentProfile = {
	id: string;
	name: string;
	role: string;
	runtimeMode?: string;
	runtimeType?: string;
	permissionProfile?: string;
	status?: string;
};

export type ModelProvider = {
	id: string;
	provider?: string;
	label: string;
	status: string;
	allowRemote?: boolean;
	metadata?: Dictionary;
};

export type RuntimeProviders = {
	runtimeModes: string[];
	ollama: { provider: string; available: boolean; models: string[]; reason?: string };
	cli: { provider: string; available: boolean; adapters: Record<string, boolean> };
	api: { provider: string; available: boolean; adapters: string[] };
};

export type Session = {
	id: string;
	projectId: string;
	name: string;
	status: string;
};

export type Chat = {
	id: string;
	projectId: string;
	sessionId?: string | null;
	title: string;
	prompt: string;
	status: string;
};

export type Pipeline = {
	id: string;
	projectId: string;
	sessionId?: string | null;
	chatId?: string | null;
	title: string;
	status: string;
	stages: Array<{ name: string; status: string }>;
};

export type Overview = {
	projects: Project[];
	jobs: Job[];
	jobRuns: Dictionary[];
	actionRequests: ActionRequest[];
	events: EventRecord[];
	auditEvents: Dictionary[];
	mcpServers: Dictionary[];
	memoryItems: Dictionary[];
	retrievalStatus?: Dictionary;
	workflows: Workflow[];
	workflowRuns: Dictionary[];
	workflowSteps: WorkflowStep[];
	permissionDecisions: Dictionary[];
	permissionGrants: Dictionary[];
	sandboxProfiles: Dictionary[];
	evidencePackages: Dictionary[];
	artifacts: Dictionary[];
	testResultRecords: Dictionary[];
	agentProfiles: AgentProfile[];
	agentRuns: Dictionary[];
	modelPolicies: Dictionary[];
	modelProviders: ModelProvider[];
	agentToolCalls: Dictionary[];
	modelCalls: Dictionary[];
	costUsage: Dictionary[];
	runtimeWorkspaces: Dictionary[];
	sessions: Session[];
	chats: Chat[];
	pipelines: Pipeline[];
	architectureDecisions: Dictionary[];
	riskRegister: Dictionary[];
	nextSteps: Dictionary[];
	security: Dictionary;
};
