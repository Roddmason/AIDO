/**
 * Shared domain logic for the Providers & CLI setup catalog: the fixed 15-provider list, their
 * presentation metadata (icon, group, preconfigured base URL, whether the operator must supply a
 * URL, and how they authenticate), the left-join of live runtime status with configuration, and the
 * rule that collapses the backend readiness booleans into one user-facing state. UI-agnostic so the
 * full panel, the wizard and the compact inspector card all read the same source of truth.
 * @author Rodrigo Mason
 */

import type { LucideProps } from 'lucide-react';
import {
	AlertTriangle,
	Bot,
	Boxes,
	CheckCircle2,
	CircleDashed,
	Clock,
	Cloud,
	Cpu,
	Network,
	PlugZap,
	Server,
	Sparkles,
	SquareTerminal,
	Wind,
	XCircle,
	Zap,
} from 'lucide-react';
import type { ComponentType } from 'react';

import type { RuntimeProvider, RuntimeProviderConfiguration } from '../../api/types';

type IconComponent = ComponentType<LucideProps>;

/** How a provider is reached — drives grouping, the kind badge and which wizard fields appear. */
export type ProviderGroup = 'cli' | 'local' | 'api' | 'gateway';

/** How a provider authenticates: required token, optional token, or none (CLI login / local daemon). */
export type ProviderAuthKind = 'api_key' | 'optional_api_key' | 'none';

/** Static presentation + wizard metadata for one catalog provider (joined at runtime with live data). */
export type ProviderCatalogEntry = {
	/** Model-gateway provider account id (the join key across runtime status, account and models). */
	id: string;
	displayName: string;
	group: ProviderGroup;
	Icon: IconComponent;
	/** Provider account `providerType` used when the wizard upserts the account. */
	providerType: string;
	/** Provider account `apiFormat` used when the wizard upserts the account. */
	apiFormat: string;
	/** Preconfigured base URL shown on the card, or null when there is no sensible default. */
	defaultBaseUrl: string | null;
	/** True only for custom/remote/Azure providers whose endpoint the operator must supply. */
	needsBaseUrl: boolean;
	/** Drives required, optional, or absent credential fields in the wizard. */
	authKind: ProviderAuthKind;
	/** Declared capabilities shown as chips before any model is discovered. */
	capabilities: string[];
	/** i18n key for the per-provider "how to configure" copy. */
	instructionsKey: string;
};

/**
 * The exact providers the setup catalog explains, in display order. Driving the cards from this
 * constant (not from either API response) guarantees every requested provider renders — even one the
 * backend has not catalogued yet — so a card can always answer "how do I connect this?".
 */
