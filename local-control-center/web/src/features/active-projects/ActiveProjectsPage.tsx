/**
 * Project lifecycle board: one reusable page that lists projects filtered by lifecycle
 * lane (active / finished / error / cancelled), driven by `statusView`.
 * Only the active lane can select a project to run work against; other lanes stay
 * audit-only and visible for traceability. Per-lane copy lives in `statusCopy`.
 */
import { FolderPlus, Settings } from 'lucide-react';

import type { Overview, Project } from '../../api/types';
import { Badge, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { shortId, toneForStatus } from '../../lib/format';

export type ProjectStatusView = 'active' | 'finished' | 'error' | 'cancelled';
export type Language = 'en' | 'es';

type StatusCopyField = { key: string; en: string };

const statusCopy: Record<
	ProjectStatusView,
	{
		kicker: StatusCopyField;
		title: StatusCopyField;
		summary: StatusCopyField;
		emptyTitle: StatusCopyField;
		emptyBody: StatusCopyField;
	}
> = {
	active: {
		kicker: { key: 'app.activeProjects.active.kicker', en: 'Active work' },
		title: { key: 'app.activeProjects.active.title', en: 'Active Projects' },
		summary: {
			key: 'app.activeProjects.active.summary',
			en: 'Only active projects can run work here. Inactive and archived projects stay visible in Settings for audit and configuration.',
		},
		emptyTitle: { key: 'app.activeProjects.active.emptyTitle', en: 'No active projects' },
		emptyBody: {
			key: 'app.activeProjects.active.emptyBody',
			en: 'Create a project or reactivate one in Settings before starting workflows, jobs, workspaces or governance records.',
		},
	},
	finished: {
		kicker: { key: 'app.activeProjects.finished.kicker', en: 'Delivered' },
		title: { key: 'app.activeProjects.finished.title', en: 'Finished Projects' },
		summary: {
			key: 'app.activeProjects.finished.summary',
			en: 'Completed and archived projects stay available for audit and cannot be selected to run new work.',
		},
		emptyTitle: { key: 'app.activeProjects.finished.emptyTitle', en: 'No finished projects' },
		emptyBody: {
			key: 'app.activeProjects.finished.emptyBody',
			en: 'Projects with completed, finished, done, finalized or archived status will appear here.',
		},
	},
	error: {
		kicker: { key: 'app.activeProjects.error.kicker', en: 'Errors' },
		title: { key: 'app.activeProjects.error.title', en: 'Projects With Error' },
		summary: {
			key: 'app.activeProjects.error.summary',
			en: 'Projects in failed or error states are isolated here so operators can triage without mixing them into active work.',
		},
		emptyTitle: { key: 'app.activeProjects.error.emptyTitle', en: 'No projects with error' },
		emptyBody: {
			key: 'app.activeProjects.error.emptyBody',
			en: 'Projects with failed, error or errored status will appear here.',
		},
	},
	cancelled: {
		kicker: { key: 'app.activeProjects.cancelled.kicker', en: 'Cancelled' },
		title: { key: 'app.activeProjects.cancelled.title', en: 'Cancelled Projects' },
		summary: {
			key: 'app.activeProjects.cancelled.summary',
			en: 'Cancelled projects stay visible for traceability, but cannot be selected to run new work.',
		},
		emptyTitle: { key: 'app.activeProjects.cancelled.emptyTitle', en: 'No cancelled projects' },
		emptyBody: {
			key: 'app.activeProjects.cancelled.emptyBody',
			en: 'Projects with cancelled or canceled status will appear here.',
		},
	},
};

function matchesProjectStatus(status: string, view: ProjectStatusView) {
	const normalized = status.toLowerCase();
	if (view === 'active') return normalized === 'active';
	if (view === 'finished')
		return ['completed', 'complete', 'finished', 'finalized', 'done', 'archived'].includes(
			normalized,
		);
	if (view === 'error') return ['failed', 'error', 'errored'].includes(normalized);
	return ['cancelled', 'canceled'].includes(normalized);
}

/**
 * Project lane route page. `statusView` selects which lifecycle lane to render; the
 * active lane exposes Select buttons (operational), other lanes render as audit-only.
 */
export function ActiveProjectsPage({
	overview,
	selectedProject,
	statusView = 'active',
	onSelectProject,
	onCreateProject,
	onOpenSettings,
}: {
	overview: Overview;
	selectedProject: Project | null;
	statusView?: ProjectStatusView;
	language?: Language;
	onSelectProject: (projectId: string) => void;
	onCreateProject: () => void;
	onOpenSettings: () => void;
}) {
	const { t } = useI18n();
	const copy = statusCopy[statusView];
	const isActiveView = statusView === 'active';
	const visibleProjects = overview.projects.filter((project) =>
		matchesProjectStatus(project.status, statusView),
	);
	const pendingApprovals = overview.actionRequests.filter((item) => item.status === 'pending');
	const activeWorkspaces = overview.runtimeWorkspaces.filter(
		(workspace) => workspace.status === 'active',
	);
	const projectJobCount = (projectId: string) =>
		overview.jobs.filter((job) => job.projectId === projectId).length;
	const projectWorkspaceCount = (projectId: string) =>
		overview.runtimeWorkspaces.filter((workspace) => workspace.projectId === projectId).length;
	const hasPendingApproval = (projectId: string) =>
		overview.actionRequests.some(
			(item) => item.projectId === projectId && item.status === 'pending',
		);

	return (
		<>
			<PageHeader
				kicker={t(copy.kicker.key, copy.kicker.en)}
				title={t(copy.title.key, copy.title.en)}
				summary={t(copy.summary.key, copy.summary.en)}
			/>

			<div className="surface-toolbar" data-motion-item>
				<Badge tone={selectedProject ? 'ok' : 'warn'}>
					{selectedProject
						? `${t('app.activeProjects.selected', 'Selected')}: ${selectedProject.name}`
						: t('app.activeProjects.noOperationalProject', 'No operational project')}
				</Badge>
				<div className="inline">
					<button className="button" type="button" onClick={onOpenSettings}>
						<Settings aria-hidden="true" size={16} />
						{t('app.activeProjects.projectSettings', 'Project settings')}
					</button>
					<button className="button primary" type="button" onClick={onCreateProject}>
						<FolderPlus aria-hidden="true" size={16} />
						{t('app.activeProjects.newProject', 'New project')}
					</button>
				</div>
			</div>

			{visibleProjects.length ? (
				<div className="masonry-grid" data-motion-item>
					{visibleProjects.map((project) => {
						const selected = selectedProject?.id === project.id;
						const pending = hasPendingApproval(project.id);
						return (
							<article
								className="card"
								data-selected={selected ? 'true' : undefined}
								key={project.id}
							>
								<div className="card-header">
									<strong className="card-title">{project.name}</strong>
									<Badge tone={toneForStatus(project.status)}>{project.status}</Badge>
								</div>
								<span className="mono muted">{project.path}</span>
								<div className="card-meta">
									<span>
										{projectJobCount(project.id)} {t('app.activeProjects.jobs', 'jobs')}
									</span>
									<span>{projectWorkspaceCount(project.id)} workspaces</span>
									<Badge tone={pending ? 'warn' : 'ok'}>
										{pending
											? t('app.activeProjects.approval', 'approval')
											: t('app.activeProjects.clear', 'clear')}
									</Badge>
								</div>
								{isActiveView ? (
									<button
										className="button"
										type="button"
										disabled={selected}
										onClick={() => onSelectProject(project.id)}
									>
										{selected
											? t('app.activeProjects.selected', 'Selected')
											: t('app.activeProjects.select', 'Select')}
									</button>
								) : (
									<Badge>{t('app.activeProjects.auditOnly', 'Audit only')}</Badge>
								)}
							</article>
						);
					})}
				</div>
			) : (
				<EmptyState
					title={t(copy.emptyTitle.key, copy.emptyTitle.en)}
					body={t(copy.emptyBody.key, copy.emptyBody.en)}
				/>
			)}

			<div className="grid two">
				<Surface title={t('app.activeProjects.recentApprovals', 'Recent approvals')}>
					{pendingApprovals.length ? (
						<div className="stack">
							{pendingApprovals.slice(0, 5).map((item) => (
								<div className="inline" key={item.id}>
									<span className="mono">{item.actionType}</span>
									<Badge tone={toneForStatus(item.riskLevel)}>{item.riskLevel}</Badge>
									<span className="mono muted">{shortId(item.projectId)}</span>
								</div>
							))}
						</div>
					) : (
						<EmptyState
							title={t('app.activeProjects.noPendingApprovals', 'No pending approvals')}
							body={t(
								'app.activeProjects.noPendingApprovalsBody',
								'Risky actions stop in the approval queue before execution.',
							)}
						/>
					)}
				</Surface>
				<Surface title={t('app.activeProjects.workspaceStatus', 'Workspace status')}>
					{activeWorkspaces.length ? (
						<div className="stack">
							{activeWorkspaces.slice(0, 5).map((workspace) => (
								<div className="inline" key={workspace.id}>
									<span className="mono">{workspace.taskId}</span>
									<span className="muted">{workspace.ownerAgentId}</span>
									<Badge tone={toneForStatus(workspace.status)}>{workspace.status}</Badge>
								</div>
							))}
						</div>
					) : (
						<EmptyState
							title={t('app.activeProjects.noActiveWorkspaces', 'No active workspaces')}
							body={t(
								'app.activeProjects.noActiveWorkspacesBody',
								'Workflow implementation steps allocate isolated workspaces.',
							)}
						/>
					)}
				</Surface>
			</div>
		</>
	);
}
