/**
 * Derives, from the live runtime-provider poll, which AIs currently need the operator's attention.
 *
 * A provider surfaces an alert only when the backend classified it with a `blockerType` (it is
 * configured/detected but cannot run — expired credentials, quota, a stopped service). Providers that
 * are healthy or were simply never set up produce nothing, so the shell never nags about unused AIs.
 * The blockerType reuses the remediation vocabulary so the modal renders the same plain-language chip
 * as a blocked loop. Pure data — the StatusBar and the health modal map it to localized copy.
 * @author Rodrigo Mason
 */
import type { RuntimeProviders } from '../api/types';

/** One AI that needs attention, ready to render as a remediation card in the health modal. */
export type RuntimeHealthAlert = {
	providerId: string;
	displayName: string;
	kind: string;
	/** Normalized cause reused from the remediation vocabulary (`runtime_auth_missing`, etc.). */
	blockerType: string;
	/** The provider needs credentials before it can run (re-authenticate, not just reconfigure). */
	needsAuthentication: boolean;
	/** The machine reason the backend reported (already secret-redacted), shown as the "Cause" fact. */
	reason: string;
	/** Extra diagnostic string kept inside the technical disclosure, never in the headline. */
	lastError: string;
	/** Settings section whose deep-link fixes this provider. */
	settingsSection: string;
};

/** Settings section that repairs a provider of the given kind (deep-linked with its providerId). */
function settingsSectionForKind(kind: string): string {
	return kind === 'cli' ? 'providers-cli' : 'credentials';
}

/**
 * Maps the polled runtime providers to the AIs that need attention. Empty when every configured
 * provider is executable (or nothing is configured) — the caller treats an empty list as "all ready".
 */
export function deriveRuntimeAlerts(
	runtimeProviders: RuntimeProviders | null,
): RuntimeHealthAlert[] {
	const providers = runtimeProviders?.providers ?? [];
	const alerts: RuntimeHealthAlert[] = [];
	for (const provider of providers) {
		const blockerType = provider.blockerType;
		if (!blockerType) continue;
		alerts.push({
			providerId: provider.id,
			displayName: provider.displayName,
			kind: provider.kind,
			blockerType,
			needsAuthentication: blockerType === 'runtime_auth_missing',
			reason: provider.reason ?? '',
			lastError: provider.lastError ?? '',
			settingsSection: settingsSectionForKind(provider.kind),
		});
	}
	return alerts;
}