export const PROVIDER_CATALOG: readonly ProviderCatalogEntry[] = [
	{
		id: 'codex_cli',
		displayName: 'Codex CLI',
		group: 'cli',
		Icon: SquareTerminal,
		providerType: 'cli',
		apiFormat: 'cli',
		defaultBaseUrl: null,
		needsBaseUrl: false,
		authKind: 'none',
		capabilities: ['code_edit', 'issue_to_patch'],
		instructionsKey: 'app.runtime.instructions.codex_cli',
	},
	{
		id: 'claude_code_cli',
		displayName: 'Claude Code CLI',
		group: 'cli',
		Icon: Bot,
		providerType: 'cli',
		apiFormat: 'cli',
		defaultBaseUrl: null,
		needsBaseUrl: false,
		authKind: 'none',
		capabilities: ['code_edit', 'issue_to_patch'],
		instructionsKey: 'app.runtime.instructions.claude_code_cli',
	},
	{
		id: 'ollama',
		displayName: 'Ollama local',
		group: 'local',
		Icon: Boxes,
		providerType: 'local',
		apiFormat: 'ollama',
		defaultBaseUrl: 'http://localhost:11434',
		needsBaseUrl: false,
		authKind: 'none',
		capabilities: ['chat', 'local', 'private'],
		instructionsKey: 'app.runtime.instructions.ollama',
	},
	{
		id: 'ollama_remote',
		displayName: 'Ollama remote',
		group: 'local',
		Icon: Server,
		providerType: 'local',
		apiFormat: 'ollama',
		defaultBaseUrl: null,
		needsBaseUrl: true,
		authKind: 'optional_api_key',
		capabilities: ['chat', 'self_hosted'],
		instructionsKey: 'app.runtime.instructions.ollama_remote',
	},
	{
		id: 'openai_api',
		displayName: 'OpenAI',
		group: 'api',
		Icon: Sparkles,
		providerType: 'api',
		apiFormat: 'responses',
		defaultBaseUrl: 'https://api.openai.com/v1',
		needsBaseUrl: false,
		authKind: 'api_key',
		capabilities: ['chat', 'tools', 'json', 'vision', 'reasoning'],
		instructionsKey: 'app.runtime.instructions.openai_api',
	},
	{
		id: 'anthropic_api',
		displayName: 'Anthropic',
		group: 'api',
		Icon: Bot,
		providerType: 'api',
		apiFormat: 'anthropic',
		defaultBaseUrl: 'https://api.anthropic.com/v1',
		needsBaseUrl: false,
		authKind: 'api_key',
		capabilities: ['chat', 'tools', 'json', 'vision', 'reasoning'],
		instructionsKey: 'app.runtime.instructions.anthropic_api',
	},
	{
		id: 'openrouter',
		displayName: 'OpenRouter',
		group: 'gateway',
		Icon: Network,
		providerType: 'gateway',
		apiFormat: 'openai_compatible',
		defaultBaseUrl: 'https://openrouter.ai/api/v1',
		needsBaseUrl: false,
		authKind: 'api_key',
		capabilities: ['chat', 'tools', 'routing'],
		instructionsKey: 'app.runtime.instructions.openrouter',
	},
	{
		id: 'nvidia_nim',
		displayName: 'NVIDIA NIM',
		group: 'api',
		Icon: Cpu,
		providerType: 'api',
		apiFormat: 'openai_compatible',
		defaultBaseUrl: 'https://integrate.api.nvidia.com/v1',
		needsBaseUrl: false,
		authKind: 'api_key',
		capabilities: ['chat', 'tools', 'vision'],
		instructionsKey: 'app.runtime.instructions.nvidia_nim',
	},
	{
		id: 'deepseek',
		displayName: 'DeepSeek',
		group: 'api',
		Icon: Wind,
		providerType: 'api',
		apiFormat: 'openai_compatible',
		defaultBaseUrl: 'https://api.deepseek.com',
		needsBaseUrl: false,
		authKind: 'api_key',
		capabilities: ['chat', 'tools', 'json', 'reasoning'],
		instructionsKey: 'app.runtime.instructions.deepseek',
	},
	{
		id: 'kimi',
		displayName: 'Kimi (Moonshot)',
		group: 'api',
		Icon: Sparkles,
		providerType: 'api',
		apiFormat: 'openai_compatible',
		defaultBaseUrl: 'https://api.moonshot.ai/v1',
		needsBaseUrl: false,
		authKind: 'api_key',
		capabilities: ['chat', 'tools', 'json'],
		instructionsKey: 'app.runtime.instructions.kimi',
	},
	{
		id: 'mistral',
		displayName: 'Mistral',
		group: 'api',
		Icon: Wind,
		providerType: 'api',
		apiFormat: 'openai_compatible',
		defaultBaseUrl: 'https://api.mistral.ai/v1',
		needsBaseUrl: false,
		authKind: 'api_key',
		capabilities: ['chat', 'tools', 'json'],
		instructionsKey: 'app.runtime.instructions.mistral',
	},
	{
		id: 'groq',
		displayName: 'Groq',
		group: 'api',
		Icon: Zap,
		providerType: 'api',
		apiFormat: 'openai_compatible',
		defaultBaseUrl: 'https://api.groq.com/openai/v1',
		needsBaseUrl: false,
		authKind: 'api_key',
		capabilities: ['chat', 'tools', 'fast'],
		instructionsKey: 'app.runtime.instructions.groq',
	},
	{
		id: 'gemini',
		displayName: 'Gemini',
		group: 'api',
		Icon: Sparkles,
		providerType: 'api',
		apiFormat: 'openai_compatible',
		defaultBaseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai/',
		needsBaseUrl: false,
		authKind: 'api_key',
		capabilities: ['chat', 'tools', 'vision', 'reasoning'],
		instructionsKey: 'app.runtime.instructions.gemini',
	},
	{
		id: 'azure_openai',
		displayName: 'Azure OpenAI',
		group: 'api',
		Icon: Cloud,
		providerType: 'api',
		apiFormat: 'azure_openai',
		defaultBaseUrl: null,
		needsBaseUrl: true,
		authKind: 'api_key',
		capabilities: ['chat', 'tools', 'json', 'enterprise'],
		instructionsKey: 'app.runtime.instructions.azure_openai',
	},
	{
		id: 'openai_compatible',
		displayName: 'Custom OpenAI-compatible',
		group: 'api',
		Icon: PlugZap,
		providerType: 'api',
		apiFormat: 'openai_compatible',
		defaultBaseUrl: null,
		needsBaseUrl: true,
		authKind: 'api_key',
		capabilities: ['chat'],
		instructionsKey: 'app.runtime.instructions.openai_compatible',
	},
] as const;

