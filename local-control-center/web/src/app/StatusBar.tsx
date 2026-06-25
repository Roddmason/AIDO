/**
 * Persistent bottom status bar of the shell: control-plane health (API link, runtimes, approvals,
 * QA, cost) on the left, and the global display controls (language, theme, density) on the right.
 *
 * Those controls used to live in the cluttered top header; they were relocated here so the shell body
 * and header stay clean (Codex-style). Health numbers come pre-derived from {@link deriveShellStatus};
 * theme and density are owned by their hooks.
 */
import { Moon, Rows3, Sun } from 'lucide-react';
import type { Overview, Project, RuntimeProviders } from '../api/types';
import { StatusDot } from '../components/primitives';
import { Button, IconButton, Tooltip } from '../components/ui';
import { useDensity } from '../hooks/useDensity';
import { useTheme } from '../hooks/useTheme';
import { deriveShellStatus } from './shellStatus';

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
	t,
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	selectedProject: Project | null;
	connected: boolean;
	language: string;
	languages: LanguageOption[];
	onChangeLanguage: (code: string) => void;
	t: (key: string, fallback?: string) => string;
}) {
	const status = deriveShellStatus(overview, runtimeProviders, connected, selectedProject);
	const projectName = status.projectName ?? t('app.global.noProject', 'no project');
	const { theme, toggleTheme } = useTheme();
	const { density, toggleDensity } = useDensity();
	const languageOptions = languages.length
		? languages
		: [
				{ code: 'es', name: 'Spanish', nativeName: 'Espanol', enabled: true },
				{ code: 'en', name: 'English', nativeName: 'English', enabled: true },
			];

	return (
		<footer className="status-bar" aria-label={t('app.global.globalStatus', 'Global status')}>
			<span className="status-bar-item">
				<StatusDot tone={status.connected ? 'ok' : 'warn'} />
				{status.connected
					? t('app.statusBar.apiConnected', 'API connected')
					: t('app.statusBar.polling', 'polling')}
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
				<strong>
					{status.recordedCost === null
						? t('app.statusBar.unavailable', 'unavailable')
						: `$${status.recordedCost.toFixed(2)}`}
				</strong>
			</span>
			<div className="status-bar-controls">
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
