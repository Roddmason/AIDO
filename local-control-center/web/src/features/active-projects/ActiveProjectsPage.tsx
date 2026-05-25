import { FolderPlus, Settings } from 'lucide-react';

import type { Overview, Project, RuntimeProviders } from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, StatusDot, Surface } from '../../components/primitives';
import { countByStatus, shortId, toneForStatus } from '../../lib/format';

export type ProjectStatusView = 'active' | 'finished' | 'error' | 'cancelled';
export type Language = 'en' | 'es';

const statusCopy: Record<Language, Record<ProjectStatusView, { title: string; kicker: string; summary: string; emptyTitle: string; emptyBody: string }>> = {
	en: {
		active: {
			kicker: 'Operational ledger',
			title: 'Active Projects',
			summary: 'Only active projects can receive operational work here. Inactive and archived project records stay visible in Settings for audit and configuration.',
			emptyTitle: 'No active projects',
			emptyBody: 'Create a project or reactivate one in Settings before starting workflows, jobs, workspaces or governance records.',
		},
		finished: {
			kicker: 'Delivery ledger',
			title: 'Finished Projects',
			summary: 'Completed and archived projects stay available for audit without being selectable for new operational mutations.',
			emptyTitle: 'No finished projects',
			emptyBody: 'Projects with completed, finished, done, finalized or archived status will appear here.',
		},
		error: {
			kicker: 'Exception ledger',
			title: 'Projects With Error',
			summary: 'Projects in failed or error states are isolated here so operators can triage without mixing them into active work.',
			emptyTitle: 'No projects with error',
			emptyBody: 'Projects with failed, error or errored status will appear here.',
		},
		cancelled: {
			kicker: 'Closed ledger',
			title: 'Cancelled Projects',
			summary: 'Cancelled projects are visible for traceability, but cannot be selected as the operational mutation target.',
			emptyTitle: 'No cancelled projects',
			emptyBody: 'Projects with cancelled or canceled status will appear here.',
		},
	},
	es: {
		active: {
			kicker: 'Registro operacional',
			title: 'Proyectos activos',
			summary: 'Solo los proyectos activos pueden recibir trabajo operacional. Los registros inactivos siguen auditables en Configuraciones.',
			emptyTitle: 'No hay proyectos activos',
			emptyBody: 'Crea un proyecto o reactiva uno en Configuraciones antes de iniciar flujos, trabajos o registros de gobierno.',
		},
		finished: {
			kicker: 'Registro de entrega',
			title: 'Proyectos finalizados',
			summary: 'Los proyectos completados y archivados quedan disponibles para auditoria sin ser seleccionables para nuevas mutaciones.',
			emptyTitle: 'No hay proyectos finalizados',
			emptyBody: 'Apareceran proyectos con estado completed, finished, done, finalized o archived.',
		},
		error: {
			kicker: 'Registro de excepciones',
			title: 'Proyectos con error',
			summary: 'Los proyectos con error quedan aislados para triage sin mezclarse con el trabajo activo.',
			emptyTitle: 'No hay proyectos con error',
			emptyBody: 'Apareceran proyectos con estado failed, error o errored.',
		},
		cancelled: {
			kicker: 'Registro cerrado',
			title: 'Proyectos cancelados',
			summary: 'Los proyectos cancelados se mantienen visibles para trazabilidad, pero no son objetivo operacional.',
			emptyTitle: 'No hay proyectos cancelados',
			emptyBody: 'Apareceran proyectos con estado cancelled o canceled.',
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
	runtimeProviders,
	selectedProject,
	statusView = 'active',
	language = 'en',
	onSelectProject,
	onCreateProject,
	onOpenSettings,
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	selectedProject: Project | null;
	statusView?: ProjectStatusView;
	language?: Language;
	onSelectProject: (projectId: string) => void;
	onCreateProject: () => void;
	onOpenSettings: () => void;
}) {
	const copy = statusCopy[language][statusView];
	const activeProjects = overview.projects.filter((project) => project.status === 'active');
	const visibleProjects = overview.projects.filter((project) => matchesProjectStatus(project.status, statusView));
	const pendingApprovals = overview.actionRequests.filter((item) => item.status === 'pending').length;
	const runningJobs = countByStatus(overview.jobs, 'running');
	const projectWorkspaces = (projectId: string) => overview.runtimeWorkspaces.filter((workspace) => workspace.projectId === projectId);
	const projectJobs = (projectId: string) => overview.jobs.filter((job) => job.projectId === projectId);
	const projectEvents = (projectId: string) => overview.events.filter((event) => event.projectId === projectId);

	return (
		<>
			<PageHeader
				kicker={copy.kicker}
				title={copy.title}
				summary={copy.summary}
			/>
			<div className="grid metrics">
				<Surface>
					<div className="inline"><StatusDot tone={activeProjects.length ? 'ok' : 'warn'} /> Active projects</div>
					<div className="metric-value">{activeProjects.length}</div>
					<div className="metric-label">{overview.projects.length} total catalog records</div>
				</Surface>
				<Surface>
					<div className="inline"><StatusDot tone={runningJobs ? 'warn' : 'ok'} /> Running jobs</div>
					<div className="metric-value">{runningJobs}</div>
					<div className="metric-label">across active lanes</div>
				</Surface>
				<Surface>
					<div className="inline"><StatusDot tone={pendingApprovals ? 'warn' : 'ok'} /> Pending approvals</div>
					<div className="metric-value">{pendingApprovals}</div>
					<div className="metric-label">granular action requests</div>
				</Surface>
				<Surface>
					<div className="inline"><StatusDot tone={runtimeProviders?.ollama.available ? 'ok' : 'warn'} /> Local runtime</div>
					<div className="metric-value">{runtimeProviders?.ollama.available ? 'ready' : 'off'}</div>
					<div className="metric-label">Ollama is optional</div>
				</Surface>
			</div>
			<Surface title={statusView === 'active' ? 'Active project register' : 'Project register'}>
				<div className="surface-toolbar">
					<div className="inline">
						<Badge tone={selectedProject ? 'ok' : 'warn'}>{selectedProject ? `selected ${selectedProject.name}` : 'no operational project'}</Badge>
					</div>
					<div className="inline">
						<button className="button" type="button" onClick={onOpenSettings}>
							<Settings aria-hidden="true" size={16} />
							Project settings
						</button>
						<button className="button primary" type="button" onClick={onCreateProject}>
							<FolderPlus aria-hidden="true" size={16} />
							New project
						</button>
					</div>
				</div>
				<DataTable
					rows={visibleProjects}
					empty={
						<EmptyState
							title={copy.emptyTitle}
							body={copy.emptyBody}
						/>
					}
					columns={[
						{
							key: 'name',
							label: 'Project',
							render: (project) => (
								<div className="stack compact">
									<strong>{project.name}</strong>
									<span className="mono muted">{shortId(project.id)}</span>
								</div>
							),
						},
						{ key: 'path', label: 'Path', render: (project) => <span className="mono">{project.path}</span> },
						{ key: 'status', label: 'Status', render: (project) => <Badge tone="ok">{project.status}</Badge> },
						{
							key: 'work',
							label: 'Work',
							render: (project) => {
								const jobs = projectJobs(project.id);
								const workspaces = projectWorkspaces(project.id);
								return `${jobs.length} jobs / ${workspaces.length} workspaces`;
							},
						},
						{
							key: 'lastEvent',
							label: 'Last event',
							render: (project) => {
								const event = projectEvents(project.id)[0];
								return event ? <span className="mono">{event.type}</span> : <span className="muted">none</span>;
							},
						},
						{
							key: 'posture',
							label: 'Posture',
							render: (project) => {
								const hasPendingApproval = overview.actionRequests.some((item) => item.projectId === project.id && item.status === 'pending');
								return <Badge tone={hasPendingApproval ? 'warn' : 'ok'}>{hasPendingApproval ? 'approval' : 'clear'}</Badge>;
							},
						},
						{
							key: 'select',
							label: 'Action',
							render: (project) => (
								<button className="button" type="button" disabled={statusView !== 'active' || selectedProject?.id === project.id} onClick={() => onSelectProject(project.id)}>
									{statusView !== 'active' ? 'Audit only' : selectedProject?.id === project.id ? 'Selected' : 'Select'}
								</button>
							),
						},
					]}
				/>
			</Surface>
			<div className="grid two">
				<Surface title="Recent approvals">
					<DataTable
						rows={overview.actionRequests.filter((item) => item.status === 'pending').slice(0, 5)}
						empty={<EmptyState title="No pending approvals" body="Risky actions stop in the approval queue before execution." />}
						columns={[
							{ key: 'action', label: 'Action', render: (row) => <span className="mono">{row.actionType}</span> },
							{ key: 'risk', label: 'Risk', render: (row) => <Badge tone={toneForStatus(row.riskLevel)}>{row.riskLevel}</Badge> },
							{ key: 'project', label: 'Project', render: (row) => <span className="mono">{shortId(row.projectId)}</span> },
						]}
					/>
				</Surface>
				<Surface title="Workspace posture">
					<DataTable
						rows={overview.runtimeWorkspaces.filter((workspace) => workspace.status === 'active').slice(0, 5)}
						empty={<EmptyState title="No active workspaces" body="Workflow implementation steps allocate isolated workspaces." />}
						columns={[
							{ key: 'task', label: 'Task', render: (row) => <span className="mono">{row.taskId}</span> },
							{ key: 'owner', label: 'Owner', render: (row) => row.ownerAgentId },
							{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
						]}
					/>
				</Surface>
			</div>
		</>
	);
}