/** Catalog provider ids in display order (the fixed card list). */
export const RUNTIME_SETUP_PROVIDER_IDS = PROVIDER_CATALOG.map((entry) => entry.id);

export type RuntimeSetupProviderId = string;

const CATALOG_BY_ID = new Map(PROVIDER_CATALOG.map((entry) => [entry.id, entry]));

/** Lookup a catalog entry by provider id (undefined for an unknown id). */
export function catalogEntry(id: string): ProviderCatalogEntry | undefined {
	return CATALOG_BY_ID.get(id);
}

export type RuntimeSetupState =
	| 'not_configured'
	| 'configured'
	| 'available'
	| 'executable'
	| 'blocked';

/** A provider as shown in the UI: the live runtime status record (may be absent when the
 *  backend has no account for it) left-joined with its configuration record. */
export type MergedProvider = {
	id: string;
	displayName: string;
	kind: string;
	status: RuntimeProvider | null;
	config: RuntimeProviderConfiguration | null;
};

const API_KINDS = new Set(['api', 'gateway']);
const FAILED_HEALTH = new Set(['failed', 'error', 'offline', 'unreachable', 'down']);

function findById<T extends { id: string }>(
	items: readonly T[] | null | undefined,
	id: string,
): T | null {
	return items?.find((item) => item.id === id) ?? null;
}

/** Left-join runtime status + configuration by provider id, in the fixed catalog order. */
export function mergeProviders(
	status: readonly RuntimeProvider[] | null | undefined,
	config: readonly RuntimeProviderConfiguration[] | null | undefined,
): MergedProvider[] {
	return PROVIDER_CATALOG.map((entry) => {
		const statusRecord = findById(status, entry.id);
		const configRecord = findById(config, entry.id);
		return {
			id: entry.id,
			displayName: statusRecord?.displayName ?? configRecord?.displayName ?? entry.displayName,
			kind: statusRecord?.kind ?? configRecord?.kind ?? entry.group,
			status: statusRecord,
			config: configRecord,
		};
	});
}

/**
 * Collapse the backend readiness booleans into one of five user-facing states.
 * Evaluated top-down, first match wins.
 *
 * Invariant: `available` is ranked ABOVE `blocked` on purpose — when a provider
 * is currently reachable/healthy, a stale `lastError` from a prior probe must not
 * downgrade it to "blocked".
 */
export function deriveRuntimeState(provider: MergedProvider): RuntimeSetupState {
	const { status, config } = provider;
	const configured = status ? status.configured : config?.status === 'configured';

	if (!configured) return 'not_configured';
	if (!status) return 'configured';
	if (status.executable) return 'executable';
	if (status.available) return 'available';
	if ((provider.kind === 'cli' || provider.kind === 'local') && status.detected === false)
		return 'blocked';
	if ((status.healthStatus && FAILED_HEALTH.has(status.healthStatus)) || status.lastError?.trim()) {
		return 'blocked';
	}
	return 'configured';
}

export type RuntimeSetupAction = 'setup' | 'validate' | 'use';

