/**
 * Git workspace controls rendered inside the chat composer (Codex-style), not in the footer.
 *
 * Owns the per-project git state — current/branch list, dirty/clean status and the last gitleaks
 * scan — and exposes the interactive controls: branch checkout, branch creation, gitleaks scan and a
 * manual refresh. Lives next to the task prompt because choosing the working branch is part of
 * describing the work, not global chrome. All writes go through the token-guarded git API.
 * @author Rodrigo Mason
 */
import { Check, GitBranch, GitCommitHorizontal, Plus, RefreshCw, ShieldCheck } from 'lucide-react';
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

/** Explicit render phase for the git bar: replaces the scattered ready/not-connected/error booleans
 *  with one discriminated state so every cluster (picker, status, actions) reads a single source of
 *  truth — and so the loading window becomes a first-class state instead of an implicit gap. */
type GitPhase = 'loading' | 'ready' | 'notConnected' | 'error';

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
	/** Branch picked in the select but not yet checked out — checkout needs the explicit
	 *  confirm button, so keyboard browsing of the list never triggers a checkout. */
	const [pendingBranch, setPendingBranch] = useState('');
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
		setPendingBranch('');
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
		setPendingBranch('');
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
	const lastCommit = gitStatus?.lastCommit?.shortHash ? gitStatus.lastCommit : null;
	// Single source of truth for the bar's render state. Both git fetches resolve together
	// (Promise.all in refreshGit), so `loading` is exactly the pre-response window — the moment the
	// branch <select> must already exist (inert) instead of appearing in the DOM seconds later.
	const gitPhase: GitPhase = gitError
		? 'error'
		: !gitStatus
			? 'loading'
			: gitStatus.status === 'configuration_required'
				? 'notConnected'
				: gitStatus.status === 'completed' && gitBranches?.status === 'completed'
					? 'ready'
					: 'error';
	const gitReady = gitPhase === 'ready';
	// The project owns no repo of its own (e.g. a folder nested inside another repo): show
	// "not connected" and hide the branch controls instead of another repository's branches.
	const notConnected = gitPhase === 'notConnected';
	const rawGitleaksStatus = gitleaks?.gitleaks.status;
	const gitleaksLabelDef = rawGitleaksStatus ? GITLEAKS_STATUS_LABEL[rawGitleaksStatus] : null;
	const gitleaksLabel = rawGitleaksStatus
		? gitleaksLabelDef
			? t(gitleaksLabelDef.labelKey, gitleaksLabelDef.fallback)
			: rawGitleaksStatus
		: t('app.statusBar.git.gitleaksNotRun', 'not run');

	// Derived tones for non-OK coloring of status label text.
	const dirtyTone = dirty ? 'warn' : 'ok';
	const gitConnTone = gitPhase === 'error' ? 'danger' : gitPhase === 'notConnected' ? 'warn' : 'ok';
	const gitleaksTone =
		gitleaks?.status === 'completed'
			? 'ok'
			: gitleaks?.status === 'blocked' || gitleaks?.status === 'failed'
				? 'danger'
				: 'ok'; // 'not run' and 'configuration_required' are informational, not problems

	return (
		// biome-ignore lint/a11y/useSemanticElements: <fieldset> only groups form controls, but this bar mixes a branch form with read-only status items and action buttons; it also carries UA chrome (border, margin-inline:2px, min-inline-size:min-content) that the flex-based .composer-git-bar does not reset, regressing the layout. role="group"+aria-label keeps the labeled grouping without breaking layout.
		<div
			className="composer-git-bar"
			role="group"
			aria-label={t('app.composer.gitControls', 'Git workspace')}
		>
			{/* Cluster (a): workspace — branch picker + create form. Rendered ONLY when the project owns
			    its own repo, so we never surface another repository's branches. */}
			{gitReady ? (
				<div className="composer-git-cluster composer-git-cluster--workspace">
					{/* Selecting only stages the branch (WCAG 3.2.2 On Input): browsing the list with
					    arrow keys must never fire a checkout. The Check button commits it. */}
					<select
						className="status-branch-select"
						aria-label={t('app.statusBar.git.branchSelect', 'Git branch')}
						value={pendingBranch || currentBranch}
						disabled={!selectedProject || gitBusy}
						onChange={(event) =>
							setPendingBranch(event.target.value === currentBranch ? '' : event.target.value)
						}
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
					{pendingBranch ? (
						<Tooltip label={t('app.statusBar.git.confirmCheckout', 'Checkout selected branch')}>
							<IconButton
								aria-label={t('app.statusBar.git.confirmCheckout', 'Checkout selected branch')}
								disabled={!selectedProject}
								loading={gitBusy}
								onClick={() => void checkoutBranch(pendingBranch)}
							>
								<Check aria-hidden="true" size={14} />
							</IconButton>
						</Tooltip>
					) : null}
					{/* Not a nested <form>: this bar renders inside the composer's <form>, and nesting forms
					    is invalid HTML that risks Enter here bubbling into a message send. Enter on the
					    input creates the branch via onKeyDown; the button is a plain type="button". */}
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
				</div>
			) : gitPhase === 'loading' ? (
				<div className="composer-git-cluster composer-git-cluster--workspace">
					{/* Loading window: the branch control is present but inert (nothing to browse yet,
					    WCAG 3.2.2 On Input) so it fills in when the policy-gated git fetch resolves instead
					    of popping into the DOM seconds later. onChange is a required no-op for a disabled
					    controlled <select>; the picker stays keyboard-reachable and announces "loading". */}
					<select
						className="status-branch-select"
						aria-label={t('app.statusBar.git.branchSelect', 'Git branch')}
						value=""
						disabled
						onChange={() => undefined}
					>
						<option value="">{t('app.statusBar.git.branchLoading', 'loading…')}</option>
					</select>
				</div>
			) : null}

			{/* Cluster (b): status — git connection (+ dirty/gitleaks when connected), read-only */}
			<div className="composer-git-cluster composer-git-cluster--status">
				{/* role="img" makes aria-label valid on this non-interactive span; the visible label (shown
				    only when not connected/error) is presentational and matches the aria-label. The reason
				    detail travels in a Tooltip on a focusable span (not title=), so keyboard and screen
				    reader users can reach the "why" too. */}
				{(() => {
					const connectionDetail = gitError || gitStatus?.reason || '';
					const connectionItem = (
						<span
							role="img"
							className="composer-git-item"
							aria-label={
								gitError
									? t('app.statusBar.git.errorLabel', 'Git error')
									: gitReady
										? t('app.statusBar.git.connectedLabel', 'Git connected')
										: t('app.statusBar.git.notConnected', 'git not connected')
							}
							data-tone={gitConnTone !== 'ok' ? gitConnTone : undefined}
							tabIndex={connectionDetail ? 0 : undefined}
						>
							<StatusDot tone={gitConnTone} />
							<GitBranch aria-hidden="true" size={14} />
							{!gitReady && (gitError || notConnected) ? (
								<span>
									{gitError
										? t('app.statusBar.git.errorLabel', 'Git error')
										: t('app.statusBar.git.notConnected', 'git not connected')}
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
				{gitReady ? (
					<>
						<span
							className="composer-git-item"
							data-tone={dirtyTone !== 'ok' ? dirtyTone : undefined}
							title={gitStatus?.porcelain.join('\n') ?? ''}
						>
							<StatusDot tone={dirtyTone} />
							{dirty
								? t('app.statusBar.git.dirty', 'dirty')
								: t('app.statusBar.git.clean', 'clean')}
						</span>
						{(() => {
							const gitleaksDetail = gitleaks?.reason ?? '';
							const gitleaksItem = (
								<span
									className="composer-git-item"
									data-tone={gitleaksTone !== 'ok' ? gitleaksTone : undefined}
									tabIndex={gitleaksDetail ? 0 : undefined}
								>
									<StatusDot tone={gitleaksTone} />
									{t('app.statusBar.git.gitleaksPrefix', 'gitleaks')} {gitleaksLabel}
								</span>
							);
							return gitleaksDetail ? (
								<Tooltip label={gitleaksDetail}>{gitleaksItem}</Tooltip>
							) : (
								gitleaksItem
							);
						})()}
						{lastCommit ? (
							<span
								className="composer-git-item"
								title={`${lastCommit.subject} — ${lastCommit.author} · ${lastCommit.authoredAt}`}
							>
								<GitCommitHorizontal aria-hidden="true" size={14} />
								<span className="mono">{lastCommit.shortHash}</span>
								<span>{truncateSubject(lastCommit.subject)}</span>
							</span>
						) : null}
					</>
				) : null}
			</div>

			{/* Cluster (c): actions — gitleaks scan (only when connected) + refresh, pushed right */}
			<div className="composer-git-cluster composer-git-cluster--actions">
				{gitReady ? (
					<Tooltip label={t('app.statusBar.git.runGitleaks', 'Run gitleaks scan')}>
						<IconButton
							aria-label={t('app.statusBar.git.runGitleaks', 'Run gitleaks scan')}
							disabled={!selectedProject}
							loading={gitleaksBusy}
							onClick={() => void runGitleaks()}
						>
							<ShieldCheck aria-hidden="true" size={14} />
						</IconButton>
					</Tooltip>
				) : null}
				<Tooltip label={t('app.statusBar.git.refresh', 'Refresh Git status')}>
					<IconButton
						aria-label={t('app.statusBar.git.refresh', 'Refresh Git status')}
						disabled={!selectedProject || gitBusy}
						onClick={() => void refreshGit()}
					>
						<RefreshCw aria-hidden="true" size={14} />
					</IconButton>
				</Tooltip>
			</div>
		</div>
	);
}
