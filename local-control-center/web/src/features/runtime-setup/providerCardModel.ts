/**
 * Joins a catalog provider with its live control-plane records into the view-model each card renders.
 * Reads the provider account (credential state, base URL, health, enablement), the discovered model
 * catalog (model count and whether pricing is known) and the role policies (which roles route to this
 * provider), so the card can state status, cost knowledge and "use for roles" without extra queries.
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

type RolePolicyCandidate = { provider?: string | null; model?: string | null };

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

/** Cost knowledge for a provider derived from its discovered models' pricing/free-tier flags. */
function costForModels(models: readonly ModelGatewayModel[]): CostKnowledge {
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
