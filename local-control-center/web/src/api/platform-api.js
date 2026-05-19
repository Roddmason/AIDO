const JSON_HEADERS = { 'Content-Type': 'application/json' };
const WRITE_HEADER = 'X-Local-Control-Token';

async function parseResponse(response) {
	const text = await response.text();
	const payload = text ? JSON.parse(text) : {};
	if (!response.ok) {
		const detail = payload.detail || payload.error || response.statusText;
		throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
	}
	return payload;
}

export async function apiRequest(path, { method = 'GET', token, body, signal } = {}) {
	const headers = { ...JSON_HEADERS };
	if (token) headers[WRITE_HEADER] = token;
	const response = await fetch(path, {
		method,
		headers,
		body: body === undefined ? undefined : JSON.stringify(body),
		signal,
	});
	return parseResponse(response);
}

export function getHandshake(signal) {
	return apiRequest('/api/v1/security/handshake', { signal });
}

export function getOverview(signal) {
	return apiRequest('/api/v1/overview', { signal });
}

export function getRetrievalStatus(signal) {
	return apiRequest('/api/v1/retrieval/status', { signal });
}

export function getJobs(signal) {
	return apiRequest('/api/v1/jobs', { signal });
}

export function getApprovals(signal) {
	return apiRequest('/api/v1/approvals', { signal });
}

export function createJob(token, body) {
	return apiRequest('/api/v1/jobs', { method: 'POST', token, body });
}

export function approveJob(token, jobId, reason = '') {
	return apiRequest(`/api/v1/jobs/${encodeURIComponent(jobId)}/approve`, {
		method: 'POST',
		token,
		body: { reason },
	});
}

export function cancelJob(token, jobId, reason = '') {
	return apiRequest(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
		method: 'POST',
		token,
		body: { reason },
	});
}

export function retryJob(token, jobId, reason = '') {
	return apiRequest(`/api/v1/jobs/${encodeURIComponent(jobId)}/retry`, {
		method: 'POST',
		token,
		body: { reason },
	});
}

export function approveAction(token, jobId, actionId, reason = '') {
	return apiRequest(`/api/v1/jobs/${encodeURIComponent(jobId)}/actions/${encodeURIComponent(actionId)}/approve`, {
		method: 'POST',
		token,
		body: { reason },
	});
}

export function denyAction(token, jobId, actionId, reason = '') {
	return apiRequest(`/api/v1/jobs/${encodeURIComponent(jobId)}/actions/${encodeURIComponent(actionId)}/deny`, {
		method: 'POST',
		token,
		body: { reason },
	});
}

export function searchRetrieval(body) {
	return apiRequest('/api/v1/retrieval/search', { method: 'POST', body });
}

export function reindexRetrieval(token) {
	return apiRequest('/api/v1/retrieval/reindex', { method: 'POST', token, body: {} });
}

export function createMemory(token, body) {
	return apiRequest('/api/v1/memory', { method: 'POST', token, body });
}
