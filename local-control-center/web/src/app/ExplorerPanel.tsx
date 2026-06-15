/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import {
	ChevronRight,
	ClipboardCheck,
	FileCheck2,
	FolderKanban,
	FolderOpen,
	FolderPlus,
	GitBranch,
	MessageSquarePlus,
	Settings as SettingsIcon,
	Workflow,
} from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import type { Overview, Project } from '../api/types';
import { Badge, EmptyState, StatusDot } from '../components/primitives';
import { shortId, toneForStatus } from '../lib/format';
import { EXPLORER_GROUPS, EXPLORER_LINKS, EXPLORER_TITLE, pickLabel } from './navigation';
import type { AreaId, ExplorerLink, PageId } from './navigation';

type Tone = 'ok' | 'warn' | 'danger' | 'info';
type RunStatus = Overview['workflows'][number]['status'];
type StackChip = { id: string; label: string };

/** Runs in any of these statuses are finished, not "active". Derived from the
 *  generated WorkflowRecord status union; unknown/new statuses are treated as
 *  active (fail-open) so in-flight work is never silently hidden from view. */
const TERMINAL_RUN_STATUSES = new Set<RunStatus>(['completed', 'failed', 'cancelled', 'promoted_to_branch', 'pr_created']);

/** Verdicts that mark an evidence package as blocking — drives the danger tone. */
const BLOCKING_VERDICTS = ['failed', 'blocked', 'security_blocked', 'devops_blocked'];

/** Most rows we list per collapsible section before deferring to "Show all". */
const SECTION_ROW_CAP = 8;

const SECTION_STORAGE_KEY = 'aido:explorer:sections';

function readStoredSections(): Record<string, boolean> {
	try {
		const raw = window.localStorage.getItem(SECTION_STORAGE_KEY);
		const parsed = raw ? JSON.parse(raw) : null;
		return parsed && typeof parsed === 'object' ? (parsed as Record<string, boolean>) : {};
	} catch {
		return {};
	}
}

function persistSections(sections: Record<string, boolean>) {
	try {
		window.localStorage.setItem(SECTION_STORAGE_KEY, JSON.stringify(sections));
	} catch {
		// localStorage is optional in restricted browser contexts.
	}
}

/** Reads the detected tech stack from project discovery metadata, defensively.
 *  `metadata` is an untyped JsonObject and may be absent on externally-created
 *  projects; degrade detectedRuntimes -> templateId -> [] (caller shows a hint). */
function readDetectedStack(project: Project): StackChip[] {
	const metadata = (project.metadata ?? {}) as Record<string, unknown>;
	const runtimes = metadata.detectedRuntimes;
	if (Array.isArray(runtimes)) {
		const chips: StackChip[] = [];
		for (const entry of runtimes) {
			if (entry && typeof entry === 'object') {
				const record = entry as Record<string, unknown>;
				const label = String(record.label ?? record.id ?? '').trim();
				if (label) chips.push({ id: String(record.id ?? label), label });
			}
		}
		if (chips.length) return chips;
	}
	const template = String(project.templateId ?? '').trim();
	return template && template !== 'other' ? [{ id: template, label: template }] : [];
}

function ProjectSection({
	id,
	title,
	count,
	tone,
	open,
	onToggle,
	emptyLabel,
	children,
}: {
	id: string;
	title: string;
	count: number;
	tone?: Tone;
	open: boolean;
	onToggle: () => void;
	emptyLabel: string;
	children: ReactNode;
}) {
	const bodyId = `explorer-section-${id}`;
	return (
		<div className="explorer-section" role="group" aria-label={title}>
			<button
				type="button"
				className="nav-group-trigger"
				aria-expanded={open}
				aria-controls={bodyId}
				aria-label={`${title} (${count})`}
				onClick={onToggle}
			>
				<ChevronRight className="nav-group-caret" aria-hidden="true" size={16} />
				<span>{title}</span>
				<span className="badge nav-group-count" data-tone={tone} aria-hidden="true">
					{count}
				</span>
			</button>
			<div id={bodyId} className={open ? 'nav-sublist' : 'nav-sublist nav-sublist--collapsed'}>
				{count ? children : <EmptyState title={emptyLabel} body="" />}
			</div>
		</div>
	);
}

