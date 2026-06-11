import type { Overview, RetrievalStatus, RuntimeProviders } from './types';
import { requestGeneratedOperation } from './generated/openapi';
import type { ApiOperationId, OperationRequestBody, OperationResponse } from './generated/openapi';

const WRITE_HEADER = 'X-Local-Control-Token';

type MutationBody<TOperationId extends ApiOperationId> = OperationRequestBody<TOperationId>;

export type ArtifactPayload = {
	artifactId: string;
	hash: string;
	contentType: string;
	filename: string;
	contentLength: number;
	blob: Blob;
	text: string;
};

export type ModelGatewayRoutePreviewRequest = MutationBody<'route_preview_api_v1_model_gateway_route_preview_post'>;
export type ModelGatewayRoutePreviewResponse = OperationResponse<'route_preview_api_v1_model_gateway_route_preview_post'>;
export type ModelGatewayBenchmarkOutcomeRequest = MutationBody<'create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post'>;
export type ModelGatewayProviderPatchRequest = MutationBody<'patch_provider_api_v1_model_gateway_providers__provider_id__patch'>;
export type IssueToPatchRequest = MutationBody<'run_issue_to_patch_api_v1_workflows_issue_to_patch_post'>;
export type IssueToPatchResponse = OperationResponse<'run_issue_to_patch_api_v1_workflows_issue_to_patch_post'>;
export type IssueToPatchApprovalResponse = OperationResponse<'approve_issue_to_patch_api_v1_workflows_issue_to_patch__run_id__approve_post'>;
export type PromotePatchToBranchRequest = MutationBody<'promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post'>;
export type PromotePatchToBranchResponse = OperationResponse<'promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post'>;
export type EvidenceDetailResponse = OperationResponse<'get_evidence_api_v1_evidence__evidence_id__get'>;
export type DeveloperAgentRunRequest = MutationBody<'run_developer_agent_api_v1_agents_developer_runs_post'>;
export type DeveloperAgentRunResponse = OperationResponse<'run_developer_agent_api_v1_agents_developer_runs_post'>;
export type DeveloperAgentStatusResponse = OperationResponse<'developer_agent_status_api_v1_agents_developer_status_get'>;
export type DevOpsAgentRunRequest = MutationBody<'run_devops_agent_api_v1_agents_devops_runs_post'>;
export type DevOpsAgentRunResponse = OperationResponse<'run_devops_agent_api_v1_agents_devops_runs_post'>;
export type DevOpsAgentStatusResponse = OperationResponse<'devops_agent_status_api_v1_agents_devops_status_get'>;
export type QAAgentRunRequest = MutationBody<'run_qa_agent_api_v1_agents_qa_runs_post'>;
export type QAAgentRunResponse = OperationResponse<'run_qa_agent_api_v1_agents_qa_runs_post'>;
export type SecurityAgentRunRequest = MutationBody<'run_security_agent_api_v1_agents_security_runs_post'>;
export type SecurityAgentRunResponse = OperationResponse<'run_security_agent_api_v1_agents_security_runs_post'>;
export type SecurityAgentStatusResponse = OperationResponse<'security_agent_status_api_v1_agents_security_status_get'>;
export type ArchitectAgentRunRequest = MutationBody<'run_architect_agent_api_v1_agents_architect_runs_post'>;
export type ArchitectAgentRunResponse = OperationResponse<'run_architect_agent_api_v1_agents_architect_runs_post'>;
export type ArchitectAgentStatusResponse = OperationResponse<'architect_agent_status_api_v1_agents_architect_status_get'>;
export type I18nLanguageRecord = {
	code: string;
	name: string;
	nativeName: string;
	enabled: boolean;
};
export type I18nCatalogResponse = {
	defaultLanguage: string;
	languages: I18nLanguageRecord[];
	translations: Record<string, Record<string, string>>;
};

async function parseResponse<T>(response: Response): Promise<T> {
	const text = await response.text();
	const payload = text ? JSON.parse(text) : {};
	if (!response.ok) {
		const detail = payload.detail ?? payload.error ?? response.statusText;
		throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
	}
	return payload as T;
}

