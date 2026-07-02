/**
 * Shared domain logic for the runtime-setup cards: the fixed list of providers to
 * explain, the left-join of live status with configuration, and the rule that collapses
 * the backend readiness booleans into one user-facing state plus its display metadata.
 * UI-agnostic so both the full panel and the compact inspector card render identically.
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
	Cpu,
	Network,
	PlugZap,
	SquareTerminal,
	XCircle,
} from 'lucide-react';
import type { ComponentType } from 'react';

import type { RuntimeProvider, RuntimeProviderConfiguration } from '../../api/types';

type IconComponent = ComponentType<LucideProps>;

/** The exact runtimes this setup surface explains, in the order they are shown.
 *  Driving the cards from this constant (rather than from either API response)
 *  guarantees every requested provider renders — even one the backend has never
 *  catalogued — so the panel can always answer "why can't I execute this?". */
export const RUNTIME_SETUP_PROVIDER_IDS = [
	'codex_cli',
	'claude_code_cli',
	'openhands',
	'swe_agent',
	'ollama',
	'openai_compatible',
	'openrouter',
	'nvidia_nim',
	'anthropic_api',
] as const;

export type RuntimeSetupProviderId = (typeof RUNTIME_SETUP_PROVIDER_IDS)[number];

export type RuntimeSetupState =
	| 'not_configured'
	| 'configured'
	| 'available'
	| 'executable'
	| 'blocked';

/** A provider as shown in the UI: the live status record (may be absent when the
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

/** Left-join status + configuration by provider id, in the fixed display order. */
export function mergeProviders(
	status: readonly RuntimeProvider[] | null | undefined,
	config: readonly RuntimeProviderConfiguration[] | null | undefined,
): MergedProvider[] {
	return RUNTIME_SETUP_PROVIDER_IDS.map((id) => {
		const statusRecord = findById(status, id);
		const configRecord = findById(config, id);
		return {
			id,
			displayName: statusRecord?.displayName ?? configRecord?.displayName ?? id,
			kind: statusRecord?.kind ?? configRecord?.kind ?? 'manual',
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

export const PROVIDER_ICON: Record<RuntimeSetupProviderId, IconComponent> = {
	codex_cli: SquareTerminal,
	claude_code_cli: Bot,
	openhands: SquareTerminal,
	swe_agent: SquareTerminal,
	ollama: Boxes,
	openai_compatible: PlugZap,
	openrouter: Network,
	nvidia_nim: Cpu,
	anthropic_api: Bot,
};

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
export const INSTRUCTIONS_KEY: Record<RuntimeSetupProviderId, string> = {
	codex_cli: 'app.runtime.instructions.codex_cli',
	claude_code_cli: 'app.runtime.instructions.claude_code_cli',
	openhands: 'app.runtime.instructions.openhands',
	swe_agent: 'app.runtime.instructions.swe_agent',
	ollama: 'app.runtime.instructions.ollama',
	openai_compatible: 'app.runtime.instructions.openai_compatible',
	openrouter: 'app.runtime.instructions.openrouter',
	nvidia_nim: 'app.runtime.instructions.nvidia_nim',
	anthropic_api: 'app.runtime.instructions.anthropic_api',
};

/** Ids of API/gateway providers whose health is stored (not live on GET) and so
 *  must be actively re-probed when the user asks to refresh health. */
export function apiProviderIdsNeedingProbe(providers: readonly MergedProvider[]): string[] {
	return providers
		.filter((provider) => API_KINDS.has(provider.kind))
		.map((provider) => provider.id);
}
