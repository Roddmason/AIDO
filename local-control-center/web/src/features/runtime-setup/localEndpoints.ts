/**
 * Domain logic for local OpenAI-compatible model servers (llama.cpp, LM Studio, vLLM, generic): the
 * facts a card states for one `/api/v1/local-endpoints` record, the wizard draft, the loopback hint
 * the endpoint step shows before the backend classifies the host, and the parsers for the structured
 * 409/422 details the local-endpoint routes return. UI-agnostic so the panel, the wizard, the
 * discovery list and the tests read one source of truth.
 * @author Rodrigo Mason
 */

import type { LocalEndpointView, LocalModelView, LocalRuntimeSuggestion } from '../../api/client';
import type { StatusTone } from '../../components/ui';
import { type EndpointHealthTone, endpointHealthTone } from './ollamaEndpoints';
import { catalogEntry } from './runtimeSetup';

/** Catalog ids the Ollama endpoints panel already owns; the local panel lists every other server. */
const OLLAMA_CATALOG_IDS = new Set(['ollama', 'ollama_remote']);

type LabelMeta = { tone: StatusTone; labelKey: string; fallback: string };

export type EndpointLocality = LocalEndpointView['locality'];

/** Where the backend says the server runs; only loopback and declared servers are private. */
export const LOCALITY_META: Record<EndpointLocality, LabelMeta> = {
	loopback: {
		tone: 'ok',
		labelKey: 'app.localRuntime.locality.loopback',
		fallback: 'this machine',
	},
	declared_local: {
		tone: 'info',
		labelKey: 'app.localRuntime.locality.declaredLocal',
		fallback: 'declared local (WSL/Docker)',
	},
	remote: {
		tone: 'warn',
		labelKey: 'app.localRuntime.locality.remote',
		fallback: 'treated as remote',
	},
};

/** The facts one local endpoint card renders. */
export type LocalEndpointCardModel = {
	id: string;
	displayName: string;
	/** Catalog name of the server kind (llama.cpp, LM Studio…), or the raw catalog id. */
	serverLabel: string;
	baseUrl: string;
	enabled: boolean;
	healthStatus: string;
	healthTone: EndpointHealthTone;
	locality: EndpointLocality;
	/** The backend reads load state only for enabled, non-remote servers; otherwise it is unknown. */
	loadStateTracked: boolean;
	loadedModels: string[];
	modelCount: number;
	/** Why the last probe failed; empty while the endpoint is healthy or unprobed. */
	failureReason: string;
	hasCredential: boolean;
};

/** Reference the backend names when it refuses to delete an endpoint that is still in use. */
export type EndpointReference = { kind: string; id: string; label: string };

export function isOllamaEndpoint(endpoint: LocalEndpointView): boolean {
	return OLLAMA_CATALOG_IDS.has(endpoint.catalogId);
}

export function deriveLocalEndpointCard(endpoint: LocalEndpointView): LocalEndpointCardModel {
	const healthStatus = endpoint.healthStatus || 'unknown';
	return {
		id: endpoint.id,
		displayName: endpoint.displayName || endpoint.id,
		serverLabel: catalogEntry(endpoint.catalogId)?.displayName ?? endpoint.catalogId,
		baseUrl: endpoint.baseUrl,
		enabled: endpoint.enabled,
		healthStatus,
		healthTone: endpointHealthTone(healthStatus),
		locality: endpoint.locality,
		loadStateTracked: endpoint.enabled && endpoint.locality !== 'remote',
		loadedModels: endpoint.loadedModels ?? [],
		modelCount: endpoint.models?.length ?? 0,
		failureReason: healthStatus === 'healthy' ? '' : (endpoint.healthReason ?? '').trim(),
		hasCredential: endpoint.hasCredential,
	};
}

export function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

/** The generated client throws `JSON.stringify(detail)` for structured details; parse it back. */
function detailObject(error: unknown): Record<string, unknown> | null {
	try {
		const parsed: unknown = JSON.parse(errorMessage(error));
		return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
			? (parsed as Record<string, unknown>)
			: null;
	} catch {
		return null;
	}
}

