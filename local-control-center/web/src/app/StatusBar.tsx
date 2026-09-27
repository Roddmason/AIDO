/**
 * Persistent bottom status bar of the shell: control-plane health (API link, runtimes, approvals,
 * QA, cost) on the left, and the global display controls (language, theme, density) on the right.
 *
 * Those controls used to live in the cluttered top header; they were relocated here so the shell body
 * and header stay clean (Codex-style). Health numbers come pre-derived from {@link deriveShellStatus};
 * theme and density are owned by their hooks. Git workspace controls (branch, gitleaks, refresh) live
 * in the composer ({@link GitBranchBar}), next to the task prompt — not in this footer.
 * @author Rodrigo Mason
 */
import { Moon, Rows3, Sun } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { ExecutionResponse } from '../api/generated/openapi';

import type { Overview, Project, RuntimeProviders } from '../api/types';
import { Button, IconButton, StatusDot, Tooltip } from '../components/ui';
import { useDensity } from '../hooks/useDensity';
import { useTheme } from '../hooks/useTheme';
import { deriveRuntimeAlerts } from './runtimeHealth';
import { deriveShellStatus } from './shellStatus';
import { WorkerStatusItem } from './WorkerStatusItem';

type LanguageOption = { code: string; name: string; nativeName: string; enabled: boolean };

/**
 * Persistent bottom status bar: API connection, current project, executable runtimes, pending
 * approvals, QA pass ratio and recorded cost, plus the relocated language/theme/density controls.
 */
