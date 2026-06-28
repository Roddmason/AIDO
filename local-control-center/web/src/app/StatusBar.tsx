/**
 * Persistent bottom status bar of the shell: control-plane health (API link, runtimes, approvals,
 * QA, cost) on the left, and the global display controls (language, theme, density) on the right.
 *
 * Those controls used to live in the cluttered top header; they were relocated here so the shell body
 * and header stay clean (Codex-style). Health numbers come pre-derived from {@link deriveShellStatus};
 * theme and density are owned by their hooks.
 * @author Rodrigo Mason
 */
import { GitBranch, Plus, RefreshCw, ShieldCheck, Moon, Rows3, Sun } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
	checkoutProjectGitBranch,
	createProjectGitBranch,
	getProjectGitBranches,
	getProjectGitStatus,
	scanProjectGitleaks,
	type ProjectGitBranchesResponse,
	type ProjectGitGitleaksScanResponse,
	type ProjectGitStatusResponse,
} from '../api/client';
import type { Overview, Project, RuntimeProviders } from '../api/types';
import { StatusDot } from '../components/primitives';
import { Button, IconButton, Tooltip, useToast } from '../components/ui';
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
	token,
	onRefresh,
	language,
	languages,
	onChangeLanguage,
	t,
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	selectedProject: Project | null;
	connected: boolean;
	token: string;
	onRefresh: () => void;
	language: string;
	languages: LanguageOption[];
	onChangeLanguage: (code: string) => void;
	t: (key: string, fallback?: string) => string;
}) {
	const status = deriveShellStatus(overview, runtimeProviders, connected, selectedProject);
	const projectName = status.projectName ?? t('app.global.noProject', 'no project');
	const { theme, toggleTheme } = useTheme();
	const { density, toggleDensity } = useDensity();
	const { notify } = useToast();
	const [gitStatus, setGitStatus] = useState<ProjectGitStatusResponse | null>(null);
	const [gitBranches, setGitBranches] = useState<ProjectGitBranchesResponse | null>(null);
	const [gitError, setGitError] = useState('');
	const [gitBusy, setGitBusy] = useState(false);
	const [branchName, setBranchName] = useState('');
	const [gitleaks, setGitleaks] = useState<ProjectGitGitleaksScanResponse | null>(null);
	const [gitleaksBusy, setGitleaksBusy] = useState(false);
	const languageOptions = languages.length
		? languages
		: [
				{ code: 'es', name: 'Spanish', nativeName: 'Espanol', enabled: true },
				{ code: 'en', name: 'English', nativeName: 'English', enabled: true },
			];
	const projectId = selectedProject?.id ?? '';

	const refreshGit = useCallback(
		async (signal?: AbortSignal) => {
			if (!projectId) {
				setGitStatus(null);
				setGitBranches(null);
				setGitError('');
				return;
			}
			try {
				const [nextStatus, nextBranches] = await Promise.all([
					getProjectGitStatus(projectId, signal),
					getProjectGitBranches(projectId, signal),
				]);
				if (signal?.aborted) return;
				setGitStatus(nextStatus);
				setGitBranches(nextBranches);
				setGitError('');
			} catch (error) {
				if (signal?.aborted) return;
				setGitError(error instanceof Error ? error.message : 'Git status unavailable.');
			}
		},
		[projectId],
	);

	useEffect(() => {
		const controller = new AbortController();
		void refreshGit(controller.signal);
		setGitleaks(null);
		setBranchName('');
		return () => controller.abort();
	}, [refreshGit]);

	const currentBranch = gitStatus?.currentBranch || gitBranches?.currentBranch || '';
	const branchOptions = useMemo(() => {
		const branches = new Set<string>();
		if (currentBranch) branches.add(currentBranch);
		for (const branch of gitBranches?.localBranches ?? []) branches.add(branch);
		return [...branches].filter(Boolean).sort((left, right) => left.localeCompare(right));
	}, [currentBranch, gitBranches?.localBranches]);

	const checkoutBranch = async (nextBranch: string) => {
		if (!projectId || !nextBranch || nextBranch === currentBranch || gitBusy) return;
		setGitBusy(true);
		try {
			const result = await checkoutProjectGitBranch(token, projectId, { branch: nextBranch });
			if (result.status === 'completed') {
				notify({ title: t('app.statusBar.git.checkoutDone', 'Branch checked out'), tone: 'ok' });
			} else {
				notify({
					title: t('app.statusBar.git.checkoutBlocked', 'Checkout blocked'),
					body: result.reason,
					tone: result.status === 'blocked' ? 'warn' : 'danger',
				});
			}
			await refreshGit();
			onRefresh();
		} catch (error) {
			notify({
				title: t('app.statusBar.git.checkoutFailed', 'Checkout failed'),
				body: error instanceof Error ? error.message : undefined,
				tone: 'danger',
			});
		} finally {
			setGitBusy(false);
		}
	};

	const createBranch = async () => {
		const name = branchName.trim();
		if (!projectId || !name || gitBusy) return;
		setGitBusy(true);
		try {
			const result = await createProjectGitBranch(token, projectId, { name });
			if (result.status === 'completed') {
				setBranchName('');
				notify({ title: t('app.statusBar.git.branchCreated', 'Branch created'), tone: 'ok' });
			} else {
				notify({
					title: t('app.statusBar.git.branchBlocked', 'Branch not created'),
					body: result.reason,
					tone: result.status === 'blocked' ? 'warn' : 'danger',
				});
			}
			await refreshGit();
			onRefresh();
		} catch (error) {
			notify({
				title: t('app.statusBar.git.branchFailed', 'Branch creation failed'),
				body: error instanceof Error ? error.message : undefined,
				tone: 'danger',
			});
		} finally {
			setGitBusy(false);
		}
	};

	const runGitleaks = async () => {
		if (!projectId || gitleaksBusy) return;
		setGitleaksBusy(true);
		try {
			const result = await scanProjectGitleaks(token, projectId);
			setGitleaks(result);
			notify({
				title:
					result.status === 'completed'
						? t('app.statusBar.git.gitleaksPassed', 'Gitleaks passed')
						: t('app.statusBar.git.gitleaksBlocked', 'Gitleaks blocked delivery'),
				body: result.reason,
				tone:
					result.status === 'completed'
						? 'ok'
						: result.status === 'configuration_required'
							? 'warn'
							: 'danger',
			});
			await refreshGit();
			onRefresh();
		} catch (error) {
			notify({
				title: t('app.statusBar.git.gitleaksFailed', 'Gitleaks scan failed'),
				body: error instanceof Error ? error.message : undefined,
				tone: 'danger',
			});
		} finally {
			setGitleaksBusy(false);
		}
	};

	const dirty = gitStatus?.dirty === true;
	const gitReady = gitStatus?.status === 'completed' && gitBranches?.status === 'completed';
	const gitleaksLabel = gitleaks?.gitleaks.status ?? t('app.statusBar.git.gitleaksNotRun', 'not run');

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
			<span className="status-bar-item status-bar-git" title={gitError || gitStatus?.reason || ''}>
				<StatusDot
					tone={
						gitError || gitStatus?.status === 'failed'
							? 'danger'
							: gitStatus?.status === 'configuration_required'
								? 'warn'
								: 'ok'
					}
				/>
				<GitBranch aria-hidden="true" size={14} />
				<select
					className="status-branch-select"
					aria-label={t('app.statusBar.git.branchSelect', 'Git branch')}
					value={currentBranch}
					disabled={!selectedProject || !gitReady || gitBusy}
					onChange={(event) => void checkoutBranch(event.target.value)}
				>
					{branchOptions.length ? null : (
						<option value="">{t('app.statusBar.git.branchUnavailable', 'not detected')}</option>
					)}
					{branchOptions.map((branch) => (
						<option key={branch} value={branch}>
							{branch}
						</option>
					))}
				</select>
			</span>
			<form
				className="status-branch-create"
				onSubmit={(event) => {
					event.preventDefault();
					void createBranch();
				}}
			>
				<input
					value={branchName}
					disabled={!selectedProject || gitBusy}
					placeholder={t('app.statusBar.git.branchPlaceholder', 'new branch')}
					aria-label={t('app.statusBar.git.branchName', 'New branch name')}
					onChange={(event) => setBranchName(event.target.value)}
				/>
				<Tooltip label={t('app.statusBar.git.createBranch', 'Create branch')}>
					<IconButton
						type="submit"
						aria-label={t('app.statusBar.git.createBranch', 'Create branch')}
						disabled={!selectedProject || !branchName.trim()}
						loading={gitBusy}
					>
						<Plus aria-hidden="true" size={15} />
					</IconButton>
				</Tooltip>
			</form>
			<span className="status-bar-item" title={gitStatus?.porcelain.join('\n') ?? ''}>
				<StatusDot tone={dirty ? 'warn' : 'ok'} />
				{dirty
					? t('app.statusBar.git.dirty', 'dirty')
					: t('app.statusBar.git.clean', 'clean')}
			</span>
			<span className="status-bar-item" title={gitleaks?.reason ?? ''}>
				<StatusDot
					tone={
						gitleaks?.status === 'completed'
							? 'ok'
							: gitleaks?.status === 'blocked' || gitleaks?.status === 'failed'
								? 'danger'
								: 'warn'
					}
				/>
				gitleaks {gitleaksLabel}
				<Tooltip label={t('app.statusBar.git.runGitleaks', 'Run gitleaks scan')}>
					<IconButton
						aria-label={t('app.statusBar.git.runGitleaks', 'Run gitleaks scan')}
						disabled={!selectedProject}
						loading={gitleaksBusy}
						onClick={() => void runGitleaks()}
					>
						<ShieldCheck aria-hidden="true" size={15} />
					</IconButton>
				</Tooltip>
				<Tooltip label={t('app.statusBar.git.refresh', 'Refresh Git status')}>
					<IconButton
						aria-label={t('app.statusBar.git.refresh', 'Refresh Git status')}
						disabled={!selectedProject || gitBusy}
						onClick={() => void refreshGit()}
					>
						<RefreshCw aria-hidden="true" size={14} />
					</IconButton>
				</Tooltip>
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
