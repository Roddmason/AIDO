/**
 * Hand-rolled HTTP client over the generated OpenAPI layer for the control plane.
 *
 * Each export is a typed call for one backend operation; mutations attach the
 * write token via `X-Local-Control-Token`. Request/response aliases here keep call
 * sites readable while staying anchored to the generated operation contracts, so a
 * server schema change surfaces as a type error rather than a silent drift.
 * @author Rodrigo Mason
 */

import type { ApiOperationId, OperationRequestBody, OperationResponse } from './generated/openapi';
import { extractErrorDetail, requestGeneratedOperation } from './generated/openapi';
import type { Overview, RetrievalStatus, RuntimeProviders } from './types';

const WRITE_HEADER = 'X-Local-Control-Token';

/** Request-body type of a generated operation, used to derive the `*Request` aliases below. */
type MutationBody<TOperationId extends ApiOperationId> = OperationRequestBody<TOperationId>;

/** Downloaded evidence artifact: the raw blob plus metadata and an optional inlined text preview. */
export type ArtifactPayload = {
	artifactId: string;
	hash: string;
	contentType: string;
	filename: string;
	contentLength: number;
	blob: Blob;
	text: string;
};

export type ModelGatewayRoutePreviewRequest =
	MutationBody<'route_preview_api_v1_model_gateway_route_preview_post'>;
export type ModelGatewayRoutePreviewResponse =
	OperationResponse<'route_preview_api_v1_model_gateway_route_preview_post'>;
export type ModelGatewayBenchmarkOutcomeRequest =
	MutationBody<'create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post'>;
export type ModelGatewayProviderPatchRequest =
	MutationBody<'patch_provider_api_v1_model_gateway_providers__provider_id__patch'>;
export type ChatCreateRequest = MutationBody<'create_chat_api_v1_chats_post'>;
export type ChatCreateResponse = OperationResponse<'create_chat_api_v1_chats_post'>;
export type SessionCreateRequest = MutationBody<'create_session_api_v1_sessions_post'>;
export type SessionCreateResponse = OperationResponse<'create_session_api_v1_sessions_post'>;
export type PipelineCreateRequest = MutationBody<'create_pipeline_api_v1_pipelines_post'>;
export type PipelineCreateResponse = OperationResponse<'create_pipeline_api_v1_pipelines_post'>;
export type ThreadCreateRequest = MutationBody<'create_thread_api_v1_threads_post'>;
export type ThreadDetailResponse = OperationResponse<'get_thread_api_v1_threads__thread_id__get'>;
export type ThreadMessageRequest =
	MutationBody<'post_message_api_v1_threads__thread_id__messages_post'>;
export type ThreadMessageResultResponse =
	OperationResponse<'post_message_api_v1_threads__thread_id__messages_post'>;
export type ThreadDecisionResolveRequest =
	MutationBody<'resolve_decision_api_v1_threads__thread_id__decisions__decision_id__resolve_post'>;
export type ThreadDecisionResolveResponse =
	OperationResponse<'resolve_decision_api_v1_threads__thread_id__decisions__decision_id__resolve_post'>;
export type IssueToPatchRequest =
	MutationBody<'run_issue_to_patch_api_v1_workflows_issue_to_patch_post'>;
export type IssueToPatchResponse =
	OperationResponse<'run_issue_to_patch_api_v1_workflows_issue_to_patch_post'>;
export type IssueToPatchApprovalResponse =
	OperationResponse<'approve_issue_to_patch_api_v1_workflows_issue_to_patch__run_id__approve_post'>;
export type IssueToPrRequest = MutationBody<'run_issue_to_pr_api_v1_workflows_issue_to_pr_post'>;
export type IssueToPrResponse =
	OperationResponse<'run_issue_to_pr_api_v1_workflows_issue_to_pr_post'>;
export type IssueToPrApprovalResponse =
	OperationResponse<'approve_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__approve_post'>;
export type PromotePatchToBranchRequest =
	MutationBody<'promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post'>;
export type PromotePatchToBranchResponse =
	OperationResponse<'promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post'>;
export type PromoteIssueToPrBranchRequest =
	MutationBody<'promote_issue_to_pr_branch_api_v1_workflows_issue_to_pr__run_id__promote_post'>;
export type PromoteIssueToPrBranchResponse =
	OperationResponse<'promote_issue_to_pr_branch_api_v1_workflows_issue_to_pr__run_id__promote_post'>;
export type PullRequestCreateRequest =
	MutationBody<'create_pull_request_from_promoted_branch_api_v1_workflows_issue_to_patch__run_id__pull_request_post'>;
export type PullRequestCreateResponse =
	OperationResponse<'create_pull_request_from_promoted_branch_api_v1_workflows_issue_to_patch__run_id__pull_request_post'>;
export type IssueToPrPullRequestCreateRequest =
	MutationBody<'create_pull_request_from_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__pull_request_post'>;
export type IssueToPrPullRequestCreateResponse =
	OperationResponse<'create_pull_request_from_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__pull_request_post'>;
export type EvidenceDetailResponse =
	OperationResponse<'get_evidence_api_v1_evidence__evidence_id__get'>;
export type ProjectProductLoopResponse =
	OperationResponse<'get_product_loop_state_api_v1_projects__project_id__product_loop_get'>;
export type ProductLoopStartRequest =
	MutationBody<'start_product_loop_api_v1_projects__project_id__product_loop_post'>;
export type ProductLoopTransitionRequest =
	MutationBody<'transition_product_loop_api_v1_projects__project_id__product_loop__loop_id__transition_post'>;
