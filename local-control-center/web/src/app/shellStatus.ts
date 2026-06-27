/**
 * Derives the shell's aggregate status from raw control-plane data: runtime
 * readiness, pending approvals, QA verdicts and summed cost. The single place
 * that computes these figures so the StatusBar (and tests) share one definition.
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
