/**
 * Git workspace controls rendered inside the chat composer (Codex-style), not in the footer.
 *
 * Owns the per-project git state — current/branch list, dirty/clean status and the last gitleaks
 * scan — and exposes the interactive controls: branch checkout, branch creation, gitleaks scan and a
 * manual refresh. Lives next to the task prompt because choosing the working branch is part of
 * describing the work, not global chrome. All writes go through the token-guarded git API.
 * @author Rodrigo Mason
 */
import { GitBranch, Plus, RefreshCw, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import {
	checkoutProjectGitBranch,
	createProjectGitBranch,
	getProjectGitBranches,
	getProjectGitStatus,
	type ProjectGitBranchesResponse,
	type ProjectGitGitleaksScanResponse,
	type ProjectGitStatusResponse,
	scanProjectGitleaks,
} from '../../api/client';
import type { Project } from '../../api/types';
import { StatusDot } from '../../components/primitives';
import { IconButton, Tooltip, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';

/**
 * Interactive git workspace bar for the composer: branch picker + create, dirty/gitleaks status and
 * refresh. `onRefresh` re-pulls the overview after a successful mutation so the rest of the shell
 * stays in sync.
 */
export function GitBranchBar({
	selectedProject,
	token,
	onRefresh,
}: {
	selectedProject: Project | null;
	token: string;
	onRefresh: () => void;
}) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [gitStatus, setGitStatus] = useState<ProjectGitStatusResponse | null>(null);
	const [gitBranches, setGitBranches] = useState<ProjectGitBranchesResponse | null>(null);
	const [gitError, setGitError] = useState('');
	const [gitBusy, setGitBusy] = useState(false);
	const [branchName, setBranchName] = useState('');
	const [gitleaks, setGitleaks] = useState<ProjectGitGitleaksScanResponse | null>(null);
	const [gitleaksBusy, setGitleaksBusy] = useState(false);
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
	const gitleaksLabel =
		gitleaks?.gitleaks.status ?? t('app.statusBar.git.gitleaksNotRun', 'not run');

	return (
		<div
			className="composer-git-bar"
			role="group"
			aria-label={t('app.composer.gitControls', 'Git workspace')}
		>
			<span className="composer-git-item" title={gitError || gitStatus?.reason || ''}>
				<StatusDot
					tone={
						gitError || gitStatus?.status === 'failed'
							? 'danger'
							: gitStatus?.status === 'configuration_required'
								? 'warn'
								: 'ok'
					}
				/>
				<GitBranch aria-hidden="true" size={13} />
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
			<span className="composer-git-item" title={gitStatus?.porcelain.join('\n') ?? ''}>
				<StatusDot tone={dirty ? 'warn' : 'ok'} />
				{dirty ? t('app.statusBar.git.dirty', 'dirty') : t('app.statusBar.git.clean', 'clean')}
			</span>
			<span className="composer-git-item" title={gitleaks?.reason ?? ''}>
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
		</div>
	);
}