export type ProductLoopAidoDecideRequest =
	MutationBody<'aido_decide_product_loop_api_v1_projects__project_id__product_loop__loop_id__aido_decide_post'>;
export type ProductLoopApprovalRequest =
	MutationBody<'approve_product_brief_api_v1_projects__project_id__product_loop_brief__brief_id__approve_post'>;
export type ProductLoopResumeResponse =
	OperationResponse<'start_product_loop_api_v1_projects__project_id__product_loop_post'>;
export type ProductOwnerAgentRunRequest =
	MutationBody<'run_product_owner_agent_api_v1_agents_product_owner_runs_post'>;
export type ProductOwnerAgentRunResponse =
	OperationResponse<'run_product_owner_agent_api_v1_agents_product_owner_runs_post'>;
export type DeveloperAgentRunRequest =
	MutationBody<'run_developer_agent_api_v1_agents_developer_runs_post'>;
export type DeveloperAgentRunResponse =
	OperationResponse<'run_developer_agent_api_v1_agents_developer_runs_post'>;
export type DeveloperAgentStatusResponse =
	OperationResponse<'developer_agent_status_api_v1_agents_developer_status_get'>;
export type DevOpsAgentRunRequest = MutationBody<'run_devops_agent_api_v1_agents_devops_runs_post'>;
export type DevOpsAgentRunResponse =
	OperationResponse<'run_devops_agent_api_v1_agents_devops_runs_post'>;
export type DevOpsAgentStatusResponse =
	OperationResponse<'devops_agent_status_api_v1_agents_devops_status_get'>;
export type QAAgentRunRequest = MutationBody<'run_qa_agent_api_v1_agents_qa_runs_post'>;
export type QAAgentRunResponse = OperationResponse<'run_qa_agent_api_v1_agents_qa_runs_post'>;
export type SecurityAgentRunRequest =
	MutationBody<'run_security_agent_api_v1_agents_security_runs_post'>;
export type SecurityAgentRunResponse =
	OperationResponse<'run_security_agent_api_v1_agents_security_runs_post'>;
export type SecurityAgentStatusResponse =
	OperationResponse<'security_agent_status_api_v1_agents_security_status_get'>;
export type ResearchAgentRunRequest =
	MutationBody<'run_research_agent_api_v1_agents_research_runs_post'>;
export type ResearchAgentRunResponse =
	OperationResponse<'run_research_agent_api_v1_agents_research_runs_post'>;
export type ResearchAgentStatusResponse =
	OperationResponse<'research_agent_status_api_v1_agents_research_status_get'>;
export type ArchitectAgentRunRequest =
	MutationBody<'run_architect_agent_api_v1_agents_architect_runs_post'>;
export type ArchitectAgentRunResponse =
	OperationResponse<'run_architect_agent_api_v1_agents_architect_runs_post'>;
export type ArchitectAgentStatusResponse =
	OperationResponse<'architect_agent_status_api_v1_agents_architect_status_get'>;
export type AgentProfilesResponse =
	OperationResponse<'list_agent_profiles_api_v1_agent_profiles_get'>;
export type AgentProfileProjectOverrideRequest =
	MutationBody<'agent_profile_override_api_v1_projects__project_id__agent_profile_overrides__profile_id__put'>;
export type AgentProfileProjectOverrideResponse =
	OperationResponse<'agent_profile_override_api_v1_projects__project_id__agent_profile_overrides__profile_id__put'>;
export type CredentialsResponse = OperationResponse<'list_credentials_api_v1_credentials_get'>;
export type CredentialCreateRequest = MutationBody<'create_credential_api_v1_credentials_post'>;
export type CredentialCreateResponse =
	OperationResponse<'create_credential_api_v1_credentials_post'>;
export type CredentialRotateRequest =
	MutationBody<'rotate_credential_api_v1_credentials__credential_id__rotate_post'>;
export type CredentialRotateResponse =
	OperationResponse<'rotate_credential_api_v1_credentials__credential_id__rotate_post'>;
export type CredentialValidateResponse =
	OperationResponse<'validate_credential_api_v1_credentials__credential_id__validate_post'>;
export type CredentialDeleteResponse =
	OperationResponse<'delete_credential_api_v1_credentials__credential_id__delete'>;
export type CredentialAuditResponse =
	OperationResponse<'list_credential_audit_api_v1_credentials_audit_get'>;
export type CredentialMigrateRequest =
	MutationBody<'migrate_credentials_api_v1_credentials_migrate_post'>;
export type CredentialMigrateResponse =
	OperationResponse<'migrate_credentials_api_v1_credentials_migrate_post'>;
export type I18nLanguageRecord = {
	code: string;
	name: string;
	nativeName: string;
	enabled: boolean;
};
/** Runtime translation catalog: available languages plus per-key, per-language copy. */
export type I18nCatalogResponse = {
	defaultLanguage: string;
	languages: I18nLanguageRecord[];
	translations: Record<string, Record<string, string>>;
};

/**
 * Parses a JSON response, raising the server-provided `detail`/`error` (or the
 * status text) as an Error on a non-2xx status so callers handle one failure shape.
 * Checks the status before parsing so a non-JSON error body (e.g. a plain-text
 * "Internal Server Error" from an unhandled 500) surfaces its real text instead of
 * an opaque `JSON.parse` SyntaxError.
 */