export async function apiRequest<T>(
	path: string,
	options: { method?: string; token?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
	const headers: Record<string, string> = { Accept: 'application/json' };
	if (options.body !== undefined) headers['Content-Type'] = 'application/json';
	if (options.token) headers[WRITE_HEADER] = options.token;
	const response = await fetch(path, {
		method: options.method ?? 'GET',
		headers,
		body: options.body === undefined ? undefined : JSON.stringify(options.body),
		signal: options.signal,
	});
	return parseResponse<T>(response);
}

export function getHandshake(signal?: AbortSignal) {
	return requestGeneratedOperation<'handshake_api_v1_security_handshake_get', { token: string }>('handshake_api_v1_security_handshake_get', { signal });
}

export function getOverview(signal?: AbortSignal) {
	return requestGeneratedOperation<'overview_api_v1_overview_get', Overview>('overview_api_v1_overview_get', { signal });
}

export function getRetrievalStatus(signal?: AbortSignal) {
	return requestGeneratedOperation<'retrieval_status_api_v1_retrieval_status_get', RetrievalStatus>('retrieval_status_api_v1_retrieval_status_get', { signal });
}

export function getRuntimeProviders(signal?: AbortSignal) {
	return requestGeneratedOperation<'list_runtime_providers_api_v1_runtime_providers_get', RuntimeProviders>('list_runtime_providers_api_v1_runtime_providers_get', { signal });
}

export function getEvidenceDetail(evidenceId: string, signal?: AbortSignal) {
	return requestGeneratedOperation<'get_evidence_api_v1_evidence__evidence_id__get', EvidenceDetailResponse>(
		'get_evidence_api_v1_evidence__evidence_id__get',
		{ pathParams: { evidence_id: evidenceId }, signal },
	);
}

export function getRuntimeProviderConfiguration(signal?: AbortSignal) {
	return requestGeneratedOperation('list_runtime_provider_configuration_api_v1_runtime_provider_configuration_get', { signal });
}

export function getDeveloperAgentStatus(signal?: AbortSignal) {
	return requestGeneratedOperation<'developer_agent_status_api_v1_agents_developer_status_get', DeveloperAgentStatusResponse>(
		'developer_agent_status_api_v1_agents_developer_status_get',
		{ signal },
	);
}

export function getDevOpsAgentStatus(signal?: AbortSignal) {
	return requestGeneratedOperation<'devops_agent_status_api_v1_agents_devops_status_get', DevOpsAgentStatusResponse>(
		'devops_agent_status_api_v1_agents_devops_status_get',
		{ signal },
	);
}

export function getArchitectAgentStatus(signal?: AbortSignal) {
	return requestGeneratedOperation<'architect_agent_status_api_v1_agents_architect_status_get', ArchitectAgentStatusResponse>(
		'architect_agent_status_api_v1_agents_architect_status_get',
		{ signal },
	);
}

export function getSecurityAgentStatus(signal?: AbortSignal) {
	return requestGeneratedOperation<'security_agent_status_api_v1_agents_security_status_get', SecurityAgentStatusResponse>(
		'security_agent_status_api_v1_agents_security_status_get',
		{ signal },
	);
}

export function runQAAgent(token: string, body: QAAgentRunRequest) {
	return requestGeneratedOperation<'run_qa_agent_api_v1_agents_qa_runs_post', QAAgentRunResponse>(
		'run_qa_agent_api_v1_agents_qa_runs_post',
		{ token, body },
	);
}

export function getI18nCatalog(signal?: AbortSignal) {
	return apiRequest<I18nCatalogResponse>('/api/v1/i18n/catalog', { signal });
}

export function updateI18nCatalog(token: string, body: I18nCatalogResponse) {
	return apiRequest<I18nCatalogResponse>('/api/v1/i18n/catalog', {
		method: 'PUT',
		token,
		body,
	});
}

export function listProjects(signal?: AbortSignal) {
	return requestGeneratedOperation('projects_api_v1_projects_get', { signal });
}

export function listProjectTemplates(signal?: AbortSignal) {
	return requestGeneratedOperation('project_templates_api_v1_project_templates_get', { signal });
}

export function createProject(token: string, body: MutationBody<'create_project_api_v1_projects_post'>) {
	return requestGeneratedOperation('create_project_api_v1_projects_post', {
		token,
		body,
	});
}

export function discoverProject(token: string, body: MutationBody<'discover_project_api_v1_projects_discover_post'>) {
	return requestGeneratedOperation('discover_project_api_v1_projects_discover_post', {
		token,
		body,
	});
}

export function selectLocalDirectory(token: string, body: MutationBody<'select_directory_api_v1_local_paths_select_directory_post'>) {
	return requestGeneratedOperation('select_directory_api_v1_local_paths_select_directory_post', {
		token,
		body,
	});
}

export function listProviders(signal?: AbortSignal) {
	return requestGeneratedOperation('providers_api_v1_providers_get', { signal });
}

export function listTeams(projectId?: string, signal?: AbortSignal) {
	return requestGeneratedOperation('teams_api_v1_teams_get', {
		query: projectId ? { projectId } : undefined,
		signal,
	});
}

export function listAgents(teamId?: string, signal?: AbortSignal) {
	return requestGeneratedOperation('agents_api_v1_agents_get', {
		query: teamId ? { teamId } : undefined,
		signal,
	});
}

function filenameFromContentDisposition(header: string | null): string {
	if (!header) return '';
	const utf8Match = header.match(/filename\*=UTF-8''([^;]+)/i);
	if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1].replace(/^"|"$/g, ''));
	const plainMatch = header.match(/filename="?([^";]+)"?/i);
	return plainMatch?.[1] ? plainMatch[1] : '';
}

