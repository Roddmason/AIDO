/**
 * Domain logic for the Ollama endpoints surface: turns one `/api/v1/ollama/endpoints` record plus the
 * role policies into the facts a card states (kind, base URL, health, latency, models, roles it is
 * preferred for), and validates an endpoint draft against the same rules the backend enforces before
 * a request is spent. UI-agnostic so the panel, its dialogs and the tests read one source of truth.
 * @author Rodrigo Mason
 */

import type { ModelGatewayRolePolicy, OllamaEndpoint } from '../../api/types';

/** Mirrors the backend `ENDPOINT_ID_RE`, so an invalid id is rejected before the 422 round-trip. */
const ENDPOINT_ID_RE = /^[a-z0-9][a-z0-9_.:-]{1,95}$/;

/** Health tones for the badge; anything the backend has not catalogued reads as a warning. */
const HEALTH_TONE: Record<string, EndpointHealthTone> = {
	healthy: 'ok',
	degraded: 'warn',
	unknown: 'info',
	offline: 'danger',
	unavailable: 'danger',
	misconfigured: 'danger',
};

export type EndpointHealthTone = 'ok' | 'warn' | 'danger' | 'info';

/** The endpoint facts a card renders, joined from the endpoint record and the role policies. */
export type EndpointCardModel = {
	id: string;
	displayName: string;
	kind: 'local' | 'remote';
	baseUrl: string;
	enabled: boolean;
	healthStatus: string;
	healthTone: EndpointHealthTone;
	/** `null` when the endpoint has never been validated (unknown, not zero). */
	latencyMs: number | null;
	models: string[];
	/** Why the last probe failed; empty while the endpoint is healthy or unprobed. */
	failureReason: string;
	credentialConfigured: boolean;
	/** Roles whose policy lists this endpoint as a preferred candidate. */
	preferredRoles: string[];
};

/** The `preferred` candidate list of a role policy, as the generated contract describes it. */
type PreferredCandidates = ModelGatewayRolePolicy['preferred'];

/** Roles that route to this endpoint first (its `preferred` list contains the provider id). */
export function preferredRolesForEndpoint(
	providerId: string,
	policies: readonly ModelGatewayRolePolicy[],
): string[] {
	return policies
		.filter((policy) => policy.preferred.some((item) => item.provider === providerId))
		.map((policy) => policy.role)
		.filter(Boolean);
}

export function endpointHealthTone(healthStatus: string): EndpointHealthTone {
	return HEALTH_TONE[healthStatus] ?? 'warn';
}

/** Build the card view-model for one endpoint; a failure reason only surfaces while unhealthy. */
export function deriveEndpointCard(
	endpoint: OllamaEndpoint,
	rolePolicies: readonly ModelGatewayRolePolicy[],
): EndpointCardModel {
	const healthStatus = String(endpoint.healthStatus || 'unknown');
	const lastError = String(endpoint.lastError || '').trim();
	return {
		id: endpoint.id,
		displayName: endpoint.displayName || endpoint.id,
		kind: endpoint.kind,
		baseUrl: String(endpoint.baseUrl || ''),
		enabled: Boolean(endpoint.enabled),
		healthStatus,
		healthTone: endpointHealthTone(healthStatus),
		latencyMs: typeof endpoint.latencyMs === 'number' ? endpoint.latencyMs : null,
		models: endpoint.models ?? [],
		failureReason: healthStatus === 'healthy' ? '' : lastError,
		credentialConfigured: String(endpoint.credentialStatus || '') === 'configured',
		preferredRoles: preferredRolesForEndpoint(endpoint.id, rolePolicies),
	};
}

/**
 * Move this endpoint's model to the head of a role's preferred list, dropping any earlier entry for
 * the same pair so repeated "set preferred" clicks stay idempotent. Other candidates keep their
 * order and their extra fields (effort, requiresApproval) because this action only reorders intent.
 */
export function preferredWithEndpointFirst(
	policy: ModelGatewayRolePolicy,
	providerId: string,
	model: string,
): PreferredCandidates {
	const rest = policy.preferred.filter(
		(item) => !(item.provider === providerId && item.model === model),
	);
	return [{ provider: providerId, model }, ...rest];
}

export function isValidEndpointId(value: string): boolean {
	return ENDPOINT_ID_RE.test(value.trim());
}

/** Mirrors the backend `_normalize_base_url`: absolute http(s), no query, params or fragment. */
export function isAbsoluteHttpUrl(value: string): boolean {
	let parsed: URL;
	try {
		parsed = new URL(value.trim());
	} catch {
		return false;
	}
	if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
	return Boolean(parsed.host) && !parsed.search && !parsed.hash;
}

export function normalizeBaseUrl(value: string): string {
	return value.trim().replace(/\/+$/, '');
}