async function parseResponse<T>(response: Response): Promise<T> {
	const text = await response.text();
	if (!response.ok) {
		throw new Error(extractErrorDetail(text, response.statusText));
	}
	try {
		return (text ? JSON.parse(text) : {}) as T;
	} catch {
		throw new Error(`Malformed JSON response from ${response.url || 'the control plane'}.`);
	}
}

/**
 * Low-level JSON fetch for routes the generated client does not cover (e.g. the
 * planned project-files endpoint). Sets JSON headers, attaches the write token when
 * given, and normalizes errors through `parseResponse`.
 */
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
	return requestGeneratedOperation<'handshake_api_v1_security_handshake_get', { token: string }>(
		'handshake_api_v1_security_handshake_get',
		{ signal },
	);
}

export function getOverview(signal?: AbortSignal) {
	return requestGeneratedOperation<'overview_api_v1_overview_get', Overview>(
		'overview_api_v1_overview_get',
		{ signal },
	);
}

export function getRetrievalStatus(signal?: AbortSignal) {
	return requestGeneratedOperation<'retrieval_status_api_v1_retrieval_status_get', RetrievalStatus>(
		'retrieval_status_api_v1_retrieval_status_get',
		{ signal },
	);
}

export function getRuntimeProviders(signal?: AbortSignal) {
	return requestGeneratedOperation<
		'list_runtime_providers_api_v1_runtime_providers_get',
		RuntimeProviders
	>('list_runtime_providers_api_v1_runtime_providers_get', { signal });
}

export function getEvidenceDetail(evidenceId: string, signal?: AbortSignal) {
	return requestGeneratedOperation<
		'get_evidence_api_v1_evidence__evidence_id__get',
		EvidenceDetailResponse
	>('get_evidence_api_v1_evidence__evidence_id__get', {
		pathParams: { evidence_id: evidenceId },
		signal,
	});
}

/** Project-scoped product-loop aggregate (loops, questions, brief, assumptions, decisions, backlog,
 *  iterations) that backs the Workbench loop sections; a read, so no write token. */
export function getProjectProductLoop(projectId: string, signal?: AbortSignal) {
	return requestGeneratedOperation<
		'get_product_loop_state_api_v1_projects__project_id__product_loop_get',
		ProjectProductLoopResponse
	>('get_product_loop_state_api_v1_projects__project_id__product_loop_get', {
		pathParams: { project_id: projectId },
		signal,
	});
}

export type SettingsResponse = OperationResponse<'get_settings_api_v1_settings_get'>;
export type ResolvedSetting = SettingsResponse['general'][number];
export type SetSettingBody = OperationRequestBody<'put_setting_api_v1_settings__key__put'>;

/** Resolved settings for both general and project scopes; a read, so no token required. */
export function getSettings(projectId: string, signal?: AbortSignal) {
	return requestGeneratedOperation<'get_settings_api_v1_settings_get', SettingsResponse>(
		'get_settings_api_v1_settings_get',
		{ query: { projectId }, signal },
	);
}

/** Persists a value for a registered setting key at the given scope; requires the write token. */
export function putSetting(key: string, body: SetSettingBody, token: string) {
	return requestGeneratedOperation('put_setting_api_v1_settings__key__put', {
		token,
		pathParams: { key },
		body,
	});
}

/** Clears a setting override so resolution falls back to the inherited value; requires token. */
export function deleteSetting(
	key: string,
	params: { scope: string; scopeId?: string },
	token: string,
) {
	return requestGeneratedOperation('delete_setting_api_v1_settings__key__delete', {
		token,
		pathParams: { key },
		query: params,
	});
}

export type TeamActivityResponse =
	OperationResponse<'team_activity_api_v1_projects__project_id__team_activity_get'>;

/** Project-scoped Team Activity board: active agents with role, runtime, current assignment, blocked
 *  reason, completed artifact, reviewer, duration, cost and collapsed low-level events. A read, no token. */
export function getProjectTeamActivity(projectId: string, signal?: AbortSignal) {
	return requestGeneratedOperation<
		'team_activity_api_v1_projects__project_id__team_activity_get',
		TeamActivityResponse
	>('team_activity_api_v1_projects__project_id__team_activity_get', {
		pathParams: { project_id: projectId },
		signal,
	});
}

/** Starts a durable product loop (idea_received) for a project; a write, so the token is required. */
export function startProductLoop(token: string, projectId: string, body: ProductLoopStartRequest) {
	return requestGeneratedOperation(
		'start_product_loop_api_v1_projects__project_id__product_loop_post',
		{
			token,
			pathParams: { project_id: projectId },
			body,
		},
	);
}

/** Advances a product loop to an FSM-allowed state; a write, so the token is required. */
export function transitionProductLoop(
	token: string,
	projectId: string,
	loopId: string,
	body: ProductLoopTransitionRequest,
) {
	return requestGeneratedOperation(
		'transition_product_loop_api_v1_projects__project_id__product_loop__loop_id__transition_post',
		{ token, pathParams: { project_id: projectId, loop_id: loopId }, body },
	);
}

/** Accepts AIDO-selected answers for open product-loop questions and records product decisions. */
export function aidoDecideProductLoop(
	token: string,
	projectId: string,
	loopId: string,
	body: ProductLoopAidoDecideRequest,
) {
	return requestGeneratedOperation(
		'aido_decide_product_loop_api_v1_projects__project_id__product_loop__loop_id__aido_decide_post',
		{ token, pathParams: { project_id: projectId, loop_id: loopId }, body },
	);
}