function downloadBlob(blob: Blob, filename: string) {
	const url = URL.createObjectURL(blob);
	const link = document.createElement('a');
	link.href = url;
	link.download = filename || 'artifact';
	document.body.appendChild(link);
	link.click();
	link.remove();
	URL.revokeObjectURL(url);
}

export async function fetchEvidenceArtifact(token: string, evidenceId: string, artifactId: string): Promise<ArtifactPayload> {
	const response = await fetch(`/api/v1/evidence/${encodeURIComponent(evidenceId)}/artifacts/${encodeURIComponent(artifactId)}`, {
		headers: {
			Accept: '*/*',
			[WRITE_HEADER]: token,
		},
	});
	const contentType = response.headers.get('content-type') ?? 'application/octet-stream';
	if (!response.ok) {
		const detail = await response.text();
		throw new Error(detail || response.statusText);
	}
	const blob = await response.blob();
	const textLike =
		contentType.startsWith('text/') ||
		contentType.includes('json') ||
		contentType.includes('xml') ||
		contentType.includes('markdown');
	const contentLength = Number(response.headers.get('content-length') ?? blob.size);
	return {
		artifactId: response.headers.get('X-AIDO-Artifact-Id') ?? artifactId,
		hash: response.headers.get('X-AIDO-Artifact-Hash') ?? '',
		contentType,
		filename: filenameFromContentDisposition(response.headers.get('content-disposition')),
		contentLength: Number.isFinite(contentLength) ? contentLength : blob.size,
		blob,
		text: textLike ? await blob.text() : '',
	};
}

export async function downloadEvidenceArtifact(
	token: string,
	evidenceId: string,
	artifactId: string,
	fallbackFilename = 'artifact',
): Promise<ArtifactPayload> {
	const payload = await fetchEvidenceArtifact(token, evidenceId, artifactId);
	downloadBlob(payload.blob, payload.filename || fallbackFilename);
	return payload;
}

export function approveAction(token: string, jobId: string, actionId: string, reason: string) {
	return requestGeneratedOperation('approve_action_api_v1_jobs__job_id__actions__action_id__approve_post', {
		token,
		pathParams: { job_id: jobId, action_id: actionId },
		body: { reason },
	});
}

export function denyAction(token: string, jobId: string, actionId: string, reason: string) {
	return requestGeneratedOperation('deny_action_api_v1_jobs__job_id__actions__action_id__deny_post', {
		token,
		pathParams: { job_id: jobId, action_id: actionId },
		body: { reason },
	});
}

export function cancelJob(token: string, jobId: string, reason: string) {
	return requestGeneratedOperation('cancel_job_api_v1_jobs__job_id__cancel_post', {
		token,
		pathParams: { job_id: jobId },
		body: { reason },
	});
}

export function retryJob(token: string, jobId: string, reason: string) {
	return requestGeneratedOperation('retry_job_api_v1_jobs__job_id__retry_post', {
		token,
		pathParams: { job_id: jobId },
		body: { reason },
	});
}