export function ExplorerPanel({
	activeArea,
	page,
	language,
	overview,
	selectedProject,
	onNavigate,
	onSelectProject,
	onCreateProject,
}: {
	activeArea: AreaId;
	page: PageId;
	language: string;
	overview: Overview;
	selectedProject: Project | null;
	onNavigate: (page: PageId) => void;
	onSelectProject: (projectId: string) => void;
	onCreateProject: () => void;
}) {
	const links = EXPLORER_LINKS[activeArea];
	const groups = EXPLORER_GROUPS[activeArea];
	const title = pickLabel(EXPLORER_TITLE[activeArea], language);
	const lang = (en: string, es: string) => (language === 'es' ? es : en);

	const renderLink = (link: ExplorerLink) => {
		const Icon = link.icon;
		const label = pickLabel(link.label, language);
		return (
			<button
				key={link.page}
				className="nav-item"
				type="button"
				aria-label={label}
				aria-current={page === link.page ? 'page' : undefined}
				onClick={() => onNavigate(link.page)}
			>
				<Icon aria-hidden="true" size={16} />
				<span>{label}</span>
			</button>
		);
	};

	const activeProjects = useMemo(
		() => overview.projects.filter((project) => String(project.status ?? 'active') === 'active'),
		[overview.projects],
	);

	const scope = useMemo(() => {
		if (!selectedProject) return null;
		const id = selectedProject.id;
		return {
			runs: overview.workflows.filter((run) => run.projectId === id && !TERMINAL_RUN_STATUSES.has(run.status)),
			approvals: overview.actionRequests.filter((item) => item.projectId === id && item.status === 'pending'),
			workspaces: overview.runtimeWorkspaces.filter((item) => item.projectId === id),
			evidence: overview.evidencePackages.filter((item) => item.projectId === id),
		};
	}, [overview.workflows, overview.actionRequests, overview.runtimeWorkspaces, overview.evidencePackages, selectedProject]);

	const stack = useMemo(() => (selectedProject ? readDetectedStack(selectedProject) : []), [selectedProject]);

	const [openSections, setOpenSections] = useState<Record<string, boolean>>(readStoredSections);
	const toggleSection = useCallback((id: string, fallbackOpen: boolean) => {
		setOpenSections((current) => {
			const isOpen = current[id] ?? fallbackOpen;
			const next = { ...current, [id]: !isOpen };
			persistSections(next);
			return next;
		});
	}, []);
	const sectionOpen = (id: string, fallbackOpen: boolean) => openSections[id] ?? fallbackOpen;

	const evidenceBlocking =
		scope?.evidence.some((item) => BLOCKING_VERDICTS.includes(String(item.qaVerdict ?? ''))) ?? false;

	const showAll = (count: number) => lang(`Show all (${count})`, `Ver todas (${count})`);

	return (
		<aside className="explorer-panel" aria-label="Explorer">
			<div className="explorer-top">
				<div className="explorer-header">
					<div className="explorer-title">{title}</div>
					{activeArea === 'home' ? (
						<button className="button explorer-action" type="button" onClick={onCreateProject}>
							<FolderPlus aria-hidden="true" size={15} />
							{lang('New project', 'Nuevo proyecto')}
						</button>
					) : null}
				</div>

				{selectedProject ? (
					<section className="explorer-project" aria-label={lang('Current project', 'Proyecto actual')}>
						<div className="workspace-root-card">
							<span>{lang('Project', 'Proyecto')}</span>
							<strong>{selectedProject.name}</strong>
							<strong className="mono" title={String(selectedProject.path ?? '')}>
								{String(selectedProject.path ?? selectedProject.id)}
							</strong>
							<div className="workspace-root-meta">
								<Badge tone={toneForStatus(String(selectedProject.status ?? 'active'))}>
									{String(selectedProject.status ?? 'active')}
								</Badge>
								{stack.length ? (
									stack.slice(0, 3).map((chip) => (
										<Badge key={chip.id} tone="info">
											{chip.label}
										</Badge>
									))
								) : (
									<Badge tone="info">{lang('Stack not detected', 'Stack no detectado')}</Badge>
								)}
								{stack.length > 3 ? <Badge tone="info">{`+${stack.length - 3}`}</Badge> : null}
							</div>
						</div>

						<div className="explorer-actions">
							<button className="button primary" type="button" onClick={() => onNavigate('workbench')}>
								<MessageSquarePlus aria-hidden="true" size={15} />
								{lang('New task', 'Nueva tarea')}
							</button>
							<button className="button" type="button" onClick={onCreateProject}>
								<FolderOpen aria-hidden="true" size={15} />
								{lang('Open folder', 'Abrir carpeta')}
							</button>
							<button className="button" type="button" onClick={() => onNavigate('jobs')}>
								<ClipboardCheck aria-hidden="true" size={15} />
								{lang('Review', 'Revisar')}
							</button>
							<button className="button" type="button" onClick={() => onNavigate('settings-project')}>
								<SettingsIcon aria-hidden="true" size={15} />
								{lang('Settings', 'Configuración')}
							</button>
						</div>
					</section>
				) : (
					<div className="empty-state-action">
						<EmptyState
							title={lang('No project selected', 'Sin proyecto seleccionado')}
							body={lang(
								'Open or pick a project to keep its context in view.',
								'Abre o elige un proyecto para mantener su contexto a la vista.',
							)}
						/>
						<button className="button" type="button" onClick={onCreateProject}>
							<FolderOpen aria-hidden="true" size={15} />
							{lang('Open folder', 'Abrir carpeta')}
						</button>
					</div>
				)}
			</div>

			<nav className="nav-list ide-nav" aria-label="Explorer navigation">
				{scope ? (
					<>
						<ProjectSection
							id="runs"
							title={lang('Active runs', 'Ejecuciones activas')}
							count={scope.runs.length}
							tone={scope.runs.length ? 'info' : undefined}
							open={sectionOpen('runs', scope.runs.length > 0)}
							onToggle={() => toggleSection('runs', scope.runs.length > 0)}
							emptyLabel={lang('No active runs', 'Sin ejecuciones activas')}
						>
							{scope.runs.slice(0, SECTION_ROW_CAP).map((run) => (
								<button key={run.id} className="nav-item" type="button" onClick={() => onNavigate('workflows')}>
									<Workflow aria-hidden="true" size={16} />
									<span>{String(run.title ?? run.id)}</span>
									<span className="nav-item-meta">
										<StatusDot tone={toneForStatus(String(run.status))} /> {String(run.status)}
									</span>
								</button>
							))}
							{scope.runs.length > SECTION_ROW_CAP ? (
								<button className="nav-item" type="button" onClick={() => onNavigate('workflows')}>
									<ChevronRight aria-hidden="true" size={16} />
									<span>{showAll(scope.runs.length)}</span>
								</button>
							) : null}
						</ProjectSection>

						<ProjectSection
							id="approvals"
							title={lang('Approvals', 'Aprobaciones')}
							count={scope.approvals.length}
							tone={scope.approvals.length ? 'warn' : undefined}
							open={sectionOpen('approvals', scope.approvals.length > 0)}
							onToggle={() => toggleSection('approvals', scope.approvals.length > 0)}
							emptyLabel={lang('No pending approvals', 'Sin aprobaciones pendientes')}
						>
							{scope.approvals.slice(0, SECTION_ROW_CAP).map((request) => (
								<button key={request.id} className="nav-item" type="button" onClick={() => onNavigate('jobs')}>
									<ClipboardCheck aria-hidden="true" size={16} />
									<span className="mono">{String(request.actionType ?? request.id)}</span>
									<span className="nav-item-meta">
										<StatusDot tone={toneForStatus(String(request.riskLevel))} /> {String(request.riskLevel)}
									</span>
								</button>
							))}
							{scope.approvals.length > SECTION_ROW_CAP ? (
								<button className="nav-item" type="button" onClick={() => onNavigate('jobs')}>
									<ChevronRight aria-hidden="true" size={16} />
									<span>{showAll(scope.approvals.length)}</span>
								</button>
							) : null}
						</ProjectSection>

						<ProjectSection
							id="workspaces"
							title={lang('Workspaces', 'Workspaces')}
							count={scope.workspaces.length}
							tone={scope.workspaces.length ? 'info' : undefined}
							open={sectionOpen('workspaces', false)}
							onToggle={() => toggleSection('workspaces', false)}
							emptyLabel={lang('No workspaces', 'Sin workspaces')}
						>
							{scope.workspaces.slice(0, SECTION_ROW_CAP).map((workspace) => (
								<button key={workspace.id} className="nav-item" type="button" onClick={() => onNavigate('workspaces')}>
									<GitBranch aria-hidden="true" size={16} />
									<span className="mono">{shortId(String(workspace.taskId ?? workspace.id))}</span>
									<span className="nav-item-meta">{String(workspace.isolationType ?? workspace.status ?? '')}</span>
								</button>
							))}
							{scope.workspaces.length > SECTION_ROW_CAP ? (
								<button className="nav-item" type="button" onClick={() => onNavigate('workspaces')}>
									<ChevronRight aria-hidden="true" size={16} />
									<span>{showAll(scope.workspaces.length)}</span>
								</button>
							) : null}
						</ProjectSection>

						<ProjectSection
							id="evidence"
							title={lang('Evidence', 'Evidencia')}
							count={scope.evidence.length}
							tone={evidenceBlocking ? 'danger' : scope.evidence.length ? 'info' : undefined}
							open={sectionOpen('evidence', false)}
							onToggle={() => toggleSection('evidence', false)}
							emptyLabel={lang('No evidence packages', 'Sin paquetes de evidencia')}
						>
							{scope.evidence.slice(0, SECTION_ROW_CAP).map((item) => (
								<button key={item.id} className="nav-item" type="button" onClick={() => onNavigate('evidence')}>
									<FileCheck2 aria-hidden="true" size={16} />
									<span className="mono">{shortId(String(item.taskId ?? item.id))}</span>
									<span className="nav-item-meta">
										<StatusDot tone={toneForStatus(String(item.qaVerdict ?? ''))} /> {String(item.qaVerdict ?? 'unknown')}
									</span>
								</button>
							))}
							{scope.evidence.length > SECTION_ROW_CAP ? (
								<button className="nav-item" type="button" onClick={() => onNavigate('evidence')}>
									<ChevronRight aria-hidden="true" size={16} />
									<span>{showAll(scope.evidence.length)}</span>
								</button>
							) : null}
						</ProjectSection>

						<div className="explorer-section">
							<div className="nav-section-label">{lang('Project files', 'Archivos del proyecto')}</div>
							<div className="empty-state-action">
								<EmptyState
									title={lang('Project files unavailable', 'Archivos del proyecto no disponibles')}
									body={lang(
										'This control plane does not index project files yet. Open the folder to browse it in your OS file manager.',
										'Este panel aún no indexa archivos del proyecto. Abre la carpeta para explorarla en tu administrador de archivos.',
									)}
								/>
								<button className="button explorer-action" type="button" onClick={onCreateProject}>
									<FolderOpen aria-hidden="true" size={15} />
									{lang('Open folder', 'Abrir carpeta')}
								</button>
							</div>
						</div>
					</>
				) : null}

				{groups ? (
					groups.map((group) => (
						<div className="explorer-section" key={pickLabel(group.label, 'en')}>
							<div className="nav-section-label">{pickLabel(group.label, language)}</div>
							{group.links.map(renderLink)}
						</div>
					))
				) : (
					<div className="explorer-section">
						<div className="nav-section-label">{lang('Navigate', 'Navegar')}</div>
						{links.map(renderLink)}
					</div>
				)}

				{activeArea === 'home' && activeProjects.length ? (
					<div className="explorer-section">
						<div className="nav-section-label">{lang('Switch project', 'Cambiar proyecto')}</div>
						{activeProjects.slice(0, 8).map((project) => (
							<button
								key={project.id}
								className="nav-item"
								type="button"
								aria-label={project.name}
								aria-current={selectedProject?.id === project.id ? 'page' : undefined}
								onClick={() => {
									onSelectProject(project.id);
									onNavigate('projects-active');
								}}
							>
								<FolderKanban aria-hidden="true" size={16} />
								<span>{project.name}</span>
							</button>
						))}
					</div>
				) : null}
			</nav>
		</aside>
	);
}