/**
 * Single primary CTA per card: `setup` while nothing is configured yet, `validate`
 * while configuration exists but execution is still blocked, `use` once executable.
 */
export function deriveRuntimeAction(state: RuntimeSetupState): RuntimeSetupAction {
	if (state === 'not_configured') return 'setup';
	if (state === 'executable') return 'use';
	return 'validate';
}

export type RuntimeReadinessFactId =
	| 'installed'
	| 'authenticated'
	| 'canRunPrompt'
	| 'canEditWorkspace';

export type RuntimeReadinessFact = {
	id: RuntimeReadinessFactId;
	labelKey: string;
	fallback: string;
	/** `null` when the backend has no status record yet (fact unknown, not false). */
	value: boolean | null;
};

/**
 * The four readiness facts every card answers explicitly — installed, authenticated,
 * can run prompt, can edit workspace — so setup is actionable instead of passive.
 */
export function readinessFacts(provider: MergedProvider): RuntimeReadinessFact[] {
	const status = provider.status;
	const fact = (flag: boolean | undefined): boolean | null => (status ? Boolean(flag) : null);
	return [
		{
			id: 'installed',
			labelKey: 'app.runtime.fact.installed',
			fallback: 'installed',
			value: fact(status?.installed),
		},
		{
			id: 'authenticated',
			labelKey: 'app.runtime.fact.authenticated',
			fallback: 'authenticated',
			value: fact(status?.authenticated),
		},
		{
			id: 'canRunPrompt',
			labelKey: 'app.runtime.fact.canRunPrompt',
			fallback: 'runs prompts',
			value: fact(status?.canRunPrompt),
		},
		{
			id: 'canEditWorkspace',
			labelKey: 'app.runtime.fact.canEditWorkspace',
			fallback: 'edits workspace',
			value: fact(status?.canEditWorkspace),
		},
	];
}

export const STATE_META: Record<
	RuntimeSetupState,
	{ tone: 'ok' | 'warn' | 'danger' | 'info'; Icon: IconComponent; labelKey: string }
> = {
	not_configured: { tone: 'info', Icon: CircleDashed, labelKey: 'app.runtime.state.notConfigured' },
	configured: { tone: 'info', Icon: Clock, labelKey: 'app.runtime.state.configured' },
	available: { tone: 'warn', Icon: AlertTriangle, labelKey: 'app.runtime.state.available' },
	executable: { tone: 'ok', Icon: CheckCircle2, labelKey: 'app.runtime.state.executable' },
	blocked: { tone: 'danger', Icon: XCircle, labelKey: 'app.runtime.state.blocked' },
};

/** Provider glyph, resolved from the catalog (falls back to a neutral plug for unknown ids). */
export const PROVIDER_ICON: Record<string, IconComponent> = Object.fromEntries(
	PROVIDER_CATALOG.map((entry) => [entry.id, entry.Icon]),
);

/** Human-readable labels for the raw provider `kind` enum (resolved via t()); the raw
 *  value is kept as fallback so an uncatalogued kind still renders something. */
export const KIND_LABEL: Record<string, { labelKey: string; fallback: string }> = {
	cli: { labelKey: 'app.runtime.kind.cli', fallback: 'Local CLI' },
	api: { labelKey: 'app.runtime.kind.api', fallback: 'API key' },
	gateway: { labelKey: 'app.runtime.kind.gateway', fallback: 'API gateway' },
	local: { labelKey: 'app.runtime.kind.local', fallback: 'Local daemon' },
	manual: { labelKey: 'app.runtime.kind.manual', fallback: 'Manual operator' },
};

/** Catalog keys for the per-provider "how to configure" copy (resolved via t()). */
export const INSTRUCTIONS_KEY: Record<string, string> = Object.fromEntries(
	PROVIDER_CATALOG.map((entry) => [entry.id, entry.instructionsKey]),
);

/** Ids of API/gateway providers whose health is stored (not live on GET) and so
 *  must be actively re-probed when the user asks to refresh health. */
export function apiProviderIdsNeedingProbe(providers: readonly MergedProvider[]): string[] {
	return providers
		.filter((provider) => API_KINDS.has(provider.kind))
		.map((provider) => provider.id);
}
