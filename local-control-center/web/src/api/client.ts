import type { Dictionary, Overview, RetrievalStatus, RuntimeProviders } from './types';
import { requestGeneratedOperation } from './generated/openapi';
import type { ApiOperationId, OperationRequestBody } from './generated/openapi';

const WRITE_HEADER = 'X-Local-Control-Token';

type MutationBody<TOperationId extends ApiOperationId> = OperationRequestBody<TOperationId>;

export type ArtifactPayload = {
	artifactId: string;
	hash: string;
	contentType: string;
	blob: Blob;
	text: string;
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
	options: { method?: string; token?: string; body?: Dictionary; signal?: AbortSignal } = {},
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
	return {
		artifactId: response.headers.get('X-AIDO-Artifact-Id') ?? artifactId,
		hash: response.headers.get('X-AIDO-Artifact-Hash') ?? '',
		contentType,
		blob,
		text: textLike ? await blob.text() : '',
	};
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

export function createAgentProfile(token: string, body: MutationBody<'upsert_agent_profile_api_v1_agent_profiles_post'>) {
	return requestGeneratedOperation('upsert_agent_profile_api_v1_agent_profiles_post', {
		token,
		body,
	});
}

export function createModelPolicy(token: string, body: MutationBody<'upsert_model_policy_api_v1_model_policies_post'>) {
	return requestGeneratedOperation('upsert_model_policy_api_v1_model_policies_post', {
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