export function StatusBar({
	overview,
	runtimeProviders,
	selectedProject,
	connected,
	language,
	languages,
	onChangeLanguage,
	onOpenRuntimeHealth,
	token,
	t,
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	selectedProject: Project | null;
	connected: boolean;
	language: string;
	languages: LanguageOption[];
	onChangeLanguage: (code: string) => void;
	onOpenRuntimeHealth: () => void;
	/** Local write token, for the worker Resume action. */
	token: string;
	t: (key: string, fallback?: string) => string;
}) {
	const [execution, setExecution] = useState<ExecutionResponse | null>(null);
	useEffect(() => {
		const observe = (event: Event) =>
			setExecution((event as CustomEvent<ExecutionResponse>).detail);
		window.addEventListener('aido:execution', observe);
		return () => window.removeEventListener('aido:execution', observe);
	}, []);
	const status = deriveShellStatus(overview, runtimeProviders, connected, selectedProject);
	const runtimeAlerts = deriveRuntimeAlerts(runtimeProviders);
	const projectName = status.projectName ?? t('app.global.noProject', 'no project');
	const apiLabel = status.connected
		? t('app.statusBar.apiConnected', 'API connected')
		: t('app.statusBar.polling', 'polling');
	const { theme, toggleTheme } = useTheme();
	const { density, toggleDensity } = useDensity();
	const languageOptions = languages.length
		? languages
		: [
				{ code: 'es', name: 'Spanish', nativeName: 'Espanol', enabled: true },
				{ code: 'en', name: 'English', nativeName: 'English', enabled: true },
			];

	return (
		<footer
			className="status-bar"
			role="contentinfo"
			aria-label={t('app.global.globalStatus', 'Global status')}
		>
			{/* Compact labels keep the bar on one line at 1280px: the short word is what is seen, the full
			    phrase is the tooltip and the screen-reader text (sr-only), so nothing is lost. */}
			<span className="status-bar-item" title={apiLabel}>
				<StatusDot tone={status.connected ? 'ok' : 'warn'} />
				<span aria-hidden="true">
					{/* Acronym, identical in every language (like the AIDO Studio wordmark). */}
					{status.connected ? 'API' : apiLabel}
				</span>
				<span className="sr-only">{apiLabel}</span>
			</span>
			<span className="status-bar-item" title={String(selectedProject?.path ?? '')}>
				{/* The name alone identifies the project (the sidebar says it is one); the word stays for SR. */}
				<span className="sr-only">{t('app.statusBar.project', 'Project')}</span>
				<strong className="status-bar-project">{projectName}</strong>
			</span>
			<a
				className="status-bar-item status-bar-item--minor status-bar-item--wide settings-console-link"
				href="#settings"
				title={execution ? `${execution.executionId} · ${execution.reason}` : undefined}
			>
				{t('app.operations.title', 'Operations')}
				{execution ? ` · ${execution.status}` : ''}
			</a>
			<WorkerStatusItem token={token} t={t} />
			<span
				className="status-bar-item"
				title={`${status.executableRuntimes} ${t('app.statusBar.executableRuntimes', 'executable runtimes')}`}
			>
				<StatusDot tone={status.executableRuntimes && !runtimeAlerts.length ? 'ok' : 'warn'} />
				{!runtimeAlerts.length && status.executableRuntimes === 0 ? (
					/* Nothing can run: the count itself is the way to set runtimes up (it used to be followed
					   by a separate "Set up runtimes" link, a second slot for the same action). */
					<a
						className="settings-console-link"
						href="#settings-runtime"
						title={t('app.statusBar.configureRuntimes', 'Set up runtimes')}
					>
						<span className="tnum">{status.executableRuntimes}</span>{' '}
						<span aria-hidden="true">{t('app.statusBar.runtimes', 'runtimes')}</span>
						<span className="sr-only">
							{t('app.statusBar.executableRuntimes', 'executable runtimes')}:{' '}
							{t('app.statusBar.configureRuntimes', 'Set up runtimes')}
						</span>
					</a>
				) : (
					<>
						<span className="tnum">{status.executableRuntimes}</span>{' '}
						<span aria-hidden="true">{t('app.statusBar.runtimes', 'runtimes')}</span>
						<span className="sr-only">
							{t('app.statusBar.executableRuntimes', 'executable runtimes')}
						</span>
					</>
				)}
				{runtimeAlerts.length ? (
					<Button className="settings-console-link" onClick={onOpenRuntimeHealth}>
						{`${runtimeAlerts.length} `}
						{t('app.runtime.health.needAttention', 'need attention')}
					</Button>
				) : null}
			</span>
			<span className="status-bar-item">
				<StatusDot tone={status.activeProviders ? 'ok' : 'warn'} />
				<span className="tnum">{status.activeProviders}</span>{' '}
				{t('app.statusBar.activeProviders', 'active providers')}
			</span>
			<span className="status-bar-item">
				<StatusDot tone={status.pendingApprovals ? 'warn' : 'ok'} />
				<span className="tnum">{status.pendingApprovals}</span>{' '}
				{t('app.statusBar.approvals', 'approvals')}
			</span>
			<span className="status-bar-item status-bar-item--minor">
				<StatusDot tone={status.qaBlocking ? 'danger' : 'ok'} />
				QA{' '}
				<span className="tnum">
					{status.qaPassed}/{status.qaTotal}
				</span>
			</span>
			<span
				className="status-bar-item status-bar-item--minor"
				title={
					status.recordedCost === null ? t('app.statusBar.unavailable', 'unavailable') : undefined
				}
			>
				<span className="status-bar-label">{t('app.statusBar.cost', 'Cost')}</span>
				{status.recordedCost === null ? (
					<>
						{/* An unknown cost reads as an em dash; the word stays in the tooltip and for SR. */}
						<strong aria-hidden="true">—</strong>
						<span className="sr-only">{t('app.statusBar.unavailable', 'unavailable')}</span>
					</>
				) : (
					<strong>{`$${status.recordedCost.toFixed(2)}`}</strong>
				)}
			</span>
			<div className="status-bar-controls">
				{/* biome-ignore lint/a11y/useSemanticElements: swapping to <fieldset> regresses layout — fieldset's UA margin-inline:2px and min-inline-size:min-content are not reset by .language-switch (a class shared only by this element) and inline-flex relies on content sizing; role="group"+aria-label keeps the labeled grouping semantics intact */}
				<div
					className="language-switch"
					role="group"
					aria-label={t('app.global.languageControl', 'Language control')}
				>
					{languageOptions.map((item) => (
						<Button
							key={item.code}
							aria-pressed={language === item.code}
							onClick={() => onChangeLanguage(item.code)}
						>
							{item.code.toUpperCase()}
						</Button>
					))}
				</div>
				<Tooltip label={t('app.global.toggleTheme', 'Toggle light and dark theme')}>
					<IconButton
						aria-label={t('app.global.toggleTheme', 'Toggle light and dark theme')}
						aria-pressed={theme === 'light'}
						onClick={toggleTheme}
					>
						{theme === 'light' ? (
							<Moon aria-hidden="true" size={16} />
						) : (
							<Sun aria-hidden="true" size={16} />
						)}
					</IconButton>
				</Tooltip>
				<Tooltip label={t('app.global.toggleDensity', 'Toggle compact density')}>
					<IconButton
						aria-label={t('app.global.toggleDensity', 'Toggle compact density')}
						aria-pressed={density === 'compact'}
						onClick={toggleDensity}
					>
						<Rows3 aria-hidden="true" size={16} />
					</IconButton>
				</Tooltip>
			</div>
		</footer>
	);
}
