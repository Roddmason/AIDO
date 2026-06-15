/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { FolderPlus, Settings } from 'lucide-react';

import type { Overview, Project } from '../../api/types';
import { Badge, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { shortId, toneForStatus } from '../../lib/format';

export type ProjectStatusView = 'active' | 'finished' | 'error' | 'cancelled';
export type Language = 'en' | 'es';

const statusCopy: Record<Language, Record<ProjectStatusView, { title: string; kicker: string; summary: string; emptyTitle: string; emptyBody: string }>> = {
	en: {
		active: {
			kicker: 'Active work',
			title: 'Active Projects',
			summary: 'Only active projects can run work here. Inactive and archived projects stay visible in Settings for audit and configuration.',
			emptyTitle: 'No active projects',
			emptyBody: 'Create a project or reactivate one in Settings before starting workflows, jobs, workspaces or governance records.',
		},
		finished: {
			kicker: 'Delivered',
			title: 'Finished Projects',
			summary: 'Completed and archived projects stay available for audit and cannot be selected to run new work.',
			emptyTitle: 'No finished projects',
			emptyBody: 'Projects with completed, finished, done, finalized or archived status will appear here.',
		},
		error: {
			kicker: 'Errors',
			title: 'Projects With Error',
			summary: 'Projects in failed or error states are isolated here so operators can triage without mixing them into active work.',
			emptyTitle: 'No projects with error',
			emptyBody: 'Projects with failed, error or errored status will appear here.',
		},
		cancelled: {
			kicker: 'Cancelled',
			title: 'Cancelled Projects',
			summary: 'Cancelled projects stay visible for traceability, but cannot be selected to run new work.',
			emptyTitle: 'No cancelled projects',
			emptyBody: 'Projects with cancelled or canceled status will appear here.',
		},
	},
	es: {
		active: {
			kicker: 'Trabajo activo',
			title: 'Proyectos activos',
			summary: 'Solo los proyectos activos pueden ejecutar trabajo aquí. Los proyectos inactivos y archivados siguen visibles en Configuración para auditoría.',
			emptyTitle: 'No hay proyectos activos',
			emptyBody: 'Crea un proyecto o reactiva uno en Configuración antes de iniciar flujos de trabajo, trabajos, workspaces o registros de gobierno.',
		},
		finished: {
			kicker: 'Entregados',
			title: 'Proyectos finalizados',
			summary: 'Los proyectos completados y archivados quedan disponibles para auditoría y no pueden seleccionarse para ejecutar trabajo nuevo.',
			emptyTitle: 'No hay proyectos finalizados',
			emptyBody: 'Aparecerán proyectos con estado completed, finished, done, finalized o archived.',
		},
		error: {
			kicker: 'Errores',
			title: 'Proyectos con error',
			summary: 'Los proyectos con error quedan aislados para revisión sin mezclarse con el trabajo activo.',
			emptyTitle: 'No hay proyectos con error',
			emptyBody: 'Aparecerán proyectos con estado failed, error o errored.',
		},
		cancelled: {
			kicker: 'Cancelados',
			title: 'Proyectos cancelados',
			summary: 'Los proyectos cancelados se mantienen visibles para trazabilidad, pero no pueden seleccionarse para ejecutar trabajo nuevo.',
			emptyTitle: 'No hay proyectos cancelados',
			emptyBody: 'Aparecerán proyectos con estado cancelled o canceled.',
		},
	},
};

function matchesProjectStatus(status: string, view: ProjectStatusView) {
	const normalized = status.toLowerCase();
	if (view === 'active') return normalized === 'active';
	if (view === 'finished') return ['completed', 'complete', 'finished', 'finalized', 'done', 'archived'].includes(normalized);
	if (view === 'error') return ['failed', 'error', 'errored'].includes(normalized);
	return ['cancelled', 'canceled'].includes(normalized);
}

export function ActiveProjectsPage({
	overview,
	selectedProject,
	statusView = 'active',
	language = 'en',
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
	const copy = statusCopy[language][statusView];
	const lang = (en: string, es: string) => (language === 'es' ? es : en);
	const isActiveView = statusView === 'active';
	const visibleProjects = overview.projects.filter((project) => matchesProjectStatus(project.status, statusView));
	const pendingApprovals = overview.actionRequests.filter((item) => item.status === 'pending');
	const activeWorkspaces = overview.runtimeWorkspaces.filter((workspace) => workspace.status === 'active');
	const projectJobCount = (projectId: string) => overview.jobs.filter((job) => job.projectId === projectId).length;
	const projectWorkspaceCount = (projectId: string) => overview.runtimeWorkspaces.filter((workspace) => workspace.projectId === projectId).length;
	const hasPendingApproval = (projectId: string) => overview.actionRequests.some((item) => item.projectId === projectId && item.status === 'pending');

	return (
		<>
			<PageHeader kicker={copy.kicker} title={copy.title} summary={copy.summary} />

			<div className="surface-toolbar" data-motion-item>
				<Badge tone={selectedProject ? 'ok' : 'warn'}>
					{selectedProject ? `${lang('Selected', 'Seleccionado')}: ${selectedProject.name}` : lang('No operational project', 'Sin proyecto operativo')}
				</Badge>
				<div className="inline">
					<button className="button" type="button" onClick={onOpenSettings}>
						<Settings aria-hidden="true" size={16} />
						{lang('Project settings', 'Configuración de proyecto')}
					</button>
					<button className="button primary" type="button" onClick={onCreateProject}>
						<FolderPlus aria-hidden="true" size={16} />
						{lang('New project', 'Nuevo proyecto')}
					</button>
				</div>
			</div>

			{visibleProjects.length ? (
				<div className="masonry-grid" data-motion-item>
					{visibleProjects.map((project) => {
						const selected = selectedProject?.id === project.id;
						const pending = hasPendingApproval(project.id);
						return (
							<article className="card" data-selected={selected ? 'true' : undefined} key={project.id}>
								<div className="card-header">
									<strong className="card-title">{project.name}</strong>
									<Badge tone={toneForStatus(project.status)}>{project.status}</Badge>
								</div>
								<span className="mono muted">{project.path}</span>
								<div className="card-meta">
									<span>{projectJobCount(project.id)} {lang('jobs', 'trabajos')}</span>
									<span>{projectWorkspaceCount(project.id)} {lang('workspaces', 'workspaces')}</span>
									<Badge tone={pending ? 'warn' : 'ok'}>{pending ? lang('approval', 'aprobación') : lang('clear', 'sin pendientes')}</Badge>
								</div>
								{isActiveView ? (
									<button className="button" type="button" disabled={selected} onClick={() => onSelectProject(project.id)}>
										{selected ? lang('Selected', 'Seleccionado') : lang('Select', 'Seleccionar')}
									</button>
								) : (
									<Badge>{lang('Audit only', 'Solo auditoría')}</Badge>
								)}
							</article>
						);
					})}
				</div>
			) : (
				<EmptyState title={copy.emptyTitle} body={copy.emptyBody} />
			)}

			<div className="grid two">
				<Surface title={lang('Recent approvals', 'Aprobaciones recientes')}>
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
							title={lang('No pending approvals', 'Sin aprobaciones pendientes')}
							body={lang('Risky actions stop in the approval queue before execution.', 'Las acciones riesgosas se detienen en la cola de aprobación antes de ejecutarse.')}
						/>
					)}
				</Surface>
				<Surface title={lang('Workspace status', 'Estado de workspaces')}>
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
							title={lang('No active workspaces', 'Sin workspaces activos')}
							body={lang('Workflow implementation steps allocate isolated workspaces.', 'Los pasos de implementación asignan workspaces aislados.')}
						/>
					)}
				</Surface>
			</div>
		</>
	);
}