function isReference(value: unknown): value is EndpointReference {
	if (typeof value !== 'object' || value === null) return false;
	const record = value as Record<string, unknown>;
	return (
		typeof record.kind === 'string' &&
		typeof record.id === 'string' &&
		typeof record.label === 'string'
	);
}

/** The references of a 409 `local_endpoint_in_use`, or null for any other failure. */
export function endpointInUseReferences(error: unknown): EndpointReference[] | null {
	const detail = detailObject(error);
	if (detail?.code !== 'local_endpoint_in_use' || !Array.isArray(detail.references)) return null;
	return detail.references.filter(isReference);
}

const IPV4_LOOPBACK_RE = /^127(?:\.\d{1,3}){3}$/;
/** `new URL()` rewrites an IPv4-mapped `::ffff:127.x.y.z` host to its hex form `::ffff:7fxx:yyyy`. */
const MAPPED_LOOPBACK_RE = /^::ffff:7f[0-9a-f]{2}:[0-9a-f]{1,4}$/;

export type LoadState = LocalModelView['loadState'];

/** Load state of one model as the server reports it; `unknown` is never shown as unloaded. */
export const LOAD_STATE_META: Record<LoadState, LabelMeta> = {
	loaded: { tone: 'ok', labelKey: 'app.localRuntime.loadState.loaded', fallback: 'loaded' },
	loading: { tone: 'warn', labelKey: 'app.localRuntime.loadState.loading', fallback: 'loading' },
	unloaded: { tone: 'info', labelKey: 'app.localRuntime.loadState.unloaded', fallback: 'unloaded' },
	unknown: {
		tone: 'pending',
		labelKey: 'app.localRuntime.loadState.unknown',
		fallback: 'state unknown',
	},
};

/**
 * Where the wizard was opened from: a catalog card may already have a stored account to read back,
 * a suggestion is new, and an endpoint (a deep link to one instance) is edited as stored.
 */
export type LocalRuntimeDraft = {
	source: 'catalog' | 'suggestion' | 'endpoint';
	catalogId: string;
	baseUrl: string;
	/** Stored endpoint being edited; null creates a new one. */
	endpointId: string | null;
	displayName: string;
};

/** The enabled model marked as default, or null while the operator has not chosen one. */
export function defaultModelOf(endpoint: LocalEndpointView): string | null {
	return (endpoint.models ?? []).find((model) => model.isDefault && model.enabled)?.model ?? null;
}

/**
 * Client-side hint only: the backend decides locality (and re-resolves declared names). Anything
 * that is not literally loopback is shown as remote until the operator declares it.
 */
export function isLoopbackUrl(value: string): boolean {
	let hostname: string;
	try {
		hostname = new URL(value.trim()).hostname;
	} catch {
		return false;
	}
	const host = hostname.replace(/^\[|\]$/g, '').toLowerCase();
	return (
		host === 'localhost' ||
		host === '::1' ||
		IPV4_LOOPBACK_RE.test(host) ||
		MAPPED_LOOPBACK_RE.test(host)
	);
}

export function draftFromCatalog(catalogId: string): LocalRuntimeDraft {
	return {
		source: 'catalog',
		catalogId,
		baseUrl: catalogEntry(catalogId)?.defaultBaseUrl ?? '',
		endpointId: null,
		displayName: '',
	};
}

/** Machine code of a structured error detail (`local_declaration_host_not_allowed`…), or ''. */
export function errorCode(error: unknown): string {
	const code = detailObject(error)?.code;
	return typeof code === 'string' ? code : '';
}

export function draftFromSuggestion(suggestion: LocalRuntimeSuggestion): LocalRuntimeDraft {
	return {
		source: 'suggestion',
		catalogId: suggestion.catalogId,
		baseUrl: suggestion.baseUrl,
		endpointId: null,
		displayName: '',
	};
}

export function draftFromEndpoint(endpoint: LocalEndpointView): LocalRuntimeDraft {
	return {
		source: 'endpoint',
		catalogId: endpoint.catalogId,
		baseUrl: endpoint.baseUrl,
		endpointId: endpoint.id,
		displayName: endpoint.displayName,
	};
}