export function createWorkflow(token: string, projectId: string, title: string) {
	return requestGeneratedOperation('create_workflow_api_v1_workflows_post', {
		token,
		body: { projectId, title, kind: 'idea_to_pr' },
	});
}

export function createWorkflowWithBody(token: string, body: MutationBody<'create_workflow_api_v1_workflows_post'>) {
	return requestGeneratedOperation('create_workflow_api_v1_workflows_post', {
		token,
		body,
	});
}

export function runIssueToPatch(token: string, body: IssueToPatchRequest) {
	return requestGeneratedOperation('run_issue_to_patch_api_v1_workflows_issue_to_patch_post', {
		token,
		body,
	});
}

export function approveIssueToPatch(token: string, runId: string, reason: string) {
	return requestGeneratedOperation<'approve_issue_to_patch_api_v1_workflows_issue_to_patch__run_id__approve_post', IssueToPatchApprovalResponse>(
		'approve_issue_to_patch_api_v1_workflows_issue_to_patch__run_id__approve_post',
		{
			token,
			pathParams: { run_id: runId },
			body: { reason },
		},
	);
}

export function promotePatchToBranch(token: string, runId: string, body: PromotePatchToBranchRequest) {
	return requestGeneratedOperation<
		'promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post',
		PromotePatchToBranchResponse
	>('promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post', {
		token,
		pathParams: { run_id: runId },
		body,
	});
}

export function runDeveloperAgent(token: string, body: DeveloperAgentRunRequest) {
	return requestGeneratedOperation('run_developer_agent_api_v1_agents_developer_runs_post', {
		token,
		body,
	});
}

export function runDevOpsAgent(token: string, body: DevOpsAgentRunRequest) {
	return requestGeneratedOperation('run_devops_agent_api_v1_agents_devops_runs_post', {
		token,
		body,
	});
}

export function runArchitectAgent(token: string, body: ArchitectAgentRunRequest) {
	return requestGeneratedOperation('run_architect_agent_api_v1_agents_architect_runs_post', {
		token,
		body,
	});
}

export function runSecurityAgent(token: string, body: SecurityAgentRunRequest) {
	return requestGeneratedOperation('run_security_agent_api_v1_agents_security_runs_post', {
		token,
		body,
	});
}

export function createAgentProfile(token: string, body: MutationBody<'upsert_agent_profile_api_v1_agent_profiles_post'>) {
	return requestGeneratedOperation('upsert_agent_profile_api_v1_agent_profiles_post', {
		token,
		body,
	});
}

export function createModelGatewayRolePolicy(token: string, body: MutationBody<'create_role_policy_api_v1_model_gateway_role_policies_post'>) {
	return requestGeneratedOperation('create_role_policy_api_v1_model_gateway_role_policies_post', {
		token,
		body,
	});
}

export function createRisk(token: string, body: MutationBody<'create_risk_api_v1_risks_post'>) {
	return requestGeneratedOperation('create_risk_api_v1_risks_post', {
		token,
		body,
	});
}

export function updateRisk(token: string, riskId: string, body: MutationBody<'update_risk_api_v1_risks__risk_id__patch'>) {
	return requestGeneratedOperation('update_risk_api_v1_risks__risk_id__patch', {
		token,
		pathParams: { risk_id: riskId },
		body,
	});
}

export function createArchitectureDecision(token: string, body: MutationBody<'create_architecture_decision_api_v1_architecture_decisions_post'>) {
	return requestGeneratedOperation('create_architecture_decision_api_v1_architecture_decisions_post', {
		token,
		body,
	});
}

export function createNextStep(token: string, body: MutationBody<'create_next_step_api_v1_next_steps_post'>) {
	return requestGeneratedOperation('create_next_step_api_v1_next_steps_post', {
		token,
		body,
	});
}

export function updateSandboxProfile(token: string, profileId: string, body: MutationBody<'update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch'>) {
	return requestGeneratedOperation('update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch', {
		token,
		pathParams: { profile_id: profileId },
		body,
	});
}

export function registerMcpServer(token: string, body: MutationBody<'register_mcp_server_api_v1_integrations_mcp_register_post'>) {
	return requestGeneratedOperation('register_mcp_server_api_v1_integrations_mcp_register_post', {
		token,
		body,
	});
}