/** Approves a ProductOwnerAgent brief and materializes its validated backlog payload. */
export function approveProductBrief(
	token: string,
	projectId: string,
	briefId: string,
	body: ProductLoopApprovalRequest,
) {
	return requestGeneratedOperation(
		'approve_product_brief_api_v1_projects__project_id__product_loop_brief__brief_id__approve_post',
		{ token, pathParams: { project_id: projectId, brief_id: briefId }, body },
	);
}

/** Records backlog approval and advances the loop when the FSM allows iteration planning. */
export function approveProductLoopBacklog(
	token: string,
	projectId: string,
	loopId: string,
	body: ProductLoopApprovalRequest,
) {
	return requestGeneratedOperation(
		'approve_product_loop_backlog_api_v1_projects__project_id__product_loop__loop_id__backlog_approve_post',
		{ token, pathParams: { project_id: projectId, loop_id: loopId }, body },
	);
}

/** Runs the Product Owner agent over an idea: it produces and persists the brief, clarification
 *  questions, assumptions, decisions and backlog (epics/stories/criteria). A write, so the token is
 *  required; fail-closed — with no executable product-owner runtime it persists nothing and says so. */
export function runProductOwnerAgent(token: string, body: ProductOwnerAgentRunRequest) {
	return requestGeneratedOperation<
		'run_product_owner_agent_api_v1_agents_product_owner_runs_post',
		ProductOwnerAgentRunResponse
	>('run_product_owner_agent_api_v1_agents_product_owner_runs_post', { token, body });
}

export type CliSessionStartRequest = MutationBody<'start_session_api_v1_cli_sessions_post'>;
export type CliSessionStartResponse = OperationResponse<'start_session_api_v1_cli_sessions_post'>;
export type CliSessionEventsResponse =
	OperationResponse<'session_events_api_v1_cli_sessions__session_id__events_get'>;
export type CliSessionEvent = CliSessionEventsResponse['events'][number];

/** Starts a streaming CLI session (runs a command in a workspace, emitting live events); a write. */
export function startCliSession(token: string, body: CliSessionStartRequest) {
	return requestGeneratedOperation('start_session_api_v1_cli_sessions_post', { token, body });
}

/** Reads the bounded CLI-session event log incrementally (events with seq > afterSeq); a read. */
export function getCliSessionEvents(sessionId: string, afterSeq = 0, signal?: AbortSignal) {
	return requestGeneratedOperation<
		'session_events_api_v1_cli_sessions__session_id__events_get',
		CliSessionEventsResponse
	>('session_events_api_v1_cli_sessions__session_id__events_get', {
		pathParams: { session_id: sessionId },
		query: { afterSeq },
		signal,
	});
}

/** Requests cancellation of a running CLI session (kills its process); a write. */
export function cancelCliSession(token: string, sessionId: string) {
	return requestGeneratedOperation('cancel_session_api_v1_cli_sessions__session_id__cancel_post', {
		token,
		pathParams: { session_id: sessionId },
	});
}

export function getRuntimeProviderConfiguration(signal?: AbortSignal) {
	return requestGeneratedOperation<
		'list_runtime_provider_configuration_api_v1_runtime_provider_configuration_get',
		OperationResponse<'list_runtime_provider_configuration_api_v1_runtime_provider_configuration_get'>
	>('list_runtime_provider_configuration_api_v1_runtime_provider_configuration_get', { signal });
}

export function getCredentials(signal?: AbortSignal) {
	return requestGeneratedOperation<'list_credentials_api_v1_credentials_get', CredentialsResponse>(
		'list_credentials_api_v1_credentials_get',
		{ signal },
	);
}

export function createCredential(token: string, body: CredentialCreateRequest) {
	return requestGeneratedOperation<
		'create_credential_api_v1_credentials_post',
		CredentialCreateResponse
	>('create_credential_api_v1_credentials_post', { token, body });
}

export function validateCredential(token: string, credentialId: string) {
	return requestGeneratedOperation<
		'validate_credential_api_v1_credentials__credential_id__validate_post',
		CredentialValidateResponse
	>('validate_credential_api_v1_credentials__credential_id__validate_post', {
		token,
		pathParams: { credential_id: credentialId },
	});
}

export function rotateCredential(
	token: string,
	credentialId: string,
	body: CredentialRotateRequest,
) {
	return requestGeneratedOperation<
		'rotate_credential_api_v1_credentials__credential_id__rotate_post',
		CredentialRotateResponse
	>('rotate_credential_api_v1_credentials__credential_id__rotate_post', {
		token,
		pathParams: { credential_id: credentialId },
		body,
	});
}

export function deleteCredential(token: string, credentialId: string) {
	return requestGeneratedOperation<
		'delete_credential_api_v1_credentials__credential_id__delete',
		CredentialDeleteResponse
	>('delete_credential_api_v1_credentials__credential_id__delete', {
		token,
		pathParams: { credential_id: credentialId },
	});
}

export function migrateCredentials(token: string, body?: CredentialMigrateRequest) {
	return requestGeneratedOperation<
		'migrate_credentials_api_v1_credentials_migrate_post',
		CredentialMigrateResponse
	>('migrate_credentials_api_v1_credentials_migrate_post', { token, body });
}

export function getCredentialAudit(credentialId?: string, signal?: AbortSignal) {
	return requestGeneratedOperation<
		'list_credential_audit_api_v1_credentials_audit_get',
		CredentialAuditResponse
	>('list_credential_audit_api_v1_credentials_audit_get', {
		query: credentialId ? { credential_id: credentialId } : undefined,
		signal,
	});
}

