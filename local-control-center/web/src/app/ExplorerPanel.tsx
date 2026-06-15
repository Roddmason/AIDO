/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { FileCheck2, FolderKanban, FolderPlus, GitBranch, Workflow } from 'lucide-react';
import { useMemo } from 'react';

import type { Overview, Project } from '../api/types';
import { shortId } from '../lib/format';
import { EXPLORER_GROUPS, EXPLORER_LINKS, EXPLORER_TITLE, pickLabel } from './navigation';
import type { AreaId, ExplorerLink, PageId } from './navigation';

type Timestamped = { updatedAt?: string; createdAt?: string };

function recent<T extends Timestamped>(rows: T[], limit = 6): T[] {
	return [...rows]
		.sort((left, right) => {
			const leftTime = Date.parse(String(left.updatedAt ?? left.createdAt ?? ''));
			const rightTime = Date.parse(String(right.updatedAt ?? right.createdAt ?? ''));
			return (Number.isNaN(rightTime) ? 0 : rightTime) - (Number.isNaN(leftTime) ? 0 : leftTime);
		})
		.slice(0, limit);
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
	const recentRuns = useMemo(() => recent(overview.workflows), [overview.workflows]);
	const recentEvidence = useMemo(() => recent(overview.evidencePackages), [overview.evidencePackages]);
	const recentWorkspaces = useMemo(() => recent(overview.runtimeWorkspaces), [overview.runtimeWorkspaces]);

	return (
		<aside className="explorer-panel" aria-label="Explorer">
			<div className="explorer-header">
				<div className="explorer-title">{title}</div>
				{activeArea === 'home' ? (
					<button className="button explorer-action" type="button" onClick={onCreateProject}>
						<FolderPlus aria-hidden="true" size={15} />
						{lang('New project', 'Nuevo proyecto')}
					</button>
				) : null}
			</div>

			<nav className="nav-list ide-nav" aria-label="Explorer navigation">
				{groups
					? groups.map((group) => (
							<div className="explorer-section" key={pickLabel(group.label, 'en')}>
								<div className="nav-section-label">{pickLabel(group.label, language)}</div>
								{group.links.map(renderLink)}
							</div>
						))
					: links.map(renderLink)}

				{activeArea === 'home' && activeProjects.length ? (
					<div className="explorer-section">
						<div className="nav-section-label">{lang('Active projects', 'Proyectos activos')}</div>
						{activeProjects.slice(0, 8).map((project) => (
							<button
								key={project.id}
								className="nav-item"
								type="button"
								aria-label={project.name}
								aria-current={selectedProject?.id === project.id ? 'page' : undefined}
								onClick={() => {
									onSelectProject(project.id);
									onNavigate('workbench');
								}}
							>
								<FolderKanban aria-hidden="true" size={16} />
								<span>{project.name}</span>
							</button>
						))}
					</div>
				) : null}

				{activeArea === 'workbench' && recentWorkspaces.length ? (
					<div className="explorer-section">
						<div className="nav-section-label">{lang('Workspaces', 'Workspaces')}</div>
						{recentWorkspaces.map((workspace) => (
							<button key={workspace.id} className="nav-item" type="button" onClick={() => onNavigate('workspaces')}>
								<GitBranch aria-hidden="true" size={16} />
								<span className="mono">{shortId(String(workspace.taskId ?? workspace.id))}</span>
							</button>
						))}
					</div>
				) : null}

				{activeArea === 'runs' && recentRuns.length ? (
					<div className="explorer-section">
						<div className="nav-section-label">{lang('Recent runs', 'Ejecuciones recientes')}</div>
						{recentRuns.map((run) => (
							<button key={run.id} className="nav-item" type="button" onClick={() => onNavigate('workflows')}>
								<Workflow aria-hidden="true" size={16} />
								<span>{String(run.title ?? run.id)}</span>
							</button>
						))}
					</div>
				) : null}

				{activeArea === 'review' && recentEvidence.length ? (
					<div className="explorer-section">
						<div className="nav-section-label">{lang('Recent evidence', 'Evidencia reciente')}</div>
						{recentEvidence.map((evidence) => (
							<button key={evidence.id} className="nav-item" type="button" onClick={() => onNavigate('evidence')}>
								<FileCheck2 aria-hidden="true" size={16} />
								<span className="mono">{shortId(String(evidence.taskId ?? evidence.id))}</span>
							</button>
						))}
					</div>
				) : null}
			</nav>
		</aside>
	);
}
