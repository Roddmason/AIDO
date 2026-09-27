/**
 * Branch manager for one project: lists local (and remote-tracking) branches grouped by gitflow type,
 * shows a gitflow health summary with suggested actions, and runs the cleanup flows — delete merged
 * branches, safe/force delete, opt-in remote delete, prune stale remote refs, rename to a gitflow
 * prefix and remove AIDO worktrees whose branch is already integrated.
 *
 * Every git command runs server-side as a queued, policy-gated execution (ToolBroker); this dialog
 * only renders the durable inventory and the per-branch results. All confirmations are steps inside
 * the same Dialog (one focus trap, Escape backs out one level) and list exactly what will happen.
 * Protected branches (dev/develop/main/master, HEAD, the integration branch, anything checked out in
 * a worktree) are never selectable; the server re-validates everything before touching git.
 * @author Rodrigo Mason
 */
import {
	AlertTriangle,
	ArrowLeft,
	CheckCircle2,
	FolderMinus,
	GitBranch,
	Pencil,
	RefreshCw,
	Scissors,
	Trash2,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
	type BranchActionResponse,
	type BranchActionResult,
	type BranchHealthIssue,
	type BranchInventory,
	type BranchInventoryRecord,
	deleteBranches,
	getBranchInventory,
	pruneRemoteBranches,
	removeMergedWorktrees,
	renameBranch,
	scanBranches,
} from '../../api/client';
import type { ExecutionObserver } from '../../api/execution-client';
import type { Project } from '../../api/types';
import type { Mutate } from '../../app/routes';
import {
	Button,
	Checkbox,
	Dialog,
	EmptyState,
	IconButton,
	SegmentedControl,
	StatusChip,
	TextField,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';

type Filter = 'all' | 'cleanup' | 'stale' | 'nonGitflow' | 'protected';
type View =
	| { kind: 'list' }
	| { kind: 'confirmDelete'; branches: BranchInventoryRecord[] }
	| { kind: 'rename'; branch: BranchInventoryRecord }
	| { kind: 'confirmWorktrees'; branches: BranchInventoryRecord[] }
	| { kind: 'results'; response: BranchActionResponse };

const GITFLOW_PREFIXES = ['feature/', 'bugfix/', 'release/', 'hotfix/', 'support/'];
const TYPE_ORDER = [
	'integration',
	'feature',
	'bugfix',
	'release',
	'hotfix',
	'support',
	'aido',
	'other',
] as const;

const keyOf = (branch: BranchInventoryRecord) => `${branch.kind}:${branch.name}`;
const isMergedWorktree = (branch: BranchInventoryRecord) =>
	Boolean(branch.aidoWorkspaces?.length) &&
	(branch.mergeState === 'merged' || branch.mergeState === 'squash_probable');

type BranchManagerDialogProps = {
	project: Project | null;
	mutate: Mutate;
	onClose: () => void;
	onOpenSettings: (section?: string) => void;
};

/** Modal branch manager; mounted by the sidebar with the project picked from its "⋯" menu. */
export function BranchManagerDialog({
	project,
	mutate,
	onClose,
	onOpenSettings,
}: BranchManagerDialogProps) {
	const { t } = useI18n();
	const [inventory, setInventory] = useState<BranchInventory | null>(null);
	const [busy, setBusy] = useState<'' | 'load' | 'scan' | 'action'>('');
	const [progress, setProgress] = useState('');
	const [error, setError] = useState('');
	const [filter, setFilter] = useState<Filter>('all');
	const [query, setQuery] = useState('');
	const [showRemote, setShowRemote] = useState(false);
	const [selected, setSelected] = useState<Set<string>>(new Set());
	const [view, setView] = useState<View>({ kind: 'list' });
	const [forceAck, setForceAck] = useState(false);
	const [deleteRemote, setDeleteRemote] = useState(false);
	const [renameValue, setRenameValue] = useState('');
	const stepHeadingRef = useRef<HTMLHeadingElement>(null);
	const projectId = project?.id ?? '';
	const integration = inventory?.integrationBranch || 'dev';

	const observer: ExecutionObserver = useCallback(
		(execution) => {
			const labels: Record<string, string> = {
				queued: t('app.branches.progress.queued', 'Queued — waiting for the worker…'),
				resource_wait: t('app.branches.progress.waiting', 'Waiting for host resources…'),
				running: t('app.branches.progress.running', 'Running git through the policy broker…'),
			};
			setProgress(labels[execution.status] ?? '');
		},
		[t],
	);

	const errorText = useCallback(
		(caught: unknown) =>
			caught instanceof Error && caught.message
				? caught.message
				: t('app.branches.error.generic', 'The branch operation could not be completed.'),
		[t],
	);

	const scan = useCallback(async () => {
		if (!projectId) return;
		setBusy('scan');
		setError('');
		try {
			const next = await mutate((token) => scanBranches(token, projectId, {}, observer), {
				awaitRefresh: false,
			});
			setInventory(next);
			setSelected(new Set());
		} catch (caught) {
			setError(errorText(caught));
		} finally {
			setBusy('');
			setProgress('');
		}
	}, [projectId, mutate, observer, errorText]);

	// biome-ignore lint/correctness/useExhaustiveDependencies: reload only when the target project changes; `scan` identity follows the translator and must not re-trigger a git scan.
	useEffect(() => {
		setInventory(null);
		setSelected(new Set());
		setView({ kind: 'list' });
		setFilter('all');
		setQuery('');
		setError('');
		if (!projectId) return undefined;
		const controller = new AbortController();
		setBusy('load');
		getBranchInventory(projectId, controller.signal)
			.then((current) => {
				if (controller.signal.aborted) return;
				setInventory(current);
				setBusy('');
				if (current.status !== 'completed' || current.refreshRequired) void scan();
			})
			.catch(() => {
				if (controller.signal.aborted) return;
				setBusy('');
				void scan();
			});
		return () => controller.abort();
	}, [projectId]);

	useEffect(() => {
		if (view.kind !== 'list') stepHeadingRef.current?.focus();
	}, [view.kind]);

	const branches = inventory?.branches ?? [];
	const visible = useMemo(() => {
		const needle = query.trim().toLowerCase();
		return branches.filter((branch) => {
			if (branch.kind === 'remote' && !showRemote) return false;
			if (needle && !branch.name.toLowerCase().includes(needle)) return false;
			if (filter === 'cleanup')
				return (
					!branch.protected &&
					(branch.mergeState === 'merged' || branch.mergeState === 'squash_probable')
				);
			if (filter === 'stale') return Boolean(branch.stale);
			if (filter === 'nonGitflow') return branch.type === 'other' && !branch.protected;
			if (filter === 'protected') return Boolean(branch.protected);
			return true;
		});
	}, [branches, filter, query, showRemote]);

	const groups = useMemo(
		() =>
			TYPE_ORDER.map((type) => ({
				type,
				items: visible.filter((branch) => branch.type === type),
			})).filter((group) => group.items.length),
		[visible],
	);

	const selectedBranches = branches.filter((branch) => selected.has(keyOf(branch)));
	const mergedLocal = branches.filter(
		(branch) =>
			branch.kind === 'local' &&
			!branch.protected &&
			branch.mergeState === 'merged' &&
			!branch.aidoWorkspaces?.length,
	);

	const typeLabel: Record<(typeof TYPE_ORDER)[number], string> = {
		integration: t('app.branches.type.integration', 'Integration'),
		feature: t('app.branches.type.feature', 'Features'),
		bugfix: t('app.branches.type.bugfix', 'Bugfixes'),
		release: t('app.branches.type.release', 'Releases'),
		hotfix: t('app.branches.type.hotfix', 'Hotfixes'),
		support: t('app.branches.type.support', 'Support'),
		aido: t('app.branches.type.aido', 'AIDO work branches'),
		other: t('app.branches.type.other', 'Without gitflow prefix'),
	};

	const reasonLabel = (reason: string) => {
		const known: Record<string, string> = {
			not_found: t('app.branches.reason.notFound', 'Branch no longer exists.'),
			'protected:integration_branch': t(
				'app.branches.reason.protectedIntegration',
				'Protected: integration branch.',
			),
			'protected:gitflow_mainline': t(
				'app.branches.reason.protectedMainline',
				'Protected: gitflow mainline (dev/develop/main/master).',
			),
			'protected:current_branch': t(
				'app.branches.reason.protectedCurrent',
				'Protected: currently checked out.',
			),
			'protected:checked_out_in_worktree': t(
				'app.branches.reason.protectedWorktree',
				'Protected: checked out in a worktree. Remove the worktree first.',
			),
			aido_workspace_active: t(
				'app.branches.reason.aidoWorkspace',
				'An AIDO workspace still uses this branch.',
			),
			not_merged_requires_force: t(
				'app.branches.reason.requiresForce',
				'Not merged into the integration branch; force delete was not confirmed.',
			),
			squash_probable_requires_force: t(
				'app.branches.reason.squashRequiresForce',
				'Probably squash-merged, but not certain; force delete was not confirmed.',
			),
			remote_delete_not_enabled: t(
				'app.branches.reason.remoteDisabled',
				'Remote deletion was not enabled.',
			),
			protected_or_unsafe: t(
				'app.branches.reason.protectedRemote',
				'Protected or unsafe remote branch.',
			),
			has_upstream: t(
				'app.branches.reason.hasUpstream',
				'Has an upstream; rename only local branches without upstream.',
			),
			aido_managed_branch: t(
				'app.branches.reason.aidoManaged',
				'AIDO-managed branch; it is not renamed.',
			),
			invalid_gitflow_name: t(
				'app.branches.reason.invalidName',
				'The new name must start with a gitflow prefix.',
			),
			target_exists: t(
				'app.branches.reason.targetExists',
				'A branch with that name already exists.',
			),
			branch_not_merged: t(
				'app.branches.reason.branchNotMerged',
				'Its branch is not merged; the worktree was kept.',
			),
			no_remote: t('app.branches.reason.noRemote', 'The repository has no remote.'),
			unknown_remote: t('app.branches.reason.unknownRemote', 'Unknown remote.'),
		};
		return known[reason] ?? reason;
	};

	const statusLabel: Record<BranchActionResult['status'], string> = {
		deleted: t('app.branches.status.deleted', 'Deleted'),
		renamed: t('app.branches.status.renamed', 'Renamed'),
		pruned: t('app.branches.status.pruned', 'Pruned'),
		removed: t('app.branches.status.removed', 'Removed'),
		skipped: t('app.branches.status.skipped', 'Skipped'),
		failed: t('app.branches.status.failed', 'Failed'),
		blocked: t('app.branches.status.blocked', 'Blocked by policy'),
	};

	const runAction = async (operation: (token: string) => Promise<BranchActionResponse>) => {
		setBusy('action');
		setError('');
		try {
			const response = await mutate(operation, { awaitRefresh: false });
			setSelected(new Set());
			setView({ kind: 'results', response });
		} catch (caught) {
			setError(errorText(caught));
		} finally {
			setBusy('');
			setProgress('');
		}
	};

	const openDelete = (items: BranchInventoryRecord[]) => {
		setForceAck(false);
		setDeleteRemote(false);
		setView({ kind: 'confirmDelete', branches: items.filter((branch) => !branch.protected) });
	};

	const toggle = (branch: BranchInventoryRecord) =>
		setSelected((current) => {
			const next = new Set(current);
			const key = keyOf(branch);
			if (next.has(key)) next.delete(key);
			else next.add(key);
			return next;
		});

	const issueCopy = (issue: BranchHealthIssue) => {
		const count = String(issue.branches?.length ?? 0);
		const copy: Record<BranchHealthIssue['kind'], string> = {
			merged_not_deleted: t(
				'app.branches.health.merged',
				'{count} branch(es) already merged into {base} but not deleted.',
			),
			squash_probable: t(
				'app.branches.health.squash',
				'{count} branch(es) probably squash-merged into {base} (not certain; review before force delete).',
			),
			merged_worktree: t(
				'app.branches.health.mergedWorktree',
				'{count} AIDO worktree(s) whose branch is already integrated.',
			),
			upstream_gone: t(
				'app.branches.health.upstreamGone',
				'{count} branch(es) whose upstream was deleted on the remote.',
			),
			non_gitflow: t(
				'app.branches.health.nonGitflow',
				'{count} local branch(es) without a gitflow prefix.',
			),
			stale: t('app.branches.health.stale', '{count} unmerged branch(es) older than {days} days.'),
			far_behind: t(
				'app.branches.health.farBehind',
				'{count} branch(es) far behind {base}; merge {base} into them manually (AIDO never rebases).',
			),
			based_on_main: t(
				'app.branches.health.basedOnMain',
				'{count} feature branch(es) seem based on main instead of {base}; consider recreating them from {base}.',
			),
			integration_missing: t(
				'app.branches.health.integrationMissing',
				'The integration branch {base} does not exist in this repository.',
			),
		};
		return copy[issue.kind]
			.replaceAll('{count}', count)
			.replaceAll('{base}', integration)
			.replace('{days}', String(inventory?.staleDays ?? 30));
	};

	const issueAction = (issue: BranchHealthIssue) => {
		const names = new Set(issue.branches ?? []);
		const affected = branches.filter((branch) => names.has(branch.name));
		switch (issue.suggestedAction) {
			case 'delete_merged':
				return {
					label: t('app.branches.action.reviewDelete', 'Review and delete'),
					run: () => openDelete(affected.filter((branch) => branch.kind === 'local')),
				};
			case 'review_force_delete':
				return {
					label: t('app.branches.action.showCleanup', 'Show cleanup candidates'),
					run: () => setFilter('cleanup'),
				};
			case 'remove_worktree':
				return {
					label: t('app.branches.action.removeWorktrees', 'Remove worktrees…'),
					run: () => setView({ kind: 'confirmWorktrees', branches: affected }),
				};
			case 'prune':
				return {
					label: t('app.branches.action.prune', 'Prune remote refs'),
					run: () => void prune(),
				};
			case 'rename':
				return {
					label: t('app.branches.action.showNonGitflow', 'Show and rename'),
					run: () => setFilter('nonGitflow'),
				};
			case 'review':
				return {
					label: t('app.branches.action.showStale', 'Show stale'),
					run: () => setFilter('stale'),
				};
			case 'configure_integration':
				return {
					label: t('app.branches.action.gitSettings', 'Open Git settings'),
					run: () => {
						onClose();
						onOpenSettings('git');
					},
				};
			default:
				return null;
		}
	};

	const prune = () => runAction((token) => pruneRemoteBranches(token, projectId, {}, observer));

	const title = project
		? t('app.branches.title', 'Branches — {project}').replace('{project}', project.name)
		: t('app.branches.titleEmpty', 'Branches');

	const renderList = () => {
		if (!inventory || (busy === 'load' && !branches.length)) {
			return (
				<p className="branch-manager-note" role="status">
					{t('app.branches.loading', 'Loading branch inventory…')}
				</p>
			);
		}
		if (inventory.status !== 'completed' && !branches.length) {
			return (
				<EmptyState
					title={t('app.branches.unavailableTitle', 'No branch inventory yet')}
					body={inventory.reason}
				/>
			);
		}
		const issues = inventory.health?.issues ?? [];
		return (
			<>
				<section className="branch-health" aria-labelledby="branch-health-title">
					<h3 id="branch-health-title">{t('app.branches.healthTitle', 'Gitflow health')}</h3>
					{issues.length ? (
						<ul>
							{issues.map((issue) => {
								const action = issueAction(issue);
								return (
									<li key={issue.kind} data-severity={issue.severity}>
										<AlertTriangle aria-hidden="true" size={14} />
										<span className="branch-health-text">{issueCopy(issue)}</span>
										{action ? (
											<Button
												className="branch-health-action"
												disabled={busy !== ''}
												onClick={action.run}
											>
												{action.label}
											</Button>
										) : null}
									</li>
								);
							})}
						</ul>
					) : (
						<p className="branch-manager-note">
							<CheckCircle2 aria-hidden="true" size={14} />{' '}
							{t('app.branches.healthy', 'Gitflow looks tidy: nothing to clean up.')}
						</p>
					)}
				</section>
				<div className="branch-manager-filters">
					<SegmentedControl<Filter>
						label={t('app.branches.filter.label', 'Filter branches')}
						value={filter}
						onChange={setFilter}
						options={[
							{ value: 'all', label: t('app.branches.filter.all', 'All') },
							{ value: 'cleanup', label: t('app.branches.filter.cleanup', 'Merged') },
							{ value: 'stale', label: t('app.branches.filter.stale', 'Stale') },
							{ value: 'nonGitflow', label: t('app.branches.filter.nonGitflow', 'No prefix') },
							{ value: 'protected', label: t('app.branches.filter.protected', 'Protected') },
						]}
					/>
					<input
						type="search"
						className="input branch-manager-search"
						aria-label={t('app.branches.searchLabel', 'Search branches')}
						placeholder={t('app.branches.searchPlaceholder', 'Search branches…')}
						value={query}
						onChange={(event) => setQuery(event.target.value)}
					/>
					{inventory.remotes?.length ? (
						<Checkbox
							label={t('app.branches.showRemote', 'Show remote branches')}
							checked={showRemote}
							onChange={(event) => setShowRemote(event.target.checked)}
						/>
					) : null}
				</div>
				{groups.length ? (
					<div className="branch-groups">
						{groups.map((group) => (
							<section
								key={group.type}
								className="branch-group"
								aria-labelledby={`branch-group-${group.type}`}
							>
								<h3 id={`branch-group-${group.type}`}>
									{typeLabel[group.type]} <span className="thread-count">{group.items.length}</span>
								</h3>
								<ul className="branch-list">{group.items.map((branch) => renderRow(branch))}</ul>
							</section>
						))}
					</div>
				) : (
					<p className="branch-manager-note">
						{t('app.branches.noMatches', 'No branches match this filter.')}
					</p>
				)}
			</>
		);
	};

	const renderRow = (branch: BranchInventoryRecord) => {
		const key = keyOf(branch);
		const canRename =
			branch.kind === 'local' &&
			!branch.protected &&
			branch.type !== 'aido' &&
			!branch.aidoWorkspaces?.length &&
			(branch.upstream?.status ?? 'none') === 'none';
		const date = branch.lastCommit.date ? new Date(branch.lastCommit.date) : null;
		const protectedReason = branch.protectedReason
			? reasonLabel(`protected:${branch.protectedReason}`)
			: '';
		return (
			<li key={key} className="branch-row" data-protected={branch.protected || undefined}>
				<input
					type="checkbox"
					className="branch-row-check"
					aria-label={t('app.branches.selectBranch', 'Select {branch}').replace(
						'{branch}',
						branch.name,
					)}
					checked={selected.has(key)}
					disabled={Boolean(branch.protected) || busy !== ''}
					title={protectedReason || undefined}
					onChange={() => toggle(branch)}
				/>
				<div className="branch-row-main">
					<div className="branch-row-title">
						<GitBranch aria-hidden="true" size={14} />
						<span className="branch-row-name">{branch.name}</span>
						{branch.current ? (
							<StatusChip tone="info">{t('app.branches.chip.current', 'HEAD')}</StatusChip>
						) : null}
						{branch.mergeState === 'merged' ? (
							<StatusChip tone="ok">
								{t('app.branches.chip.merged', 'Merged into {base}').replace('{base}', integration)}
							</StatusChip>
						) : null}
						{branch.mergeState === 'squash_probable' ? (
							<StatusChip tone="warn" title={branch.mergeEvidence ?? undefined}>
								{t('app.branches.chip.squash', 'Probably merged (squash)')}
							</StatusChip>
						) : null}
						{branch.protected ? (
							<StatusChip tone="info" title={protectedReason}>
								{t('app.branches.chip.protected', 'Protected')}
							</StatusChip>
						) : null}
						{branch.upstream?.status === 'gone' ? (
							<StatusChip tone="warn">{t('app.branches.chip.gone', 'Upstream gone')}</StatusChip>
						) : null}
						{branch.stale ? (
							<StatusChip tone="warn">
								{t('app.branches.chip.stale', 'Stale {days}d').replace(
									'{days}',
									String(branch.ageDays ?? ''),
								)}
							</StatusChip>
						) : null}
						{branch.aidoWorkspaces?.length ? (
							<StatusChip tone="info">
								{t('app.branches.chip.worktree', 'AIDO worktree')}
							</StatusChip>
						) : null}
						{branch.basedOnMain ? (
							<StatusChip tone="warn">
								{t('app.branches.chip.basedOnMain', 'Based on main')}
							</StatusChip>
						) : null}
					</div>
					<p className="branch-row-meta">
						{date ? (
							<time dateTime={branch.lastCommit.date}>{date.toLocaleDateString()}</time>
						) : null}
						{branch.lastCommit.author ? <span>{branch.lastCommit.author}</span> : null}
						{branch.lastCommit.subject ? (
							<span className="branch-row-subject" title={branch.lastCommit.subject}>
								{branch.lastCommit.subject}
							</span>
						) : null}
						{branch.ahead != null && branch.behind != null && branch.type !== 'integration' ? (
							<span className="branch-row-counts">
								{t('app.branches.aheadBehind', '↑{ahead} ↓{behind} vs {base}')
									.replace('{ahead}', String(branch.ahead))
									.replace('{behind}', String(branch.behind))
									.replace('{base}', integration)}
							</span>
						) : null}
						{branch.upstream?.name ? (
							<span className="branch-row-upstream">
								{t('app.branches.tracking', 'tracks {upstream}').replace(
									'{upstream}',
									branch.upstream.name,
								)}
							</span>
						) : null}
					</p>
				</div>
				<div className="branch-row-actions">
					{isMergedWorktree(branch) ? (
						<IconButton
							aria-label={t(
								'app.branches.removeWorktreeFor',
								'Remove worktree of {branch}',
							).replace('{branch}', branch.name)}
							disabled={busy !== ''}
							onClick={() => setView({ kind: 'confirmWorktrees', branches: [branch] })}
						>
							<FolderMinus aria-hidden="true" size={14} />
						</IconButton>
					) : null}
					{canRename ? (
						<IconButton
							aria-label={t('app.branches.renameBranch', 'Rename {branch}').replace(
								'{branch}',
								branch.name,
							)}
							disabled={busy !== ''}
							onClick={() => {
								setRenameValue(branch.suggestedName || `feature/${branch.name}`);
								setView({ kind: 'rename', branch });
							}}
						>
							<Pencil aria-hidden="true" size={14} />
						</IconButton>
					) : null}
					{!branch.protected ? (
						<IconButton
							aria-label={t('app.branches.deleteBranch', 'Delete {branch}').replace(
								'{branch}',
								branch.name,
							)}
							disabled={busy !== ''}
							onClick={() => openDelete([branch])}
						>
							<Trash2 aria-hidden="true" size={14} />
						</IconButton>
					) : null}
				</div>
			</li>
		);
	};

	const renderConfirmDelete = (items: BranchInventoryRecord[]) => {
		const local = items.filter((branch) => branch.kind === 'local');
		const remote = items.filter((branch) => branch.kind === 'remote');
		const safe = local.filter((branch) => branch.mergeState === 'merged');
		const forced = items.filter((branch) => branch.mergeState !== 'merged');
		const upstreams = local
			.filter((branch) => branch.upstream?.status === 'tracking' && branch.upstream.name)
			.map((branch) => branch.upstream?.name ?? '');
		const remoteTargets = [...upstreams, ...remote.map((branch) => branch.name)];
		const blocked = forced.length > 0 && !forceAck;
		const confirm = () =>
			runAction((token) =>
				deleteBranches(
					token,
					projectId,
					{
						branches: local.map((branch) => branch.name),
						forceBranches: forceAck ? forced.map((branch) => branch.name) : [],
						deleteRemote,
						remoteBranches: remote.map((branch) => branch.name),
					},
					observer,
				),
			);
		return (
			<div className="branch-step">
				<h3 ref={stepHeadingRef} tabIndex={-1}>
					{t('app.branches.confirm.title', 'Confirm branch deletion')}
				</h3>
				{safe.length ? (
					<section>
						<p className="branch-step-label">
							{t(
								'app.branches.confirm.safe',
								'Merged into {base} — safe delete (git branch -d):',
							).replace('{base}', integration)}
						</p>
						<ul className="branch-step-list">
							{safe.map((branch) => (
								<li key={keyOf(branch)}>{branch.name}</li>
							))}
						</ul>
					</section>
				) : null}
				{forced.length ? (
					<section className="branch-step-danger">
						<p className="branch-step-label">
							{t(
								'app.branches.confirm.force',
								'NOT certainly merged into {base} — force delete (git branch -D):',
							).replace('{base}', integration)}
						</p>
						<ul className="branch-step-list">
							{forced.map((branch) => (
								<li key={keyOf(branch)}>
									{branch.name}{' '}
									<span className="branch-step-hint">
										{branch.mergeState === 'squash_probable'
											? t('app.branches.chip.squash', 'Probably merged (squash)')
											: t('app.branches.confirm.unmerged', 'has commits not in {base}').replace(
													'{base}',
													integration,
												)}
									</span>
								</li>
							))}
						</ul>
						<Checkbox
							label={t(
								'app.branches.confirm.forceAck',
								'I understand these commits may be lost and want to force delete them.',
							)}
							checked={forceAck}
							onChange={(event) => setForceAck(event.target.checked)}
						/>
					</section>
				) : null}
				{inventory?.remotes?.length ? (
					<section>
						<Checkbox
							label={t(
								'app.branches.confirm.remote',
								'Also delete the branch on the remote (git push <remote> --delete)',
							)}
							help={t(
								'app.branches.confirm.remoteHelp',
								'Off by default. Protected branches are never deleted on the remote.',
							)}
							checked={deleteRemote}
							onChange={(event) => setDeleteRemote(event.target.checked)}
						/>
						{deleteRemote && remoteTargets.length ? (
							<ul className="branch-step-list">
								{remoteTargets.map((name) => (
									<li key={name}>{name}</li>
								))}
							</ul>
						) : null}
						{!deleteRemote && remote.length ? (
							<p className="branch-step-hint">
								{t(
									'app.branches.confirm.remoteSkipped',
									'Selected remote branches will be skipped unless remote deletion is enabled.',
								)}
							</p>
						) : null}
					</section>
				) : null}
				{renderStepActions(
					t('app.branches.confirm.submit', 'Delete {count} branch(es)').replace(
						'{count}',
						String(items.length),
					),
					confirm,
					blocked || !items.length,
					'danger',
				)}
			</div>
		);
	};

	const renderRename = (branch: BranchInventoryRecord) => {
		const valid =
			GITFLOW_PREFIXES.some((prefix) => renameValue.startsWith(prefix)) &&
			!/\s|\.\.|[~^:?*[\\]/.test(renameValue) &&
			renameValue.length > 8;
		return (
			<div className="branch-step">
				<h3 ref={stepHeadingRef} tabIndex={-1}>
					{t('app.branches.rename.title', 'Rename {branch}').replace('{branch}', branch.name)}
				</h3>
				<TextField
					label={t('app.branches.rename.label', 'New gitflow name')}
					help={t(
						'app.branches.rename.help',
						'Must start with feature/, bugfix/, release/, hotfix/ or support/. Only local branches without upstream are renamed.',
					)}
					error={
						renameValue && !valid
							? t('app.branches.rename.invalid', 'Use a gitflow prefix and a valid branch name.')
							: undefined
					}
					value={renameValue}
					onChange={(event) => setRenameValue(event.target.value)}
				/>
				{renderStepActions(
					t('app.branches.rename.submit', 'Rename branch'),
					() =>
						runAction((token) =>
							renameBranch(token, projectId, { branch: branch.name, newName: renameValue }),
						),
					!valid,
					'primary',
				)}
			</div>
		);
	};

	const renderConfirmWorktrees = (items: BranchInventoryRecord[]) => {
		const workspaces = items.flatMap((branch) =>
			(branch.aidoWorkspaces ?? []).map((workspace) => ({ branch, workspace })),
		);
		return (
			<div className="branch-step">
				<h3 ref={stepHeadingRef} tabIndex={-1}>
					{t('app.branches.worktrees.title', 'Remove integrated AIDO worktrees')}
				</h3>
				<p className="branch-step-hint">
					{t(
						'app.branches.worktrees.body',
						'The workspace is archived and its worktree folder removed; the branch itself is kept until you delete it.',
					)}
				</p>
				<ul className="branch-step-list">
					{workspaces.map(({ branch, workspace }) => (
						<li key={workspace.workspaceId}>
							{branch.name}
							<span className="branch-step-hint"> {workspace.path}</span>
						</li>
					))}
				</ul>
				{renderStepActions(
					t('app.branches.worktrees.submit', 'Remove {count} worktree(s)').replace(
						'{count}',
						String(workspaces.length),
					),
					() =>
						runAction((token) =>
							removeMergedWorktrees(
								token,
								projectId,
								{ workspaceIds: workspaces.map(({ workspace }) => workspace.workspaceId) },
								observer,
							),
						),
					!workspaces.length,
					'danger',
				)}
			</div>
		);
	};

	const renderResults = (response: BranchActionResponse) => (
		<div className="branch-step">
			<h3 ref={stepHeadingRef} tabIndex={-1}>
				{t('app.branches.results.title', 'Result')}
			</h3>
			<p className="branch-step-hint">
				{t('app.branches.results.summary', '{done} done · {skipped} skipped · {failed} failed')
					.replace('{done}', String(response.summary?.done ?? 0))
					.replace('{skipped}', String(response.summary?.skipped ?? 0))
					.replace('{failed}', String(response.summary?.failed ?? 0))}
			</p>
			<ul className="branch-results">
				{(response.results ?? []).map((result) => (
					<li key={`${result.kind}-${result.action}-${result.branch}`} data-status={result.status}>
						<StatusChip
							tone={
								result.status === 'skipped'
									? 'pending'
									: result.status === 'failed' || result.status === 'blocked'
										? 'danger'
										: 'ok'
							}
						>
							{statusLabel[result.status]}
						</StatusChip>
						<span className="branch-row-name">{result.branch}</span>
						{result.forced ? (
							<span className="branch-step-hint">{t('app.branches.results.forced', 'forced')}</span>
						) : null}
						<span className="branch-results-reason">
							{reasonLabel(result.reason ?? '')}
							{result.detail?.length ? ` ${result.detail.join(', ')}` : ''}
						</span>
					</li>
				))}
			</ul>
			<div className="branch-step-actions">
				<Button
					variant="primary"
					icon={<ArrowLeft aria-hidden="true" size={14} />}
					onClick={() => {
						setView({ kind: 'list' });
						void scan();
					}}
				>
					{t('app.branches.results.back', 'Back to branches')}
				</Button>
			</div>
		</div>
	);

	const renderStepActions = (
		label: string,
		onConfirm: () => void,
		disabled: boolean,
		variant: 'primary' | 'danger',
	) => (
		<div className="branch-step-actions">
			<Button disabled={busy !== ''} onClick={() => setView({ kind: 'list' })}>
				{t('app.branches.back', 'Back')}
			</Button>
			<Button variant={variant} loading={busy === 'action'} disabled={disabled} onClick={onConfirm}>
				{label}
			</Button>
		</div>
	);

	const close = () => {
		if (view.kind !== 'list' && view.kind !== 'results' && busy !== 'action') {
			setView({ kind: 'list' });
			return;
		}
		onClose();
	};

	return (
		<Dialog open={project != null} onClose={close} label={title} className="branch-manager">
			<div className="branch-manager-body">
				<div className="branch-manager-toolbar">
					<p className="branch-manager-context">
						{t('app.branches.integration', 'Integration branch')}: <code>{integration}</code>
						{inventory?.currentBranch ? (
							<>
								{' · '}
								{t('app.branches.current', 'HEAD')}: <code>{inventory.currentBranch}</code>
							</>
						) : null}
					</p>
					<div className="branch-manager-toolbar-actions">
						{inventory?.remotes?.length ? (
							<Button
								icon={<Scissors aria-hidden="true" size={14} />}
								disabled={busy !== '' || view.kind !== 'list'}
								onClick={() => void prune()}
							>
								{t('app.branches.action.prune', 'Prune remote refs')}
							</Button>
						) : null}
						<Button
							icon={<RefreshCw aria-hidden="true" size={14} />}
							loading={busy === 'scan'}
							disabled={busy !== '' || view.kind !== 'list'}
							onClick={() => void scan()}
						>
							{t('app.branches.rescan', 'Rescan')}
						</Button>
					</div>
				</div>
				<p className="branch-manager-progress" role="status" aria-live="polite">
					{busy === 'scan'
						? progress || t('app.branches.scanning', 'Scanning branches…')
						: busy === 'action'
							? progress || t('app.branches.applying', 'Applying changes…')
							: ''}
				</p>
				{error ? (
					<p className="branch-manager-error" role="alert">
						{error}
					</p>
				) : null}
				{view.kind === 'list' ? renderList() : null}
				{view.kind === 'confirmDelete' ? renderConfirmDelete(view.branches) : null}
				{view.kind === 'rename' ? renderRename(view.branch) : null}
				{view.kind === 'confirmWorktrees' ? renderConfirmWorktrees(view.branches) : null}
				{view.kind === 'results' ? renderResults(view.response) : null}
				{view.kind === 'list' && branches.length ? (
					<div className="branch-manager-footer">
						<span className="branch-manager-note">
							{t('app.branches.selectedCount', '{count} selected').replace(
								'{count}',
								String(selectedBranches.length),
							)}
						</span>
						<Button
							disabled={busy !== '' || !mergedLocal.length}
							onClick={() => openDelete(mergedLocal)}
						>
							{t('app.branches.deleteMerged', 'Delete merged branches ({count})…').replace(
								'{count}',
								String(mergedLocal.length),
							)}
						</Button>
						<Button
							variant="danger"
							icon={<Trash2 aria-hidden="true" size={14} />}
							disabled={busy !== '' || !selectedBranches.length}
							onClick={() => openDelete(selectedBranches)}
						>
							{t('app.branches.deleteSelected', 'Delete selected…')}
						</Button>
					</div>
				) : null}
			</div>
		</Dialog>
	);
}