export function getDeveloperAgentStatus(signal?: AbortSignal) {
	return requestGeneratedOperation<
		'developer_agent_status_api_v1_agents_developer_status_get',
		DeveloperAgentStatusResponse
	>('developer_agent_status_api_v1_agents_developer_status_get', { signal });
}

export function getDevOpsAgentStatus(signal?: AbortSignal) {
	return requestGeneratedOperation<
		'devops_agent_status_api_v1_agents_devops_status_get',
		DevOpsAgentStatusResponse
	>('devops_agent_status_api_v1_agents_devops_status_get', { signal });
}

export function getArchitectAgentStatus(signal?: AbortSignal) {
	return requestGeneratedOperation<
		'architect_agent_status_api_v1_agents_architect_status_get',
		ArchitectAgentStatusResponse
	>('architect_agent_status_api_v1_agents_architect_status_get', { signal });
}

export function getSecurityAgentStatus(signal?: AbortSignal) {
	return requestGeneratedOperation<
		'security_agent_status_api_v1_agents_security_status_get',
		SecurityAgentStatusResponse
	>('security_agent_status_api_v1_agents_security_status_get', { signal });
}

export function getResearchAgentStatus(signal?: AbortSignal) {
	return requestGeneratedOperation<
		'research_agent_status_api_v1_agents_research_status_get',
		ResearchAgentStatusResponse
	>('research_agent_status_api_v1_agents_research_status_get', { signal });
}

export function runQAAgent(token: string, body: QAAgentRunRequest) {
	return requestGeneratedOperation<'run_qa_agent_api_v1_agents_qa_runs_post', QAAgentRunResponse>(
		'run_qa_agent_api_v1_agents_qa_runs_post',
		{ token, body },
	);
}

export function getI18nCatalog(signal?: AbortSignal) {
	return requestGeneratedOperation<'get_i18n_catalog_api_v1_i18n_catalog_get', I18nCatalogResponse>(
		'get_i18n_catalog_api_v1_i18n_catalog_get',
		{ signal },
	);
}

export function updateI18nCatalog(token: string, body: I18nCatalogResponse) {
	return requestGeneratedOperation<'put_i18n_catalog_api_v1_i18n_catalog_put', I18nCatalogResponse>(
		'put_i18n_catalog_api_v1_i18n_catalog_put',
		{
			token,
			body,
		},
	);
}

/**
 * Project file index — prepared contract for a future backend endpoint.
 *
 * CONTRACT: the control plane does not yet expose a project file tree. These
 * types plus `getProjectFiles` define the seam so the ExplorerPanel can render a
 * real tree the moment the route ships. Until then the panel gates consumption
 * behind a capability flag and never calls this; flipping the flag is the only
 * change needed once `/api/v1/projects/{id}/files` exists server-side.
 */
export interface ProjectFileNode {
	path: string;
	name: string;
	kind: 'file' | 'directory';
	size?: number | null;
	children?: ProjectFileNode[];
}

export interface ProjectFilesResponse {
	projectId: string;
	root: string;
	truncated: boolean;
	nodes: ProjectFileNode[];
}

export type GitWorkspaceStatus = 'completed' | 'blocked' | 'configuration_required' | 'failed';

export interface ProjectGitRemote {
	name: string;
	url: string;
	direction: 'fetch' | 'push';
}

export interface ProjectGitStatusResponse {
	status: GitWorkspaceStatus;
	reason: string;
	projectId: string;
	workspaceId: string;
	root: string;
	currentBranch: string;
	dirty: boolean;
	porcelain: string[];
	changedFiles: string[];
	untrackedFiles: string[];
	stagedFiles: string[];
	remotes: ProjectGitRemote[];
	lastCommit: {
		hash: string;
		shortHash: string;
		author: string;
		authoredAt: string;
		subject: string;
	} | null;
	worktrees: Array<{
		path: string;
		head: string;
		branch: string;
		detached: boolean;
		bare: boolean;
	}>;
}

export interface ProjectGitBranchesResponse {
	status: GitWorkspaceStatus;
	reason: string;
	projectId: string;
	workspaceId: string;
	currentBranch: string;
	dirty: boolean;
	localBranches: string[];
	remoteBranches: string[];
	remotes: ProjectGitRemote[];
}

export interface ProjectGitBranchCreateRequest {
	name: string;
	base?: string | null;
}

export interface ProjectGitBranchMutationResponse {
	status: GitWorkspaceStatus;
	reason: string;
	projectId: string;
	workspaceId: string;
	branch: string;
	base?: string | null;
	currentBranch: string;
}

export interface ProjectGitCheckoutRequest {
	branch: string;
	allowDirty?: boolean;
}

export interface ProjectGitCheckoutResponse {
	status: GitWorkspaceStatus;
	reason: string;
	projectId: string;
	workspaceId: string;
	requestedBranch: string;
	currentBranch: string;
	dirty: boolean;
}

export interface ProjectGitGitleaksScanResponse {
	status: GitWorkspaceStatus;
	reason: string;
	projectId: string;
	workspaceId: string;
	deliveryBlocked: boolean;
	gitleaks: {
		status: GitWorkspaceStatus;
		reason: string;
		executable: boolean;
		configured: boolean;
		exitCode: number | null;
		findingCount: number;
		reportPath: string | null;
	};
}

export function getProjectFiles(
	projectId: string,
	signal?: AbortSignal,
): Promise<ProjectFilesResponse> {
	return apiRequest<ProjectFilesResponse>(
		`/api/v1/projects/${encodeURIComponent(projectId)}/files`,
		{ signal },
	);
}

