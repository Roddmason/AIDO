/**
 * Git workspace controls rendered inside the chat composer (Codex-style), not in the footer.
 *
 * Owns the per-project git state and turns the raw backend enum (completed / blocked /
 * configuration_required / failed) into a single explicit render phase so that NO state is ever a
 * dead end — the guiding invariant is "Git must not block without an exit". Every blocking phase
 * surfaces a concrete way forward: `no_git` → Initialize Git, `blocked` → an explicit policy notice
 * (retrying the same blocked command is not offered as the way out), `error` → Retry, detached/empty
 * HEAD → pick or create a branch, gitleaks missing → open setup. All writes go through the
 * token-guarded git API; the diff/patch and remote forms live in portalled Dialogs so they never
 * nest inside the composer <form>.
 * @author Rodrigo Mason
 */
import {
	AlertTriangle,
	Check,
	Cloud,
	Download,
	GitBranch,
	GitCommitHorizontal,
	GitCompare,
	Plus,
	RefreshCw,
	ShieldAlert,
	ShieldCheck,
} from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import {
	addProjectGitRemote,
	checkoutProjectGitBranch,
	createProjectGitBranch,
	getProjectGitBranches,
	getProjectGitDiff,
	getProjectGitStatus,
	initProjectGitRepository,
	type ProjectGitBranchesResponse,
	type ProjectGitDiffResponse,
	type ProjectGitGitleaksScanResponse,
	type ProjectGitStatusResponse,
	scanProjectGitleaks,
	testProjectGitRemote,
} from '../../api/client';
import { requestCompletedOperation } from '../../api/execution-client';
import type { Project } from '../../api/types';
import {
	Button,
	Dialog,
	IconButton,
	StatusDot,
	TextField,
	Tooltip,
	useToast,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';

/** Keeps the composer bar single-line; the full subject stays available in the title tooltip. */
function truncateSubject(subject: string): string {
	return subject.length > 42 ? `${subject.slice(0, 41)}…` : subject;
}

/** Human-readable labels for the raw gitleaks scan status enum (service.py contract). */
const GITLEAKS_STATUS_LABEL: Record<string, { labelKey: string; fallback: string }> = {
	completed: { labelKey: 'app.statusBar.git.gitleaksPassedShort', fallback: 'passed' },
	blocked: { labelKey: 'app.statusBar.git.gitleaksBlockedShort', fallback: 'blocked' },
	failed: { labelKey: 'app.statusBar.git.gitleaksFailedShort', fallback: 'failed' },
	configuration_required: {
		labelKey: 'app.statusBar.git.gitleaksMissing',
		fallback: 'not installed',
	},
};

/** Branches Git already owns as protected lines; creating work directly on them is blocked server-side. */
const PROTECTED_BRANCHES = new Set(['main', 'master']);

/**
 * Explicit render phase for the git bar. `blocked` is intentionally NOT folded into `error`: a
 * policy block is not a transient failure, so re-running the same command via Refresh is not a valid
 * exit — the phase gets its own copy instead. `failed` stays in `error` because there Refresh is a
 * legitimate retry after a transient exception.
 */
type GitPhase = 'loading' | 'error' | 'blocked' | 'no_git' | 'ready';

/**
 * Interactive git workspace bar for the composer: state-driven primary CTA (never a dead end),
 * branch picker + create, dirty/gitleaks status, and the diff/patch + remotes management dialogs.
 * `onRefresh` re-pulls the overview after a successful mutation so the rest of the shell stays in sync.
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
	/** Branch picked in the select but not yet checked out — checkout needs the explicit
	 *  confirm button, so keyboard browsing of the list never triggers a checkout (WCAG 3.2.2). */
	const [pendingBranch, setPendingBranch] = useState('');
	const [gitleaks, setGitleaks] = useState<ProjectGitGitleaksScanResponse | null>(null);
	const [gitleaksBusy, setGitleaksBusy] = useState(false);

	const [changesOpen, setChangesOpen] = useState(false);
	const [diff, setDiff] = useState<ProjectGitDiffResponse | null>(null);
	const [diffBusy, setDiffBusy] = useState(false);
	const [diffError, setDiffError] = useState('');
	const [remotesOpen, setRemotesOpen] = useState(false);
	const [remoteName, setRemoteName] = useState('');
	const [remoteUrl, setRemoteUrl] = useState('');
	const [remoteFormError, setRemoteFormError] = useState('');
	const [remoteBusy, setRemoteBusy] = useState(false);
	const [remoteTestBusy, setRemoteTestBusy] = useState('');
	const [setupOpen, setSetupOpen] = useState(false);
	/** Target branch awaiting a dirty-tree confirmation before checkout (null = no confirm pending). */
	const [checkoutConfirm, setCheckoutConfirm] = useState('');

	const projectId = selectedProject?.id ?? '';
	const selectHelpId = useId();
	const activeProjectRef = useRef(projectId);
	activeProjectRef.current = projectId;

	const refreshGit = useCallback(
		async (signal?: AbortSignal) => {
			if (!projectId) {
				setGitStatus(null);
				setGitBranches(null);
				setGitError('');
				return;
			}
			try {
				if (!signal && token) {
					await requestCompletedOperation(
						'refresh_git_api_v1_projects__project_id__git_refresh_post',
						{
							token,
							pathParams: { project_id: projectId },
						},
					);
				}
				const [nextStatus, nextBranches] = await Promise.all([
					getProjectGitStatus(projectId, signal),
					getProjectGitBranches(projectId, signal),
				]);
				if (signal?.aborted || activeProjectRef.current !== projectId) return;
				setGitStatus(nextStatus);
				setGitBranches(nextBranches);
				setGitError('');
			} catch (error) {
				if (signal?.aborted || activeProjectRef.current !== projectId) return;
				setGitError(error instanceof Error ? error.message : 'Git status unavailable.');
			}
		},
		[projectId, token],
	);

	useEffect(() => {
		const controller = new AbortController();
		void refreshGit(controller.signal);
		setGitleaks(null);
		setBranchName('');
		setPendingBranch('');
		setChangesOpen(false);
		setDiff(null);
		setDiffError('');
		setRemotesOpen(false);
		setRemoteName('');
		setRemoteUrl('');
		setRemoteFormError('');
		setSetupOpen(false);
		setCheckoutConfirm('');
		return () => controller.abort();
	}, [refreshGit]);

	const currentBranch = gitStatus?.currentBranch || gitBranches?.currentBranch || '';
	const branchOptions = useMemo(() => {
		const branches = new Set<string>();
		if (currentBranch && currentBranch !== 'HEAD') branches.add(currentBranch);
		for (const branch of gitBranches?.localBranches ?? []) {
			if (branch && branch !== 'HEAD') branches.add(branch);
		}
		return [...branches].filter(Boolean).sort((left, right) => left.localeCompare(right));
	}, [currentBranch, gitBranches?.localBranches]);

	const lastCommit = gitStatus?.lastCommit?.shortHash ? gitStatus.lastCommit : null;

	const gitPhase: GitPhase = gitError
		? 'error'
		: !gitStatus
			? 'loading'
			: gitStatus.refreshRequired && !gitStatus.snapshotAt
				? 'error'
				: gitStatus.status === 'configuration_required'
					? 'no_git'
					: gitStatus.status === 'blocked'
						? 'blocked'
						: gitStatus.status === 'completed' && gitBranches?.status === 'completed'
							? 'ready'
							: 'error';
	const ready = gitPhase === 'ready';

	useEffect(() => {
		if (gitPhase !== 'ready') setGitleaks(null);
	}, [gitPhase]);

	const gitMissing =
		gitPhase === 'no_git' && /executable was not found/i.test(gitStatus?.reason ?? '');

	const hasCommits = Boolean(lastCommit);
	const branchMissing = ready && !hasCommits;
	const detachedHead = ready && hasCommits && (currentBranch === 'HEAD' || currentBranch === '');
	const unbornBranch = branchMissing && currentBranch !== 'HEAD' ? currentBranch : '';
	const showPicker = ready && hasCommits;
	const onlyProtectedBranch =
		showPicker &&
		!detachedHead &&
		branchOptions.length <= 1 &&
		PROTECTED_BRANCHES.has(currentBranch.toLowerCase());

	const dirty = gitStatus?.dirty === true;
	const changedCount = gitStatus?.changedFiles.length ?? 0;
	const stagedCount = gitStatus?.stagedFiles.length ?? 0;
	const untrackedCount = gitStatus?.untrackedFiles.length ?? 0;
	const fileGroups = useMemo(
		() =>
			[
				{
					key: 'staged',
					heading: t('app.statusBar.git.stagedHeading', 'Staged'),
					files: gitStatus?.stagedFiles ?? [],
				},
				{
					key: 'changed',
					heading: t('app.statusBar.git.changedHeading', 'Changed'),
					files: gitStatus?.changedFiles ?? [],
				},
				{
					key: 'untracked',
					heading: t('app.statusBar.git.untrackedHeading', 'Untracked'),
					files: gitStatus?.untrackedFiles ?? [],
				},
			].filter((group) => group.files.length > 0),
		[gitStatus?.stagedFiles, gitStatus?.changedFiles, gitStatus?.untrackedFiles, t],
	);
	const dirtyBreakdown = [
		changedCount ? `${changedCount} ${t('app.statusBar.git.changedLabel', 'changed')}` : '',
		stagedCount ? `${stagedCount} ${t('app.statusBar.git.stagedLabel', 'staged')}` : '',
		untrackedCount ? `${untrackedCount} ${t('app.statusBar.git.untrackedLabel', 'untracked')}` : '',
	]
		.filter(Boolean)
		.join(' · ');

	const remoteNames = useMemo(
		() => [...new Set((gitStatus?.remotes ?? []).map((remote) => remote.name))].filter(Boolean),
		[gitStatus?.remotes],
	);
	const noRemotes = ready && remoteNames.length === 0;

	const rawGitleaksStatus = gitleaks?.gitleaks.status;
	const gitleaksMissing = rawGitleaksStatus === 'configuration_required';
	const gitleaksLabelDef = rawGitleaksStatus ? GITLEAKS_STATUS_LABEL[rawGitleaksStatus] : null;
	const gitleaksLabel = rawGitleaksStatus
		? gitleaksLabelDef
			? t(gitleaksLabelDef.labelKey, gitleaksLabelDef.fallback)
			: rawGitleaksStatus
		: t('app.statusBar.git.gitleaksNotRun', 'not run');

	const dirtyTone = dirty ? 'warn' : 'ok';
	const gitConnTone =
		gitPhase === 'loading'
			? 'pending'
			: gitPhase === 'error'
				? 'danger'
				: gitPhase === 'blocked' || gitPhase === 'no_git'
					? 'warn'
					: 'ok';
	const gitleaksTone =
		gitleaks?.status === 'completed'
			? 'ok'
			: gitleaks?.status === 'blocked' || gitleaks?.status === 'failed'
				? 'danger'
				: 'ok';

	/**
	 * Runs one token-guarded git write: toggles the busy flag, toasts the completed/blocked outcome (or the
	 * failure), then re-pulls git state and the overview. `onCompleted` runs only on a completed result.
	 */
	const runGitAction = async (
		run: () => Promise<{ status: string; reason: string }>,
		copy: { done: string; blocked: string; failed: string },
		onCompleted?: () => void,
	) => {
		setGitBusy(true);
		try {
			const result = await run();
			if (result.status === 'completed') {
				onCompleted?.();
				notify({ title: copy.done, tone: 'ok' });
			} else {
				notify({
					title: copy.blocked,
					body: result.reason,
					tone: result.status === 'blocked' ? 'warn' : 'danger',
					durationMs: 0,
				});
			}
			await refreshGit();
			onRefresh();
		} catch (error) {
			notify({
				title: copy.failed,
				body: error instanceof Error ? error.message : undefined,
				tone: 'danger',
				durationMs: 0,
			});
		} finally {
			setGitBusy(false);
		}
	};

	const initGit = async () => {
		if (!projectId || gitBusy) return;
		await runGitAction(() => initProjectGitRepository(token, projectId, {}), {
			done: t('app.statusBar.git.initGitDone', 'Git repository initialized'),
			blocked: t('app.statusBar.git.initGitFailed', 'Git init failed'),
			failed: t('app.statusBar.git.initGitFailed', 'Git init failed'),
		});
	};

	const checkoutBranch = async (nextBranch: string, allowDirty: boolean) => {
		if (!projectId || !nextBranch || nextBranch === currentBranch || gitBusy) return;
		setPendingBranch('');
		setCheckoutConfirm('');
		await runGitAction(
			() => checkoutProjectGitBranch(token, projectId, { branch: nextBranch, allowDirty }),
			{
				done: t('app.statusBar.git.checkoutDone', 'Branch checked out'),
				blocked: t('app.statusBar.git.checkoutBlocked', 'Checkout blocked'),
				failed: t('app.statusBar.git.checkoutFailed', 'Checkout failed'),
			},
		);
	};

	const requestCheckout = (nextBranch: string) => {
		if (!nextBranch || nextBranch === currentBranch) return;
		if (dirty) setCheckoutConfirm(nextBranch);
		else void checkoutBranch(nextBranch, false);
	};

	const createBranch = async () => {
		const name = branchName.trim();
		if (!projectId || !name || gitBusy) return;
		await runGitAction(
			() => createProjectGitBranch(token, projectId, { name }),
			{
				done: t('app.statusBar.git.branchCreated', 'Branch created'),
				blocked: t('app.statusBar.git.branchBlocked', 'Branch not created'),
				failed: t('app.statusBar.git.branchFailed', 'Branch creation failed'),
			},
			() => setBranchName(''),
		);
	};

	const runGitleaks = async () => {
		if (!projectId || gitleaksBusy) return;
		setGitleaksBusy(true);
		try {
			const result = await scanProjectGitleaks(token, projectId);
			setGitleaks(result);
			const passed = result.status === 'completed';
			notify({
				title: passed
					? t('app.statusBar.git.gitleaksPassed', 'Gitleaks passed')
					: t('app.statusBar.git.gitleaksBlocked', 'Gitleaks blocked delivery'),
				body: result.reason,
				tone: passed ? 'ok' : result.status === 'configuration_required' ? 'warn' : 'danger',
				durationMs: passed ? 5000 : 0,
			});
			await refreshGit();
			onRefresh();
		} catch (error) {
			notify({
				title: t('app.statusBar.git.gitleaksFailed', 'Gitleaks scan failed'),
				body: error instanceof Error ? error.message : undefined,
				tone: 'danger',
				durationMs: 0,
			});
		} finally {
			setGitleaksBusy(false);
		}
	};

	const loadDiff = useCallback(async () => {
		if (!projectId) return;
		setDiffBusy(true);
		setDiffError('');
		try {
			const result = await getProjectGitDiff(projectId);
			setDiff(result);
		} catch (error) {
			setDiffError(
				error instanceof Error
					? error.message
					: t('app.statusBar.git.diffFailed', 'Could not load the diff'),
			);
		} finally {
			setDiffBusy(false);
		}
	}, [projectId, t]);

	const openChanges = () => {
		setChangesOpen(true);
		void loadDiff();
	};

	const savePatch = () => {
		const text = diff?.diff ?? '';
		if (!text.trim()) {
			notify({
				title: t('app.statusBar.git.savePatchEmpty', 'No changes to export'),
				tone: 'info',
			});
			return;
		}
		const shortHash = lastCommit?.shortHash || 'working';
		const raw = `${selectedProject?.name ?? 'project'}-${currentBranch || 'HEAD'}-${shortHash}`;
		const fileName = `${raw.replace(/[^\w.-]+/g, '-').replace(/^-+|-+$/g, '') || 'patch'}.patch`;
		const url = URL.createObjectURL(new Blob([text], { type: 'text/x-patch' }));
		const anchor = document.createElement('a');
		anchor.href = url;
		anchor.download = fileName;
		document.body.appendChild(anchor);
		anchor.click();
		anchor.remove();
		URL.revokeObjectURL(url);
		notify({ title: t('app.statusBar.git.savePatchDone', 'Patch saved'), tone: 'ok' });
	};

	const addRemote = async () => {
		const name = remoteName.trim();
		const url = remoteUrl.trim();
		if (!projectId || !name || !url || remoteBusy) return;
		setRemoteBusy(true);
		setRemoteFormError('');
		try {
			const result = await addProjectGitRemote(token, projectId, { name, url });
			if (result.status === 'completed') {
				setRemoteName('');
				setRemoteUrl('');
				notify({ title: t('app.statusBar.git.remoteAdded', 'Remote added'), tone: 'ok' });
				await refreshGit();
				onRefresh();
			} else {
				setRemoteFormError(result.reason);
			}
		} catch (error) {
			setRemoteFormError(
				error instanceof Error
					? error.message
					: t('app.statusBar.git.remoteAddFailed', 'Could not add the remote'),
			);
		} finally {
			setRemoteBusy(false);
		}
	};

	const testRemote = async (name: string) => {
		if (!projectId || remoteTestBusy) return;
		setRemoteTestBusy(name);
		try {
			const result = await testProjectGitRemote(token, projectId, name, { allowNetwork: true });
			const passed = result.status === 'completed';
			notify({
				title: passed
					? t('app.statusBar.git.remoteTested', 'Remote reachable')
					: t('app.statusBar.git.remoteTestFailed', 'Remote test failed'),
				body: result.reason,
				tone: passed ? 'ok' : 'danger',
				durationMs: passed ? 5000 : 0,
			});
			await refreshGit();
		} catch (error) {
			notify({
				title: t('app.statusBar.git.remoteTestFailed', 'Remote test failed'),
				body: error instanceof Error ? error.message : undefined,
				tone: 'danger',
				durationMs: 0,
			});
		} finally {
			setRemoteTestBusy('');
		}
	};

	const connectionDetail = gitError || gitStatus?.reason || '';
	const projectName = selectedProject?.name ?? '';

	return (
		// biome-ignore lint/a11y/useSemanticElements: <fieldset> only groups form controls, but this bar mixes a branch form with read-only status items and action buttons; it also carries UA chrome (border, margin-inline:2px, min-inline-size:min-content) that the flex-based .composer-git-bar does not reset, regressing the layout. role="group"+aria-label keeps the labeled grouping without breaking layout.
		<div
			className="composer-git-bar"
			role="group"
			aria-label={t('app.composer.gitControls', 'Git workspace')}
		>
			<div className="composer-git-cluster composer-git-cluster--workspace">
				{gitPhase === 'loading' ? (
					<select
						className="status-branch-select"
						aria-label={t('app.statusBar.git.branchSelect', 'Git branch')}
						value=""
						disabled
						onChange={() => undefined}
					>
						<option value="">{t('app.statusBar.git.branchLoading', 'loading…')}</option>
					</select>
				) : gitPhase === 'error' ? (
					<Button
						variant="primary"
						icon={<RefreshCw aria-hidden="true" size={14} />}
						loading={gitBusy}
						disabled={!selectedProject}
						onClick={() => void refreshGit()}
					>
						{t('app.statusBar.git.retry', 'Retry')}
					</Button>
				) : gitPhase === 'blocked' ? (
					<span className="composer-git-note" data-tone="warn">
						<AlertTriangle aria-hidden="true" size={14} />
						{t('app.statusBar.git.blockedByPolicy', 'Blocked by policy')}
					</span>
				) : gitPhase === 'no_git' ? (
					<>
						{gitMissing ? (
							<span className="composer-git-note" data-tone="warn">
								<AlertTriangle aria-hidden="true" size={14} />
								{t('app.statusBar.git.gitNotInstalled', 'Git is not installed')}
							</span>
						) : (
							<Button
								variant="primary"
								icon={<GitBranch aria-hidden="true" size={14} />}
								loading={gitBusy}
								disabled={!selectedProject}
								onClick={() => void initGit()}
							>
								{t('app.statusBar.git.initGit', 'Initialize Git')}
							</Button>
						)}
						<span className="composer-git-note composer-git-note--hint">
							{t(
								'app.statusBar.git.whyGit',
								'AIDO delivers work as branches, diffs and secret-scanned commits: without Git it cannot isolate what an agent changed, show you the diff, or roll it back.',
							)}
						</span>
					</>
				) : branchMissing ? (
					<span className="composer-git-note" data-tone="warn">
						<GitCommitHorizontal aria-hidden="true" size={14} />
						{unbornBranch
							? `${t('app.statusBar.git.branchMissingPrefix', 'No commits yet — Git creates branch')} ${unbornBranch} ${t('app.statusBar.git.branchMissingSuffix', 'with the first commit')}`
							: t('app.statusBar.git.noCommits', 'No commits yet')}
					</span>
				) : (
					<>
						{detachedHead ? (
							<span className="composer-git-note" data-tone="warn">
								<GitBranch aria-hidden="true" size={14} />
								{t('app.statusBar.git.detachedHead', 'Detached HEAD')}
							</span>
						) : null}
						{showPicker ? (
							<>
								<span className="sr-only" id={selectHelpId}>
									{t(
										'app.statusBar.git.selectHelp',
										'Select a branch, then confirm with the checkout button',
									)}
								</span>
								<select
									className="status-branch-select"
									aria-label={t('app.statusBar.git.branchSelect', 'Git branch')}
									aria-describedby={selectHelpId}
									value={pendingBranch || (detachedHead ? '' : currentBranch)}
									disabled={!selectedProject || gitBusy}
									onChange={(event) =>
										setPendingBranch(event.target.value === currentBranch ? '' : event.target.value)
									}
								>
									{detachedHead ? (
										<option value="">
											{t('app.statusBar.git.branchDetachedOption', 'select a branch')}
										</option>
									) : null}
									{branchOptions.length ? null : (
										<option value="">
											{t('app.statusBar.git.branchUnavailable', 'not detected')}
										</option>
									)}
									{branchOptions.map((branch) => (
										<option key={branch} value={branch}>
											{branch}
										</option>
									))}
								</select>
								{pendingBranch ? (
									<Tooltip
										label={t('app.statusBar.git.confirmCheckout', 'Checkout selected branch')}
									>
										<IconButton
											aria-label={t(
												'app.statusBar.git.confirmCheckout',
												'Checkout selected branch',
											)}
											disabled={!selectedProject}
											loading={gitBusy}
											onClick={() => requestCheckout(pendingBranch)}
										>
											<Check aria-hidden="true" size={14} />
										</IconButton>
									</Tooltip>
								) : null}
								<div className="status-branch-create">
									<input
										value={branchName}
										disabled={!selectedProject || gitBusy}
										placeholder={t('app.statusBar.git.branchPlaceholder', 'new branch')}
										aria-label={t('app.statusBar.git.branchName', 'New branch name')}
										onChange={(event) => setBranchName(event.target.value)}
										onKeyDown={(event) => {
											if (event.key === 'Enter') {
												event.preventDefault();
												void createBranch();
											}
										}}
									/>
									<Tooltip label={t('app.statusBar.git.createBranch', 'Create branch')}>
										<IconButton
											aria-label={t('app.statusBar.git.createBranch', 'Create branch')}
											disabled={!selectedProject || !branchName.trim()}
											loading={gitBusy}
											onClick={() => void createBranch()}
										>
											<Plus aria-hidden="true" size={14} />
										</IconButton>
									</Tooltip>
								</div>
								{onlyProtectedBranch ? (
									<span className="composer-git-note composer-git-note--hint">
										{t('app.statusBar.git.onlyProtectedPrefix', 'Working on')} {currentBranch} —{' '}
										{t(
											'app.statusBar.git.onlyProtectedSuffix',
											'create a work branch before your first change',
										)}
									</span>
								) : null}
							</>
						) : null}
					</>
				)}
			</div>

			<div className="composer-git-cluster composer-git-cluster--status">
				{(() => {
					const connectionItem = (
						<span
							role="img"
							className="composer-git-item"
							aria-label={
								gitPhase === 'error'
									? t('app.statusBar.git.errorLabel', 'Git error')
									: gitPhase === 'blocked'
										? t('app.statusBar.git.blockedByPolicy', 'Blocked by policy')
										: ready
											? t('app.statusBar.git.connectedLabel', 'Git connected')
											: t('app.statusBar.git.notConnected', 'git not connected')
							}
							data-tone={gitConnTone !== 'ok' ? gitConnTone : undefined}
							tabIndex={connectionDetail ? 0 : undefined}
						>
							<StatusDot tone={gitConnTone} />
							<GitBranch aria-hidden="true" size={14} />
							{gitPhase === 'loading' ? (
								<span>{t('app.statusBar.git.branchLoading', 'loading…')}</span>
							) : null}
							{gitPhase === 'error' ? (
								<span>{t('app.statusBar.git.errorLabel', 'Git error')}</span>
							) : null}
							{gitPhase === 'blocked' ? (
								<span>{t('app.statusBar.git.blockedByPolicy', 'Blocked by policy')}</span>
							) : null}
							{gitPhase === 'no_git' ? (
								<span>
									{gitMissing
										? t('app.statusBar.git.gitNotInstalled', 'Git is not installed')
										: t('app.statusBar.git.noRepo', 'no repository')}
								</span>
							) : null}
						</span>
					);
					return connectionDetail ? (
						<Tooltip label={connectionDetail}>{connectionItem}</Tooltip>
					) : (
						connectionItem
					);
				})()}
				{ready ? (
					<>
						<span
							className="composer-git-item"
							data-tone={dirtyTone !== 'ok' ? dirtyTone : undefined}
							title={gitStatus?.porcelain.join('\n') ?? ''}
						>
							<StatusDot tone={dirtyTone} />
							<span className="composer-git-item-label">
								{dirty
									? `${t('app.statusBar.git.dirty', 'dirty')} · ${dirtyBreakdown}`
									: t('app.statusBar.git.clean', 'clean')}
							</span>
						</span>
						{(() => {
							const gitleaksDetail = gitleaks?.reason ?? '';
							const gitleaksInner = (
								<>
									<StatusDot tone={gitleaksTone} />
									{t('app.statusBar.git.gitleaksPrefix', 'gitleaks')} {gitleaksLabel}
								</>
							);
							if (gitleaksMissing) {
								return (
									<Tooltip label={t('app.statusBar.git.gitleaksSetup', 'Open setup')}>
										<button
											type="button"
											className="composer-git-item composer-git-item--action"
											data-tone="warn"
											onClick={() => setSetupOpen(true)}
										>
											{gitleaksInner}
										</button>
									</Tooltip>
								);
							}
							const chip = (
								<span
									className="composer-git-item"
									data-tone={gitleaksTone !== 'ok' ? gitleaksTone : undefined}
									tabIndex={gitleaksDetail ? 0 : undefined}
								>
									{gitleaksInner}
								</span>
							);
							return gitleaksDetail ? <Tooltip label={gitleaksDetail}>{chip}</Tooltip> : chip;
						})()}
						{noRemotes ? (
							<span className="composer-git-item" data-tone="warn">
								<StatusDot tone="warn" />
								{t('app.statusBar.git.noRemote', 'no remote')}
							</span>
						) : null}
						{lastCommit ? (
							<span
								className="composer-git-item"
								title={`${lastCommit.subject} — ${lastCommit.author} · ${lastCommit.authoredAt}`}
							>
								<GitCommitHorizontal aria-hidden="true" size={14} />
								<span className="mono">{lastCommit.shortHash}</span>
								<span className="composer-git-item-label">
									{truncateSubject(lastCommit.subject)}
								</span>
							</span>
						) : null}
					</>
				) : null}
			</div>

			<div className="composer-git-cluster composer-git-cluster--actions">
				{ready ? (
					<>
						{hasCommits ? (
							<Button
								variant="secondary"
								icon={<GitCompare aria-hidden="true" size={14} />}
								disabled={!selectedProject}
								onClick={openChanges}
							>
								{t('app.statusBar.git.viewChanges', 'View changes')}
							</Button>
						) : null}
						<Tooltip
							label={
								noRemotes
									? t('app.statusBar.git.addRemote', 'Add remote')
									: t('app.statusBar.git.remotes', 'Remotes')
							}
						>
							<IconButton
								aria-label={t('app.statusBar.git.remotes', 'Remotes')}
								disabled={!selectedProject}
								onClick={() => {
									setRemoteFormError('');
									setRemotesOpen(true);
								}}
							>
								<span className="composer-git-icon-badge">
									<Cloud aria-hidden="true" size={14} />
									{noRemotes ? <StatusDot tone="warn" /> : null}
								</span>
							</IconButton>
						</Tooltip>
						<Tooltip
							label={
								gitleaksMissing
									? t('app.statusBar.git.gitleaksSetup', 'Open setup')
									: gitleaksTone === 'danger'
										? t('app.statusBar.git.gitleaksRescan', 'Rescan')
										: t('app.statusBar.git.runGitleaks', 'Run gitleaks scan')
							}
						>
							<IconButton
								aria-label={
									gitleaksMissing
										? t('app.statusBar.git.gitleaksSetup', 'Open setup')
										: t('app.statusBar.git.runGitleaks', 'Run gitleaks scan')
								}
								disabled={!selectedProject}
								loading={gitleaksBusy}
								onClick={() => (gitleaksMissing ? setSetupOpen(true) : void runGitleaks())}
							>
								{gitleaksTone === 'danger' ? (
									<ShieldAlert aria-hidden="true" size={14} />
								) : (
									<ShieldCheck aria-hidden="true" size={14} />
								)}
							</IconButton>
						</Tooltip>
					</>
				) : null}
				<Tooltip label={t('app.statusBar.git.refresh', 'Refresh Git status')}>
					<IconButton
						aria-label={t('app.statusBar.git.refresh', 'Refresh Git status')}
						disabled={!selectedProject || gitBusy || gitPhase === 'loading'}
						onClick={() => void refreshGit()}
					>
						<RefreshCw aria-hidden="true" size={14} />
					</IconButton>
				</Tooltip>
			</div>

			<Dialog
				open={Boolean(checkoutConfirm)}
				onClose={() => setCheckoutConfirm('')}
				label={t('app.statusBar.git.checkoutDirtyTitle', 'Uncommitted changes')}
			>
				<div className="composer-git-dialog">
					<p>
						{t(
							'app.statusBar.git.checkoutDirtyBody',
							'Switching branches with uncommitted changes may fail or carry them over.',
						)}
					</p>
					{dirtyBreakdown ? <p className="composer-git-dialog-detail">{dirtyBreakdown}</p> : null}
					<div className="composer-git-dialog-actions">
						<Button
							variant="secondary"
							onClick={() => {
								setCheckoutConfirm('');
								openChanges();
							}}
						>
							{t('app.statusBar.git.viewChanges', 'View changes')}
						</Button>
						<Button
							variant="danger"
							loading={gitBusy}
							onClick={() => void checkoutBranch(checkoutConfirm, true)}
						>
							{t('app.statusBar.git.checkoutAnyway', 'Switch anyway')}
						</Button>
					</div>
				</div>
			</Dialog>

			<Dialog
				open={changesOpen}
				onClose={() => setChangesOpen(false)}
				label={`${t('app.statusBar.git.changesTitle', 'Git changes')}${projectName ? ` — ${projectName}` : ''}`}
			>
				<div className="composer-git-dialog">
					<div className="composer-git-dialog-toolbar">
						<Button
							variant="secondary"
							icon={<Download aria-hidden="true" size={14} />}
							disabled={diffBusy || !diff?.diff.trim()}
							onClick={savePatch}
						>
							{t('app.statusBar.git.savePatch', 'Save patch')}
						</Button>
					</div>
					{fileGroups.length ? (
						<div className="composer-git-files">
							{fileGroups.map((group) => (
								<section key={group.key} className="composer-git-file-group">
									<h3 className="composer-git-file-heading">
										{group.heading}
										<span className="composer-git-file-count">{group.files.length}</span>
									</h3>
									<ul className="composer-git-file-list">
										{group.files.map((file) => (
											<li key={file} className="mono">
												{file}
											</li>
										))}
									</ul>
								</section>
							))}
						</div>
					) : null}
					<div className="composer-git-diff" aria-live="polite">
						{diffBusy ? (
							<p className="composer-git-dialog-detail">
								{t('app.statusBar.git.branchLoading', 'loading…')}
							</p>
						) : diffError ? (
							<p className="composer-git-dialog-detail" data-tone="danger" role="alert">
								{diffError}
							</p>
						) : diff?.diff.trim() ? (
							<pre className="composer-git-diff-body">{diff.diff}</pre>
						) : (
							<p className="composer-git-dialog-detail">
								{t('app.statusBar.git.noChanges', 'No changes against HEAD')}
							</p>
						)}
					</div>
					{untrackedCount ? (
						<p className="composer-git-dialog-detail composer-git-note--hint">
							{t(
								'app.statusBar.git.untrackedNotInDiff',
								'Untracked files are listed above but never appear in the diff against HEAD.',
							)}
						</p>
					) : null}
				</div>
			</Dialog>

			<Dialog
				open={remotesOpen}
				onClose={() => setRemotesOpen(false)}
				label={`${t('app.statusBar.git.remotesTitle', 'Git remotes')}${projectName ? ` — ${projectName}` : ''}`}
			>
				<div className="composer-git-dialog">
					{remoteNames.length ? (
						<ul className="composer-git-remote-list">
							{remoteNames.map((name) => {
								const url = gitStatus?.remotes.find((remote) => remote.name === name)?.url ?? '';
								return (
									<li key={name} className="composer-git-remote-row">
										<span className="composer-git-remote-name">{name}</span>
										<span className="composer-git-remote-url mono">{url}</span>
										<Button
											variant="secondary"
											loading={remoteTestBusy === name}
											disabled={Boolean(remoteTestBusy)}
											onClick={() => void testRemote(name)}
										>
											{t('app.statusBar.git.testRemote', 'Test remote')}
										</Button>
									</li>
								);
							})}
						</ul>
					) : (
						<p className="composer-git-dialog-detail">
							{t('app.statusBar.git.noRemotes', 'No remotes configured')}
						</p>
					)}
					<div className="composer-git-remote-form">
						<TextField
							label={t('app.statusBar.git.remoteName', 'Remote name')}
							placeholder={t('app.statusBar.git.remoteNamePlaceholder', 'e.g. origin')}
							value={remoteName}
							onChange={(event) => setRemoteName(event.target.value)}
						/>
						<TextField
							label={t('app.statusBar.git.remoteUrl', 'Remote URL')}
							placeholder={t('app.statusBar.git.remoteUrlPlaceholder', 'https://host/org/repo.git')}
							value={remoteUrl}
							error={remoteFormError || undefined}
							onChange={(event) => setRemoteUrl(event.target.value)}
						/>
						<Button
							variant="primary"
							icon={<Plus aria-hidden="true" size={14} />}
							loading={remoteBusy}
							disabled={!remoteName.trim() || !remoteUrl.trim()}
							onClick={() => void addRemote()}
						>
							{t('app.statusBar.git.addRemote', 'Add remote')}
						</Button>
						<p className="composer-git-dialog-detail composer-git-note--hint">
							{t('app.statusBar.git.testRemoteHint', 'Testing a remote uses the network')}
						</p>
					</div>
				</div>
			</Dialog>

			<Dialog
				open={setupOpen}
				onClose={() => setSetupOpen(false)}
				label={t('app.statusBar.git.gitleaksSetupTitle', 'Gitleaks setup')}
			>
				<div className="composer-git-dialog">
					<p>
						{t(
							'app.statusBar.git.gitleaksSetupBody',
							'Gitleaks is not installed on this machine. Install it and add it to PATH, then rescan.',
						)}
					</p>
					<div className="composer-git-dialog-actions">
						<Button
							variant="primary"
							icon={<ShieldCheck aria-hidden="true" size={14} />}
							loading={gitleaksBusy}
							onClick={() => {
								setSetupOpen(false);
								void runGitleaks();
							}}
						>
							{t('app.statusBar.git.gitleaksRescan', 'Rescan')}
						</Button>
					</div>
				</div>
			</Dialog>
		</div>
	);
}
