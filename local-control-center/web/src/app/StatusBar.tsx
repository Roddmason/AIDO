/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import type { Overview, Project, RuntimeProviders } from '../api/types';
import { StatusDot } from '../components/primitives';

function sumRecordedCost(rows: Overview['costUsage']): number | null {
	const amounts = rows.map((row) => Number(row.amountUsd)).filter((amount) => Number.isFinite(amount));
	return amounts.length ? amounts.reduce((sum, amount) => sum + amount, 0) : null;
}

export function StatusBar({
	overview,
	runtimeProviders,
	selectedProject,
	connected,
	language,
	t,
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	selectedProject: Project | null;
	connected: boolean;
	language: string;
	t: (key: string, fallback?: string) => string;
}) {
	const executableRuntimes = runtimeProviders?.providers.filter((provider) => provider.executable).length ?? 0;
	const pendingApprovals = overview.actionRequests.filter((item) => item.status === 'pending').length;
	const evidence = overview.evidencePackages;
	const qaPassed = evidence.filter((item) => String(item.qaVerdict ?? '') === 'passed').length;
	const qaBlocking = evidence.filter((item) => ['failed', 'blocked', 'security_blocked', 'devops_blocked'].includes(String(item.qaVerdict ?? ''))).length;
	const recordedCost = sumRecordedCost(overview.costUsage);
	const projectName = selectedProject?.name ?? t('app.global.noProject', 'no project');

	return (
		<footer className="status-bar" aria-label={t('app.global.globalStatus', 'Global status')}>
			<span className="status-bar-item">
				<StatusDot tone={connected ? 'ok' : 'warn'} />
				{connected ? t('app.statusBar.apiConnected', 'API connected') : t('app.statusBar.polling', 'polling')}
			</span>
			<span className="status-bar-item" title={String(selectedProject?.path ?? '')}>
				<span className="status-bar-label">{t('app.statusBar.project', 'Project')}</span>
				<strong>{projectName}</strong>
			</span>
			<span className="status-bar-item">
				<StatusDot tone={executableRuntimes ? 'ok' : 'warn'} />
				{executableRuntimes} {t('app.statusBar.executableRuntimes', 'executable runtimes')}
			</span>
			<span className="status-bar-item">
				<StatusDot tone={pendingApprovals ? 'warn' : 'ok'} />
				{pendingApprovals} {t('app.statusBar.approvals', 'approvals')}
			</span>
			<span className="status-bar-item">
				<StatusDot tone={qaBlocking ? 'danger' : 'ok'} />
				QA {qaPassed}/{evidence.length}
			</span>
			<span className="status-bar-item">
				<span className="status-bar-label">{t('app.statusBar.cost', 'Cost')}</span>
				<strong>{recordedCost === null ? t('app.statusBar.unavailable', 'unavailable') : `$${recordedCost.toFixed(2)}`}</strong>
			</span>
		</footer>
	);
}
