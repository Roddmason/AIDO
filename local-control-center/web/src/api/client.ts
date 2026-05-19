import type { Dictionary, Overview, RuntimeProviders } from './types';

const WRITE_HEADER = 'X-Local-Control-Token';

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
	return apiRequest<{ token: string }>('/api/v1/security/handshake', { signal });
}

export function getOverview(signal?: AbortSignal) {
	return apiRequest<Overview>('/api/v1/overview', { signal });
}

export function getRetrievalStatus(signal?: AbortSignal) {
	return apiRequest<Dictionary>('/api/v1/retrieval/status', { signal });
}

export function getRuntimeProviders(signal?: AbortSignal) {
	return apiRequest<RuntimeProviders>('/api/v1/runtime/providers', { signal });
}

export function approveAction(token: string, jobId: string, actionId: string, reason: string) {
	return apiRequest<Dictionary>(`/api/v1/jobs/${encodeURIComponent(jobId)}/actions/${encodeURIComponent(actionId)}/approve`, {
		method: 'POST',
		token,
		body: { reason },
	});
}

export function denyAction(token: string, jobId: string, actionId: string, reason: string) {
	return apiRequest<Dictionary>(`/api/v1/jobs/${encodeURIComponent(jobId)}/actions/${encodeURIComponent(actionId)}/deny`, {
		method: 'POST',
		token,
		body: { reason },
	});
}

export function cancelJob(token: string, jobId: string, reason: string) {
	return apiRequest<Dictionary>(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
		method: 'POST',
		token,
		body: { reason },
	});
}

export function retryJob(token: string, jobId: string, reason: string) {
	return apiRequest<Dictionary>(`/api/v1/jobs/${encodeURIComponent(jobId)}/retry`, {
		method: 'POST',
		token,
		body: { reason },
	});
}

export function createWorkflow(token: string, projectId: string, title: string) {
	return apiRequest<Dictionary>('/api/v1/workflows', {
		method: 'POST',
		token,
		body: { projectId, title, kind: 'idea_to_pr' },
	});
}

export function createWorkflowWithBody(token: string, body: Dictionary) {
	return apiRequest<Dictionary>('/api/v1/workflows', {
		method: 'POST',
		token,
		body,
	});
}

export function createAgentProfile(token: string, body: Dictionary) {
	return apiRequest<Dictionary>('/api/v1/agent-profiles', {
		method: 'POST',
		token,
		body,
	});
}

export function createModelPolicy(token: string, body: Dictionary) {
	return apiRequest<Dictionary>('/api/v1/model-policies', {
		method: 'POST',
		token,
		body,
	});
}

export function createRisk(token: string, body: Dictionary) {
	return apiRequest<Dictionary>('/api/v1/risks', {
		method: 'POST',
		token,
		body,
	});
}

export function createArchitectureDecision(token: string, body: Dictionary) {
	return apiRequest<Dictionary>('/api/v1/architecture-decisions', {
		method: 'POST',
		token,
		body,
	});
}

export function createNextStep(token: string, body: Dictionary) {
	return apiRequest<Dictionary>('/api/v1/next-steps', {
		method: 'POST',
		token,
		body,
	});
}

export function updateSandboxProfile(token: string, profileId: string, body: Dictionary) {
	return apiRequest<Dictionary>(`/api/v1/sandbox/profiles/${encodeURIComponent(profileId)}`, {
		method: 'PATCH',
		token,
		body,
	});
}

export function registerMcpServer(token: string, body: Dictionary) {
	return apiRequest<Dictionary>('/api/v1/integrations/mcp/register', {
		method: 'POST',
		token,
		body,
	});
}
