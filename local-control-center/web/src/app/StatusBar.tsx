/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import type { Overview, Project, RuntimeProviders } from '../api/types';
import { StatusDot } from '../components/primitives';
import { deriveShellStatus } from './shellStatus';

/**
 * Persistent bottom status bar: API connection, current project, executable
 * runtimes, pending approvals, QA pass ratio and recorded cost. Reads its data
 * through {@link deriveShellStatus} (the single source for the cost sum).
 */
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
	const status = deriveShellStatus(overview, runtimeProviders, connected, selectedProject);
	const projectName = status.projectName ?? t('app.global.noProject', 'no project');

	return (
		<footer className="status-bar" aria-label={t('app.global.globalStatus', 'Global status')}>
			<span className="status-bar-item">
				<StatusDot tone={status.connected ? 'ok' : 'warn'} />
				{status.connected ? t('app.statusBar.apiConnected', 'API connected') : t('app.statusBar.polling', 'polling')}
			</span>
			<span className="status-bar-item" title={String(selectedProject?.path ?? '')}>
				<span className="status-bar-label">{t('app.statusBar.project', 'Project')}</span>
				<strong>{projectName}</strong>
			</span>
			<span className="status-bar-item">
				<StatusDot tone={status.executableRuntimes ? 'ok' : 'warn'} />
				{status.executableRuntimes} {t('app.statusBar.executableRuntimes', 'executable runtimes')}
			</span>
			<span className="status-bar-item">
				<StatusDot tone={status.pendingApprovals ? 'warn' : 'ok'} />
				{status.pendingApprovals} {t('app.statusBar.approvals', 'approvals')}
			</span>
			<span className="status-bar-item">
				<StatusDot tone={status.qaBlocking ? 'danger' : 'ok'} />
				QA {status.qaPassed}/{status.qaTotal}
			</span>
			<span className="status-bar-item">
				<span className="status-bar-label">{t('app.statusBar.cost', 'Cost')}</span>
				<strong>{status.recordedCost === null ? t('app.statusBar.unavailable', 'unavailable') : `$${status.recordedCost.toFixed(2)}`}</strong>
			</span>
		</footer>
	);
}
