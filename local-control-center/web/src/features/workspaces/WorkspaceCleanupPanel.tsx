/**
 * Confirmed cleanup of orphan runtime workspaces: terminated worktrees accumulate in the
 * real project repository and slow every git status down, so this panel builds a read-only
 * plan, lets the operator review each candidate, and only deletes after an explicit
 * confirmation dialog. Uncommitted changes in removed worktrees are discarded; committed
 * work stays on its branch unless branch deletion is opted in.
 * @author Rodrigo Mason
 */
import { useCallback, useMemo, useState } from 'react';

import {
	applyWorkspaceCleanup,
	getWorkspaceCleanupPlan,
	type WorkspaceCleanupApplyResponse,
	type WorkspaceCleanupPlanResponse,
} from '../../api/client';
import type { Overview } from '../../api/types';
import type { Mutate } from '../../app/routes';
import {
	Button,
	Checkbox,
	Dialog,
	EmptyState,
	SelectField,
	StatusChip,
	Surface,
	useToast,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';

/** Reason slugs the backend emits, mapped to human copy in `reasonLabel`. */
const REASON_TONES: Record<string, 'warn' | 'danger' | 'info'> = {
	thread_archived: 'info',
	thread_deleted: 'warn',
	owner_missing: 'warn',
	workflow_finished: 'info',
	path_missing: 'danger',
};

export function WorkspaceCleanupPanel({
	overview,
	mutate,
}: {
	overview: Overview;
	mutate: Mutate;
}) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [projectId, setProjectId] = useState(overview.projects[0]?.id ?? '');
	const [plan, setPlan] = useState<WorkspaceCleanupPlanResponse | null>(null);
	const [planError, setPlanError] = useState('');
	const [loadingPlan, setLoadingPlan] = useState(false);
	const [selectedWorkspaces, setSelectedWorkspaces] = useState<ReadonlySet<string>>(new Set());
	const [selectedOrphans, setSelectedOrphans] = useState<ReadonlySet<string>>(new Set());
	const [deleteBranches, setDeleteBranches] = useState(false);
	const [confirmOpen, setConfirmOpen] = useState(false);
	const [applying, setApplying] = useState(false);

	const reasonLabel = useMemo(
		() =>
			({
				thread_archived: t('app.workspaceCleanup.reason.threadArchived', 'Thread archived'),
				thread_deleted: t('app.workspaceCleanup.reason.threadDeleted', 'Thread deleted'),
				owner_missing: t('app.workspaceCleanup.reason.ownerMissing', 'Owner missing'),
				workflow_finished: t('app.workspaceCleanup.reason.workflowFinished', 'Workflow finished'),
				path_missing: t('app.workspaceCleanup.reason.pathMissing', 'Path missing'),
			}) as Record<string, string>,
		[t],
	);

	const loadPlan = useCallback(async (targetProjectId: string) => {
		if (!targetProjectId) return;
		setLoadingPlan(true);
		setPlanError('');
		try {
			const next = await getWorkspaceCleanupPlan(targetProjectId);
			setPlan(next);
			setSelectedWorkspaces(new Set(next.candidates.map((item) => item.workspaceId)));
			setSelectedOrphans(new Set(next.repoOrphans.map((item) => item.path)));
		} catch (error) {
			setPlan(null);
			setPlanError(error instanceof Error ? error.message : String(error));
		} finally {
			setLoadingPlan(false);
		}
	}, []);

	const toggleWorkspace = (workspaceId: string) => {
		setSelectedWorkspaces((current) => {
			const next = new Set(current);
			if (next.has(workspaceId)) next.delete(workspaceId);
			else next.add(workspaceId);
			return next;
		});
	};

	const toggleOrphan = (path: string) => {
		setSelectedOrphans((current) => {
			const next = new Set(current);
			if (next.has(path)) next.delete(path);
			else next.add(path);
			return next;
		});
	};

	const selectionCount = selectedWorkspaces.size + selectedOrphans.size;

	const applySelection = async () => {
		if (!plan || selectionCount === 0) return;
		setApplying(true);
		try {
			const result: WorkspaceCleanupApplyResponse = await mutate((token) =>
				applyWorkspaceCleanup(token, plan.projectId, {
					workspaceIds: [...selectedWorkspaces],
					orphanWorktreePaths: [...selectedOrphans],
					deleteBranches,
					reason: 'Operator-confirmed cleanup from the Workspaces panel.',
				}),
			);
			setConfirmOpen(false);
			const cleanedCount = result.summary.archivedCount + result.summary.orphanRemovedCount;
			const skippedCount = result.summary.skippedCount + result.summary.orphanRefusedCount;
			notify({
				title: t('app.workspaceCleanup.toast.applied', 'Workspace cleanup applied'),
				body: `${cleanedCount} ${t('app.workspaceCleanup.toast.cleaned', 'cleaned')} · ${skippedCount} ${t('app.workspaceCleanup.toast.skipped', 'skipped')}`,
				tone: 'ok',
			});
			await loadPlan(plan.projectId);
		} catch (error) {
			notify({
				title: t('app.workspaceCleanup.toast.failed', 'Workspace cleanup failed'),
				body: error instanceof Error ? error.message : String(error),
				tone: 'danger',
			});
		} finally {
			setApplying(false);
		}
	};

	return (
		<Surface title={t('app.workspaceCleanup.title', 'Workspace cleanup')}>
			<div className="stack">
				<p className="muted">
					{t(
						'app.workspaceCleanup.summary',
						'Terminated runtime worktrees pile up in the real repository and slow every git status down. Build the plan, review each orphan candidate and confirm before anything is deleted.',
					)}
				</p>
				<div className="surface-toolbar">
					<SelectField
						label={t('ui.static.project.f6f4da8d', 'Project')}
						value={projectId}
						onChange={(event) => setProjectId(event.target.value)}
					>
						{overview.projects.map((project) => (
							<option key={project.id} value={project.id}>
								{project.name}
							</option>
						))}
					</SelectField>
					<Button
						variant="primary"
						loading={loadingPlan}
						disabled={!projectId}
						onClick={() => void loadPlan(projectId)}
					>
						{t('app.workspaceCleanup.analyze', 'Analyze candidates')}
					</Button>
				</div>
				{planError ? <p className="form-error">{planError}</p> : null}
				{plan ? (
					<>
						<div className="inline">
							<StatusChip tone="info">
								{`${plan.summary.activeWorkspaceCount} ${t('app.workspaceCleanup.chip.active', 'active workspaces')}`}
							</StatusChip>
							<StatusChip tone={plan.summary.candidateCount > 0 ? 'warn' : 'ok'}>
								{`${plan.summary.candidateCount} ${t('app.workspaceCleanup.chip.candidates', 'cleanup candidates')}`}
							</StatusChip>
							<StatusChip tone={plan.summary.repoOrphanCount > 0 ? 'warn' : 'ok'}>
								{`${plan.summary.repoOrphanCount} ${t('app.workspaceCleanup.chip.orphans', 'orphan worktrees')}`}
							</StatusChip>
						</div>
						{plan.candidates.length === 0 && plan.repoOrphans.length === 0 ? (
							<EmptyState
								title={t('app.workspaceCleanup.empty.title', 'Nothing to clean up')}
								body={t(
									'app.workspaceCleanup.empty.body',
									'Every active workspace still belongs to a live thread, workflow or run.',
								)}
							/>
						) : (
							<>
								{plan.candidates.map((candidate) => (
									<Checkbox
										key={candidate.workspaceId}
										checked={selectedWorkspaces.has(candidate.workspaceId)}
										onChange={() => toggleWorkspace(candidate.workspaceId)}
										label={
											<span className="inline">
												<StatusChip tone={REASON_TONES[candidate.reason] ?? 'warn'}>
													{reasonLabel[candidate.reason] ?? candidate.reason}
												</StatusChip>
												<span className="mono">{candidate.branch ?? candidate.taskId}</span>
												{candidate.threadTitle ? <span>{candidate.threadTitle}</span> : null}
											</span>
										}
										help={<span className="mono">{candidate.path}</span>}
									/>
								))}
								{plan.repoOrphans.length > 0 ? (
									<>
										<h3>
											{t(
												'app.workspaceCleanup.orphans.title',
												'Orphan worktrees without an active workspace row',
											)}
										</h3>
										{plan.repoOrphans.map((orphan) => (
											<Checkbox
												key={orphan.path}
												checked={selectedOrphans.has(orphan.path)}
												onChange={() => toggleOrphan(orphan.path)}
												label={<span className="mono">{orphan.branch ?? orphan.path}</span>}
												help={<span className="mono">{orphan.path}</span>}
											/>
										))}
									</>
								) : null}
								<Checkbox
									checked={deleteBranches}
									onChange={(event) => setDeleteBranches(event.target.checked)}
									label={t(
										'app.workspaceCleanup.deleteBranches',
										'Also delete the work branches (git branch -D)',
									)}
									help={t(
										'app.workspaceCleanup.deleteBranches.help',
										'Branches keep committed work recoverable; delete them only when those refs are no longer needed.',
									)}
								/>
								<div className="surface-toolbar">
									<Button
										variant="danger"
										disabled={selectionCount === 0}
										onClick={() => setConfirmOpen(true)}
									>
										{t('app.workspaceCleanup.apply', 'Clean up selected')}
									</Button>
								</div>
							</>
						)}
					</>
				) : null}
			</div>
			<Dialog
				open={confirmOpen}
				onClose={() => setConfirmOpen(false)}
				label={t('app.workspaceCleanup.confirm.title', 'Confirm workspace cleanup')}
			>
				<div className="stack">
					<p>
						{t(
							'app.workspaceCleanup.confirm.body',
							'The selected workspaces will be archived and their worktrees removed from the project repository.',
						)}
					</p>
					<p className="form-error">
						{t(
							'app.workspaceCleanup.confirm.warning',
							'Uncommitted changes inside the removed worktrees are discarded permanently. Committed work stays on its branch unless branch deletion is enabled.',
						)}
					</p>
					<p className="mono">
						{`${selectedWorkspaces.size} ${t('app.workspaceCleanup.chip.candidates', 'cleanup candidates')} · ${selectedOrphans.size} ${t('app.workspaceCleanup.chip.orphans', 'orphan worktrees')}`}
					</p>
					<div className="surface-toolbar">
						<Button onClick={() => setConfirmOpen(false)}>
							{t('app.workspaceCleanup.confirm.cancel', 'Cancel')}
						</Button>
						<Button variant="danger" loading={applying} onClick={() => void applySelection()}>
							{t('app.workspaceCleanup.confirm.apply', 'Confirm cleanup')}
						</Button>
					</div>
				</div>
			</Dialog>
		</Surface>
	);
}