export function getModelGatewayOverview(signal?: AbortSignal) {
	return requestGeneratedOperation('overview_api_v1_model_gateway_overview_get', { signal });
}

export function getModelGatewayProviders(signal?: AbortSignal) {
	return requestGeneratedOperation('list_providers_api_v1_model_gateway_providers_get', { signal });
}

export function getModelGatewayModels(signal?: AbortSignal) {
	return requestGeneratedOperation('list_models_api_v1_model_gateway_models_get', { signal });
}

export function getModelGatewayRoutingProfiles(signal?: AbortSignal) {
	return requestGeneratedOperation('list_routing_profiles_api_v1_model_gateway_routing_profiles_get', { signal });
}

export function getModelGatewayRolePolicies(signal?: AbortSignal) {
	return requestGeneratedOperation('list_role_policies_api_v1_model_gateway_role_policies_get', { signal });
}

export function getModelGatewayUsageLedger(signal?: AbortSignal) {
	return requestGeneratedOperation('list_usage_ledger_api_v1_model_gateway_usage_ledger_get', { signal });
}

export function getModelGatewayUsageSummary(signal?: AbortSignal) {
	return requestGeneratedOperation('usage_summary_api_v1_model_gateway_usage_ledger_summary_get', { signal });
}

export function getModelGatewayRoutingDecisions(signal?: AbortSignal) {
	return requestGeneratedOperation('list_routing_decisions_api_v1_model_gateway_routing_decisions_get', { signal });
}

export function getModelGatewayProviderLimits(signal?: AbortSignal) {
	return requestGeneratedOperation('list_provider_limits_api_v1_model_gateway_provider_limits_get', { signal });
}

export function getModelGatewayBudgetRules(signal?: AbortSignal) {
	return requestGeneratedOperation('list_budget_rules_api_v1_model_gateway_budget_rules_get', { signal });
}

export function getModelGatewayCliRuntimes(signal?: AbortSignal) {
	return requestGeneratedOperation('list_cli_runtimes_api_v1_model_gateway_cli_runtimes_get', { signal });
}

export function detectModelGatewayCliRuntime(token: string, runtimeId: string) {
	return requestGeneratedOperation('detect_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__detect_post', {
		token,
		pathParams: { runtime_id: runtimeId },
	});
}

export function getModelGatewayCliSessions(signal?: AbortSignal) {
	return requestGeneratedOperation('list_cli_sessions_api_v1_model_gateway_cli_sessions_get', { signal });
}

export function getModelGatewayBenchmarks(signal?: AbortSignal) {
	return requestGeneratedOperation('list_benchmarks_api_v1_model_gateway_benchmarks_get', { signal });
}

export function getModelGatewayBenchmarkOutcomes(signal?: AbortSignal) {
	return requestGeneratedOperation('list_benchmark_outcomes_api_v1_model_gateway_benchmark_outcomes_get', { signal });
}

export function recordModelGatewayBenchmarkOutcome(token: string, body: ModelGatewayBenchmarkOutcomeRequest) {
	return requestGeneratedOperation<'create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post'>('create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post', {
		token,
		body,
	});
}

export function previewModelRoute(token: string, body: ModelGatewayRoutePreviewRequest) {
	return requestGeneratedOperation<'route_preview_api_v1_model_gateway_route_preview_post'>('route_preview_api_v1_model_gateway_route_preview_post', {
		token,
		body,
	});
}

export function patchModelGatewayProvider(token: string, providerId: string, body: ModelGatewayProviderPatchRequest) {
	return requestGeneratedOperation('patch_provider_api_v1_model_gateway_providers__provider_id__patch', {
		token,
		pathParams: { provider_id: providerId },
		body,
	});
}

export function healthCheckModelGatewayProvider(token: string, providerId: string) {
	return requestGeneratedOperation('provider_health_check_api_v1_model_gateway_providers__provider_id__health_check_post', {
		token,
		pathParams: { provider_id: providerId },
	});
}

export function discoverModelGatewayProviderModels(token: string, providerId: string) {
	return requestGeneratedOperation('discover_models_api_v1_model_gateway_providers__provider_id__discover_models_post', {
		token,
		pathParams: { provider_id: providerId },
	});
}