export function getProjectGitStatus(projectId: string, signal?: AbortSignal) {
	return apiRequest<ProjectGitStatusResponse>(
		`/api/v1/projects/${encodeURIComponent(projectId)}/git/status`,
		{ signal },
	);
}

export function getProjectGitBranches(projectId: string, signal?: AbortSignal) {
	return apiRequest<ProjectGitBranchesResponse>(
		`/api/v1/projects/${encodeURIComponent(projectId)}/git/branches`,
		{ signal },
	);
}

export function createProjectGitBranch(
	token: string,
	projectId: string,
	body: ProjectGitBranchCreateRequest,
) {
	return apiRequest<ProjectGitBranchMutationResponse>(
		`/api/v1/projects/${encodeURIComponent(projectId)}/git/branches`,
		{ method: 'POST', token, body },
	);
}

export function checkoutProjectGitBranch(
	token: string,
	projectId: string,
	body: ProjectGitCheckoutRequest,
) {
	return apiRequest<ProjectGitCheckoutResponse>(
		`/api/v1/projects/${encodeURIComponent(projectId)}/git/checkout`,
		{ method: 'POST', token, body },
	);
}

export function scanProjectGitleaks(token: string, projectId: string) {
	return apiRequest<ProjectGitGitleaksScanResponse>(
		`/api/v1/projects/${encodeURIComponent(projectId)}/git/gitleaks/scan`,
		{ method: 'POST', token },
	);
}

export function listProjects(signal?: AbortSignal) {
	return requestGeneratedOperation('projects_api_v1_projects_get', { signal });
}

export function listProjectTemplates(signal?: AbortSignal) {
	return requestGeneratedOperation('project_templates_api_v1_project_templates_get', { signal });
}

export function createProject(
	token: string,
	body: MutationBody<'create_project_api_v1_projects_post'>,
) {
	return requestGeneratedOperation('create_project_api_v1_projects_post', {
		token,
		body,
	});
}

export function discoverProject(
	token: string,
	body: MutationBody<'discover_project_api_v1_projects_discover_post'>,
) {
	return requestGeneratedOperation('discover_project_api_v1_projects_discover_post', {
		token,
		body,
	});
}

