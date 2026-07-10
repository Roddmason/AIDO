/**
 * Joins a catalog provider with its live control-plane records into the view-model each card renders.
 * Reads the provider account (credential state, base URL, health, enablement), the discovered model
 * catalog (model count and whether pricing is known) and the role policies (which roles route to this
 * provider), so the card can state status, cost knowledge and "use for roles" without extra queries.
 * Also owns the rule that turns "use this provider for this role" into the role's preferred candidate
 * list, so the card and the wizard share one definition of cost knowledge and role routing.
 * @author Rodrigo Mason
 */

import type {
	ModelGatewayModel,
	ModelGatewayProviderAccount,
	ModelGatewayRolePolicy,
} from '../../api/types';
import type { ProviderCatalogEntry } from './runtimeSetup';

/** Whether the per-token cost of a provider's models is known, free, unknown, or has no models yet. */
export type CostKnowledge = 'known' | 'free' | 'unknown' | 'none';

/** Presentation of each cost-knowledge state, shared by the provider card and the wizard. */
export const COST_META: Record<
	CostKnowledge,
	{ tone: 'ok' | 'warn' | 'info'; labelKey: string; fallback: string }
> = {
	known: { tone: 'ok', labelKey: 'app.providers.cost.known', fallback: 'cost known' },
	free: { tone: 'ok', labelKey: 'app.providers.cost.free', fallback: 'free tier' },
	unknown: { tone: 'warn', labelKey: 'app.providers.cost.unknown', fallback: 'cost unknown' },
	none: { tone: 'info', labelKey: 'app.providers.cost.none', fallback: 'no models yet' },
};

/** The modern setup facts a card shows on top of the runtime-diagnostic readiness. */
export type ProviderSetupInfo = {
	account: ModelGatewayProviderAccount | null;
	enabled: boolean;
	credentialStatus: string;
	hasCredential: boolean;
	baseUrl: string;
	healthStatus: string;
	modelCount: number;
	cost: CostKnowledge;
	roles: string[];
};

/** One routing candidate of a role policy; extra routing keys (effort, requiresApproval) may ride along. */
export type RolePolicyCandidate = { provider?: string | null; model?: string | null };

function candidateLists(policy: ModelGatewayRolePolicy): RolePolicyCandidate[][] {
	const record = policy as unknown as Record<string, unknown>;
	return (['preferred', 'fallback', 'escalation'] as const).map((key) => {
		const value = record[key];
		return Array.isArray(value) ? (value as RolePolicyCandidate[]) : [];
	});
}

/** Roles whose preferred/fallback/escalation candidates route to this provider id. */
function rolesForProvider(
	providerId: string,
	policies: readonly ModelGatewayRolePolicy[],
): string[] {
	const roles: string[] = [];
	for (const policy of policies) {
		const role = (policy as unknown as { role?: string }).role;
		if (!role) continue;
		const routes = candidateLists(policy)
			.flat()
			.some((candidate) => candidate?.provider === providerId);
		if (routes) roles.push(role);
	}
	return roles;
}

/** The role's preferred candidates, in router order (first match wins). */
function preferredCandidates(policy: ModelGatewayRolePolicy): RolePolicyCandidate[] {
	const value = (policy as unknown as Record<string, unknown>).preferred;
	return Array.isArray(value) ? (value as RolePolicyCandidate[]) : [];
}

/** Whether the role already routes to this provider as a preferred candidate. */
export function rolePrefersProvider(policy: ModelGatewayRolePolicy, providerId: string): boolean {
	return preferredCandidates(policy).some((candidate) => candidate?.provider === providerId);
}

/**
 * The role's preferred list after the operator assigns (or unassigns) this provider.
 * Assigning puts `{provider, model}` first — the router reads `preferred` in order, so a provider the
 * operator just set up leads for the roles they picked; unassigning drops every candidate of this
 * provider. Candidates of other providers are carried through untouched so their extra routing keys
 * (effort, requiresApproval) survive the write.
 */
export function nextPreferredCandidates(
	policy: ModelGatewayRolePolicy,
	providerId: string,
	model: string,
	assign: boolean,
): RolePolicyCandidate[] {
	const others = preferredCandidates(policy).filter(
		(candidate) => candidate?.provider !== providerId,
	);
	return assign ? [{ provider: providerId, model }, ...others] : others;
}

/**
 * Whether applying the operator's choice would actually change the role's preferred list.
 * A role that already routes to this provider with this model is left alone — finishing the wizard
 * for an already-configured provider must not silently re-prioritise roles nobody touched.
 */
export function rolePreferredNeedsUpdate(
	policy: ModelGatewayRolePolicy,
	providerId: string,
	model: string,
	assign: boolean,
): boolean {
	const existing = preferredCandidates(policy).find(
		(candidate) => candidate?.provider === providerId,
	);
	if (!assign) return existing !== undefined;
	return existing === undefined || existing.model !== model;
}

/** Cost knowledge for a provider derived from its discovered models' pricing/free-tier flags. */
export function costForModels(models: readonly ModelGatewayModel[]): CostKnowledge {
	if (models.length === 0) return 'none';
	const priced = models.some(
		(model) => model.inputPricePerMtok != null || model.outputPricePerMtok != null,
	);
	if (priced) return 'known';
	if (models.every((model) => model.freeTier)) return 'free';
	return 'unknown';
}

/** Build the card view-model for one catalog provider from the live control-plane records. */
export function deriveProviderSetup(
	entry: ProviderCatalogEntry,
	account: ModelGatewayProviderAccount | null,
	models: readonly ModelGatewayModel[],
	rolePolicies: readonly ModelGatewayRolePolicy[],
): ProviderSetupInfo {
	const providerModels = models.filter((model) => model.providerId === entry.id);
	const credentialStatus = String(account?.credentialStatus ?? 'missing');
	return {
		account,
		enabled: Boolean(account?.enabled),
		credentialStatus,
		hasCredential: credentialStatus === 'configured' || credentialStatus === 'unverified',
		baseUrl: String(account?.baseUrl || entry.defaultBaseUrl || ''),
		healthStatus: String(account?.healthStatus ?? 'unknown'),
		modelCount: providerModels.length,
		cost: costForModels(providerModels),
		roles: rolesForProvider(entry.id, rolePolicies),
	};
}
