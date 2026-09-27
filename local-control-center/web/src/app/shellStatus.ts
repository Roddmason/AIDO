/**
 * Derives the shell's aggregate status from raw control-plane data: runtime
 * readiness, pending approvals, QA verdicts and summed cost. The single place
 * that computes these figures so the StatusBar (and tests) share one definition.
 * @author Rodrigo Mason
 */
import type { Overview, Project, RuntimeProviders } from '../api/types';
import { sumRecordedCostUsd } from '../lib/format';

const BLOCKING_QA_VERDICTS = ['failed', 'blocked', 'security_blocked', 'devops_blocked'];

/** Aggregate health the shell surfaces in the status bar: connection, runtime
 *  readiness, pending approvals, QA verdicts and recorded cost. Pure data — the
 *  StatusBar maps it to localized labels. */
export type ShellStatus = {
	connected: boolean;
	executableRuntimes: number;
	/** Providers a thread's AI team can use: same rule as `activeProviders` of `GET /runtime/team`. */
	activeProviders: number;
	pendingApprovals: number;
	qaPassed: number;
	qaTotal: number;
	qaBlocking: number;
	/** Summed recorded cost in USD, or `null` when no finite amounts exist (never fabricated as 0). */
	recordedCost: number | null;
	/** Raw selected-project name, or `null` when none is selected (StatusBar applies the i18n fallback). */
	projectName: string | null;
};

/** Single source of truth for the status-bar derivation, including the cost sum. */
export function deriveShellStatus(
	overview: Overview,
	runtimeProviders: RuntimeProviders | null,
	connected: boolean,
	selectedProject: Project | null,
): ShellStatus {
	const evidence = overview.evidencePackages;
	return {
		connected,
		executableRuntimes:
			runtimeProviders?.providers.filter((provider) => provider.executable).length ?? 0,
		activeProviders: runtimeProviders?.providers.filter(isActiveTeamProvider).length ?? 0,
		pendingApprovals: overview.actionRequests.filter((item) => item.status === 'pending').length,
		qaPassed: evidence.filter((item) => String(item.qaVerdict ?? '') === 'passed').length,
		qaTotal: evidence.length,
		qaBlocking: evidence.filter((item) =>
			BLOCKING_QA_VERDICTS.includes(String(item.qaVerdict ?? '')),
		).length,
		recordedCost: sumRecordedCostUsd(overview.costUsage),
		projectName: selectedProject?.name ?? null,
	};
}

/** Provider kinds that can serve an AI team role (`runtime_team/facts.py:TEAM_RUNTIME_KINDS`). */
const TEAM_RUNTIME_KINDS = new Set(['cli', 'api', 'gateway', 'local']);

/**
 * Active for the AI team: the operator switch is on, the runtime policy allows it and it is a kind a
 * role can use (the manual operator never counts). Mirrors `load_runtime_facts` + the policy veto, so
 * the status bar and `GET /api/v1/runtime/team` report the same number.
 */
export function isActiveTeamProvider(provider: {
	enabled?: boolean;
	policyAllowed?: boolean;
	kind: string;
}): boolean {
	return (
		Boolean(provider.enabled) &&
		provider.policyAllowed !== false &&
		TEAM_RUNTIME_KINDS.has(provider.kind)
	);
}