export function selectLocalDirectory(
	token: string,
	body: MutationBody<'select_directory_api_v1_local_paths_select_directory_post'>,
) {
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

/** Extracts the download filename from a Content-Disposition header, preferring RFC 5987 UTF-8. */
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

/**
 * Fetches an evidence artifact as a Blob plus its metadata (hash, content type,
 * server filename). Inlines `text` only for text-like content types so callers can
 * preview without re-reading the blob; binary payloads leave `text` empty.
 */
export async function fetchEvidenceArtifact(
	token: string,
	evidenceId: string,
	artifactId: string,
): Promise<ArtifactPayload> {
	const response = await fetch(
		`/api/v1/evidence/${encodeURIComponent(evidenceId)}/artifacts/${encodeURIComponent(artifactId)}`,
		{
			headers: {
				Accept: '*/*',
				[WRITE_HEADER]: token,
			},
		},
	);
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

/** Fetches an evidence artifact and triggers a browser download, returning the same payload. */
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
	return requestGeneratedOperation(
		'approve_action_api_v1_jobs__job_id__actions__action_id__approve_post',
		{
			token,
			pathParams: { job_id: jobId, action_id: actionId },
			body: { reason },
		},
	);
}

export function denyAction(token: string, jobId: string, actionId: string, reason: string) {
	return requestGeneratedOperation(
		'deny_action_api_v1_jobs__job_id__actions__action_id__deny_post',
		{
			token,
			pathParams: { job_id: jobId, action_id: actionId },
			body: { reason },
		},
	);
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

export function createWorkflowWithBody(
	token: string,
	body: MutationBody<'create_workflow_api_v1_workflows_post'>,
) {
	return requestGeneratedOperation('create_workflow_api_v1_workflows_post', {
		token,
		body,
	});
}

export function createChat(token: string, body: ChatCreateRequest, signal?: AbortSignal) {
	return requestGeneratedOperation<'create_chat_api_v1_chats_post', ChatCreateResponse>(
		'create_chat_api_v1_chats_post',
		{
			token,
			body,
			signal,
		},
	);
}

export function createSession(token: string, body: SessionCreateRequest, signal?: AbortSignal) {
	return requestGeneratedOperation<'create_session_api_v1_sessions_post', SessionCreateResponse>(
		'create_session_api_v1_sessions_post',
		{
			token,
			body,
			signal,
		},
	);
}

export function createPipeline(token: string, body: PipelineCreateRequest, signal?: AbortSignal) {
	return requestGeneratedOperation<'create_pipeline_api_v1_pipelines_post', PipelineCreateResponse>(
		'create_pipeline_api_v1_pipelines_post',
		{
			token,
			body,
			signal,
		},
	);
}

/** Creates a real project thread bound to an owner entity; requires the write token. */
export function createThread(token: string, body: ThreadCreateRequest, signal?: AbortSignal) {
	return requestGeneratedOperation<'create_thread_api_v1_threads_post', ThreadDetailResponse>(
		'create_thread_api_v1_threads_post',
		{ token, body, signal },
	);
}

/** Reads one thread with its full timeline (messages, artifacts, decisions, events); a read. */
export function getThread(threadId: string, signal?: AbortSignal) {
	return requestGeneratedOperation<
		'get_thread_api_v1_threads__thread_id__get',
		ThreadDetailResponse
	>('get_thread_api_v1_threads__thread_id__get', {
		pathParams: { thread_id: threadId },
		signal,
	});
}

/** Posts a user message and runs the coordinator (responds or blocks); requires the write token. */
export function postThreadMessage(
	token: string,
	threadId: string,
	body: ThreadMessageRequest,
	signal?: AbortSignal,
) {
	return requestGeneratedOperation<
		'post_message_api_v1_threads__thread_id__messages_post',
		ThreadMessageResultResponse
	>('post_message_api_v1_threads__thread_id__messages_post', {
		token,
		pathParams: { thread_id: threadId },
		body,
		signal,
	});
}

/** Resolves a pending decision request and reopens the thread; requires the write token. */
export function resolveThreadDecision(
	token: string,
	threadId: string,
	decisionId: string,
	body: ThreadDecisionResolveRequest,
	signal?: AbortSignal,
) {
	return requestGeneratedOperation<
		'resolve_decision_api_v1_threads__thread_id__decisions__decision_id__resolve_post',
		ThreadDecisionResolveResponse
	>('resolve_decision_api_v1_threads__thread_id__decisions__decision_id__resolve_post', {
		token,
		pathParams: { thread_id: threadId, decision_id: decisionId },
		body,
		signal,
	});
}

export function runIssueToPatch(token: string, body: IssueToPatchRequest) {
	return requestGeneratedOperation('run_issue_to_patch_api_v1_workflows_issue_to_patch_post', {
		token,
		body,
	});
}

export function runIssueToPr(token: string, body: IssueToPrRequest) {
	return requestGeneratedOperation('run_issue_to_pr_api_v1_workflows_issue_to_pr_post', {
		token,
		body,
	});
}

export function approveIssueToPatch(token: string, runId: string, reason: string) {
	return requestGeneratedOperation<
		'approve_issue_to_patch_api_v1_workflows_issue_to_patch__run_id__approve_post',
		IssueToPatchApprovalResponse
	>('approve_issue_to_patch_api_v1_workflows_issue_to_patch__run_id__approve_post', {
		token,
		pathParams: { run_id: runId },
		body: { reason },
	});
}

export function approveIssueToPr(token: string, runId: string, reason: string) {
	return requestGeneratedOperation<
		'approve_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__approve_post',
		IssueToPrApprovalResponse
	>('approve_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__approve_post', {
		token,
		pathParams: { run_id: runId },
		body: { reason },
	});
}

export function promotePatchToBranch(
	token: string,
	runId: string,
	body: PromotePatchToBranchRequest,
) {
	return requestGeneratedOperation<
		'promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post',
		PromotePatchToBranchResponse
	>('promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post', {
		token,
		pathParams: { run_id: runId },
		body,
	});
}

export function promoteIssueToPrBranch(
	token: string,
	runId: string,
	body: PromoteIssueToPrBranchRequest,
) {
	return requestGeneratedOperation<
		'promote_issue_to_pr_branch_api_v1_workflows_issue_to_pr__run_id__promote_post',
		PromoteIssueToPrBranchResponse
	>('promote_issue_to_pr_branch_api_v1_workflows_issue_to_pr__run_id__promote_post', {
		token,
		pathParams: { run_id: runId },
		body,
	});
}

export function createPullRequestFromPromotedBranch(
	token: string,
	runId: string,
	body: PullRequestCreateRequest,
) {
	return requestGeneratedOperation<
		'create_pull_request_from_promoted_branch_api_v1_workflows_issue_to_patch__run_id__pull_request_post',
		PullRequestCreateResponse
	>(
		'create_pull_request_from_promoted_branch_api_v1_workflows_issue_to_patch__run_id__pull_request_post',
		{
			token,
			pathParams: { run_id: runId },
			body,
		},
	);
}

export function createPullRequestFromIssueToPr(
	token: string,
	runId: string,
	body: IssueToPrPullRequestCreateRequest,
) {
	return requestGeneratedOperation<
		'create_pull_request_from_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__pull_request_post',
		IssueToPrPullRequestCreateResponse
	>(
		'create_pull_request_from_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__pull_request_post',
		{
			token,
			pathParams: { run_id: runId },
			body,
		},
	);
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

export function runResearchAgent(token: string, body: ResearchAgentRunRequest) {
	return requestGeneratedOperation('run_research_agent_api_v1_agents_research_runs_post', {
		token,
		body,
	});
}

export function createAgentProfile(
	token: string,
	body: MutationBody<'upsert_agent_profile_api_v1_agent_profiles_post'>,
) {
	return requestGeneratedOperation('upsert_agent_profile_api_v1_agent_profiles_post', {
		token,
		body,
	});
}

export function getAgentProfiles(projectId?: string, signal?: AbortSignal) {
	return requestGeneratedOperation<
		'list_agent_profiles_api_v1_agent_profiles_get',
		AgentProfilesResponse
	>('list_agent_profiles_api_v1_agent_profiles_get', {
		query: projectId ? { projectId } : undefined,
		signal,
	});
}

export function upsertAgentProfileProjectOverride(
	token: string,
	projectId: string,
	profileId: string,
	body: AgentProfileProjectOverrideRequest,
) {
	return requestGeneratedOperation<
		'agent_profile_override_api_v1_projects__project_id__agent_profile_overrides__profile_id__put',
		AgentProfileProjectOverrideResponse
	>(
		'agent_profile_override_api_v1_projects__project_id__agent_profile_overrides__profile_id__put',
		{
			token,
			pathParams: { project_id: projectId, profile_id: profileId },
			body,
		},
	);
}

export function createModelGatewayRolePolicy(
	token: string,
	body: MutationBody<'create_role_policy_api_v1_model_gateway_role_policies_post'>,
) {
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

export function updateRisk(
	token: string,
	riskId: string,
	body: MutationBody<'update_risk_api_v1_risks__risk_id__patch'>,
) {
	return requestGeneratedOperation('update_risk_api_v1_risks__risk_id__patch', {
		token,
		pathParams: { risk_id: riskId },
		body,
	});
}

export function createArchitectureDecision(
	token: string,
	body: MutationBody<'create_architecture_decision_api_v1_architecture_decisions_post'>,
) {
	return requestGeneratedOperation(
		'create_architecture_decision_api_v1_architecture_decisions_post',
		{
			token,
			body,
		},
	);
}

export function createNextStep(
	token: string,
	body: MutationBody<'create_next_step_api_v1_next_steps_post'>,
) {
	return requestGeneratedOperation('create_next_step_api_v1_next_steps_post', {
		token,
		body,
	});
}

export function updateSandboxProfile(
	token: string,
	profileId: string,
	body: MutationBody<'update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch'>,
) {
	return requestGeneratedOperation(
		'update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch',
		{
			token,
			pathParams: { profile_id: profileId },
			body,
		},
	);
}

export function registerMcpServer(
	token: string,
	body: MutationBody<'register_mcp_server_api_v1_integrations_mcp_register_post'>,
) {
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
	return requestGeneratedOperation(
		'list_routing_profiles_api_v1_model_gateway_routing_profiles_get',
		{ signal },
	);
}

export function getModelGatewayRolePolicies(signal?: AbortSignal) {
	return requestGeneratedOperation('list_role_policies_api_v1_model_gateway_role_policies_get', {
		signal,
	});
}

export function getModelGatewayUsageLedger(signal?: AbortSignal) {
	return requestGeneratedOperation('list_usage_ledger_api_v1_model_gateway_usage_ledger_get', {
		signal,
	});
}

export function getModelGatewayUsageSummary(signal?: AbortSignal) {
	return requestGeneratedOperation('usage_summary_api_v1_model_gateway_usage_ledger_summary_get', {
		signal,
	});
}

export function getModelGatewayRoutingDecisions(signal?: AbortSignal) {
	return requestGeneratedOperation(
		'list_routing_decisions_api_v1_model_gateway_routing_decisions_get',
		{ signal },
	);
}

export function getModelGatewayProviderLimits(signal?: AbortSignal) {
	return requestGeneratedOperation(
		'list_provider_limits_api_v1_model_gateway_provider_limits_get',
		{ signal },
	);
}

export function getModelGatewayBudgetRules(signal?: AbortSignal) {
	return requestGeneratedOperation('list_budget_rules_api_v1_model_gateway_budget_rules_get', {
		signal,
	});
}

export function getModelGatewayCliRuntimes(signal?: AbortSignal) {
	return requestGeneratedOperation('list_cli_runtimes_api_v1_model_gateway_cli_runtimes_get', {
		signal,
	});
}

export function detectModelGatewayCliRuntime(token: string, runtimeId: string) {
	return requestGeneratedOperation(
		'detect_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__detect_post',
		{
			token,
			pathParams: { runtime_id: runtimeId },
		},
	);
}

export function getModelGatewayCliSessions(signal?: AbortSignal) {
	return requestGeneratedOperation('list_cli_sessions_api_v1_model_gateway_cli_sessions_get', {
		signal,
	});
}

export function getModelGatewayBenchmarks(signal?: AbortSignal) {
	return requestGeneratedOperation('list_benchmarks_api_v1_model_gateway_benchmarks_get', {
		signal,
	});
}

export function getModelGatewayBenchmarkOutcomes(signal?: AbortSignal) {
	return requestGeneratedOperation(
		'list_benchmark_outcomes_api_v1_model_gateway_benchmark_outcomes_get',
		{ signal },
	);
}

export function recordModelGatewayBenchmarkOutcome(
	token: string,
	body: ModelGatewayBenchmarkOutcomeRequest,
) {
	return requestGeneratedOperation<'create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post'>(
		'create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post',
		{
			token,
			body,
		},
	);
}

export function previewModelRoute(token: string, body: ModelGatewayRoutePreviewRequest) {
	return requestGeneratedOperation<'route_preview_api_v1_model_gateway_route_preview_post'>(
		'route_preview_api_v1_model_gateway_route_preview_post',
		{
			token,
			body,
		},
	);
}

export function patchModelGatewayProvider(
	token: string,
	providerId: string,
	body: ModelGatewayProviderPatchRequest,
) {
	return requestGeneratedOperation(
		'patch_provider_api_v1_model_gateway_providers__provider_id__patch',
		{
			token,
			pathParams: { provider_id: providerId },
			body,
		},
	);
}

export function healthCheckModelGatewayProvider(token: string, providerId: string) {
	return requestGeneratedOperation(
		'provider_health_check_api_v1_model_gateway_providers__provider_id__health_check_post',
		{
			token,
			pathParams: { provider_id: providerId },
		},
	);
}

export function discoverModelGatewayProviderModels(token: string, providerId: string) {
	return requestGeneratedOperation(
		'discover_models_api_v1_model_gateway_providers__provider_id__discover_models_post',
		{
			token,
			pathParams: { provider_id: providerId },
		},
	);
}
