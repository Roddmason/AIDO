/**
 * Context-aware left sidebar: per-area navigation plus the selected project's scope.
 *
 * When a project is selected it lists that project's active runs, approvals,
 * workspaces and evidence in collapsible sections whose state persists in localStorage.
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
import type { Variants } from 'motion/react';
import { m } from 'motion/react';
import type { ReactNode } from 'react';
import { useCallback, useMemo, useState } from 'react';

import type { Overview, Project } from '../api/types';
import { Badge, EmptyState, StatusDot } from '../components/primitives';
import { useI18n } from '../i18n/I18nProvider';
import { shortId, toneForStatus } from '../lib/format';
import { EASE_OUT, INDICATOR_TRANSITION } from '../motion/variants';
import type { AreaId, NavigationItem, PageId } from './navigation';
import { EXPLORER_GROUPS, EXPLORER_LINKS, EXPLORER_TITLE, pickLabel } from './navigation';

/**
 * Revelado del Explorer: el panel entra fundiéndose y deslizándose desde su borde (no un
 * corte). En desktop vive en un pane redimensionable del shell; en el layout apilado se
 * monta/desmonta según su estado. Bajo `MotionConfig reducedMotion="user"` el
 * desplazamiento cae a no-op y queda solo el fundido.
 */
const explorerReveal: Variants = {
	initial: { opacity: 0, x: -16 },
	animate: { opacity: 1, x: 0, transition: { duration: 0.22, ease: EASE_OUT } },
	exit: { opacity: 0, x: -16, transition: { duration: 0.16, ease: 'easeIn' } },
};

type Tone = 'ok' | 'warn' | 'danger' | 'info';
type RunStatus = Overview['workflows'][number]['status'];
type StackChip = { id: string; label: string };

/** Runs in any of these statuses are finished, not "active". Derived from the
 *  generated WorkflowRecord status union; unknown/new statuses are treated as
 *  active (fail-open) so in-flight work is never silently hidden from view. */
const TERMINAL_RUN_STATUSES = new Set<RunStatus>([
	'completed',
	'failed',
	'cancelled',
	'promoted_to_branch',
	'pr_created',
]);

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

/**
 * Context-aware left sidebar: per-area navigation (flat or grouped) plus, when a
 * project is selected, collapsible sections for its active runs, approvals,
 * workspaces and evidence. Persists section open/closed state in localStorage.
 */
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
	const { t } = useI18n();
	const links = EXPLORER_LINKS[activeArea];
	const groups = EXPLORER_GROUPS[activeArea];
	const title = pickLabel(EXPLORER_TITLE[activeArea], language);

	const renderLink = (link: NavigationItem) => {
		const Icon = link.icon;
		const label = pickLabel(link.label, language);
		const isActive = page === link.page;
		return (
			<button
				key={link.page}
				className="nav-item"
				type="button"
				aria-label={label}
				aria-current={isActive ? 'page' : undefined}
				onClick={() => onNavigate(link.page)}
				style={{ position: 'relative' }}
			>
				{isActive ? (
					<m.span
						layoutId="explorer-active"
						aria-hidden="true"
						style={{
							position: 'absolute',
							left: 0,
							top: '0.5rem',
							bottom: '0.5rem',
							width: '2px',
							borderRadius: '999px',
							background: 'var(--color-accent)',
						}}
						transition={INDICATOR_TRANSITION}
					/>
				) : null}
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
			runs: overview.workflows.filter(
				(run) => run.projectId === id && !TERMINAL_RUN_STATUSES.has(run.status),
			),
			approvals: overview.actionRequests.filter(
				(item) => item.projectId === id && item.status === 'pending',
			),
			workspaces: overview.runtimeWorkspaces.filter((item) => item.projectId === id),
			evidence: overview.evidencePackages.filter((item) => item.projectId === id),
		};
	}, [
		overview.workflows,
		overview.actionRequests,
		overview.runtimeWorkspaces,
		overview.evidencePackages,
		selectedProject,
	]);

	const stack = useMemo(
		() => (selectedProject ? readDetectedStack(selectedProject) : []),
		[selectedProject],
	);

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
		scope?.evidence.some((item) => BLOCKING_VERDICTS.includes(String(item.qaVerdict ?? ''))) ??
		false;

	const showAll = (count: number) => `${t('app.explorer.showAll', 'Show all')} (${count})`;

	return (
		<m.aside
			className="explorer-panel"
			aria-label={t('app.explorer.aria.panel', 'Explorer')}
			variants={explorerReveal}
			initial="initial"
			animate="animate"
			exit="exit"
			style={{ boxShadow: 'var(--shadow-panel-edge, 0 0 1.25rem rgb(0 0 0 / 0.18))' }}
		>
			<div className="explorer-top">
				<div className="explorer-header">
					<div className="explorer-title">{title}</div>
					{activeArea === 'home' ? (
						<button className="button explorer-action" type="button" onClick={onCreateProject}>
							<FolderPlus aria-hidden="true" size={15} />
							{t('app.explorer.newProject', 'New project')}
						</button>
					) : null}
				</div>

				{selectedProject ? (
					<section
						className="explorer-project"
						aria-label={t('app.explorer.currentProject', 'Current project')}
					>
						<div className="workspace-root-card">
							<span>{t('app.explorer.project', 'Project')}</span>
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
									<Badge tone="info">
										{t('app.explorer.stackNotDetected', 'Stack not detected')}
									</Badge>
								)}
								{stack.length > 3 ? <Badge tone="info">{`+${stack.length - 3}`}</Badge> : null}
							</div>
						</div>

						<div className="explorer-actions">
							<button
								className="button primary"
								type="button"
								onClick={() => onNavigate('workbench')}
							>
								<MessageSquarePlus aria-hidden="true" size={15} />
								{t('app.explorer.newTask', 'New task')}
							</button>
							<button className="button" type="button" onClick={onCreateProject}>
								<FolderOpen aria-hidden="true" size={15} />
								{t('app.explorer.openFolder', 'Open folder')}
							</button>
							<button className="button" type="button" onClick={() => onNavigate('review-board')}>
								<ClipboardCheck aria-hidden="true" size={15} />
								{t('app.explorer.review', 'Review')}
							</button>
							<button
								className="button"
								type="button"
								onClick={() => onNavigate('settings-project')}
							>
								<SettingsIcon aria-hidden="true" size={15} />
								{t('app.explorer.settings', 'Settings')}
							</button>
						</div>
					</section>
				) : (
					<div className="empty-state-action">
						<EmptyState
							title={t('app.explorer.noProjectSelected', 'No project selected')}
							body={t(
								'app.explorer.noProjectSelectedBody',
								'Open or pick a project to keep its context in view.',
							)}
						/>
						<button className="button" type="button" onClick={onCreateProject}>
							<FolderOpen aria-hidden="true" size={15} />
							{t('app.explorer.openFolder', 'Open folder')}
						</button>
					</div>
				)}
			</div>

			<nav
				className="nav-list ide-nav"
				aria-label={t('app.explorer.aria.nav', 'Explorer navigation')}
			>
				{scope ? (
					<>
						<ProjectSection
							id="runs"
							title={t('app.explorer.activeRuns', 'Active runs')}
							count={scope.runs.length}
							tone={scope.runs.length ? 'info' : undefined}
							open={sectionOpen('runs', scope.runs.length > 0)}
							onToggle={() => toggleSection('runs', scope.runs.length > 0)}
							emptyLabel={t('app.explorer.noActiveRuns', 'No active runs')}
						>
							{scope.runs.slice(0, SECTION_ROW_CAP).map((run) => (
								<button
									key={run.id}
									className="nav-item"
									type="button"
									onClick={() => onNavigate('workflows')}
								>
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
							title={t('app.explorer.approvals', 'Approvals')}
							count={scope.approvals.length}
							tone={scope.approvals.length ? 'warn' : undefined}
							open={sectionOpen('approvals', scope.approvals.length > 0)}
							onToggle={() => toggleSection('approvals', scope.approvals.length > 0)}
							emptyLabel={t('app.explorer.noPendingApprovals', 'No pending approvals')}
						>
							{scope.approvals.slice(0, SECTION_ROW_CAP).map((request) => (
								<button
									key={request.id}
									className="nav-item"
									type="button"
									onClick={() => onNavigate('review-board')}
								>
									<ClipboardCheck aria-hidden="true" size={16} />
									<span className="mono">{String(request.actionType ?? request.id)}</span>
									<span className="nav-item-meta">
										<StatusDot tone={toneForStatus(String(request.riskLevel))} />{' '}
										{String(request.riskLevel)}
									</span>
								</button>
							))}
							{scope.approvals.length > SECTION_ROW_CAP ? (
								<button
									className="nav-item"
									type="button"
									onClick={() => onNavigate('review-board')}
								>
									<ChevronRight aria-hidden="true" size={16} />
									<span>{showAll(scope.approvals.length)}</span>
								</button>
							) : null}
						</ProjectSection>

						<ProjectSection
							id="workspaces"
							title={t('app.nav.workspaces', 'Workspaces')}
							count={scope.workspaces.length}
							tone={scope.workspaces.length ? 'info' : undefined}
							open={sectionOpen('workspaces', false)}
							onToggle={() => toggleSection('workspaces', false)}
							emptyLabel={t('app.explorer.noWorkspaces', 'No workspaces')}
						>
							{scope.workspaces.slice(0, SECTION_ROW_CAP).map((workspace) => (
								<button
									key={workspace.id}
									className="nav-item"
									type="button"
									onClick={() => onNavigate('workspaces')}
								>
									<GitBranch aria-hidden="true" size={16} />
									<span className="mono">{shortId(String(workspace.taskId ?? workspace.id))}</span>
									<span className="nav-item-meta">
										{String(workspace.isolationType ?? workspace.status ?? '')}
									</span>
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
							title={t('app.explorer.evidence', 'Evidence')}
							count={scope.evidence.length}
							tone={evidenceBlocking ? 'danger' : scope.evidence.length ? 'info' : undefined}
							open={sectionOpen('evidence', false)}
							onToggle={() => toggleSection('evidence', false)}
							emptyLabel={t('app.explorer.noEvidencePackages', 'No evidence packages')}
						>
							{scope.evidence.slice(0, SECTION_ROW_CAP).map((item) => (
								<button
									key={item.id}
									className="nav-item"
									type="button"
									onClick={() => onNavigate('evidence')}
								>
									<FileCheck2 aria-hidden="true" size={16} />
									<span className="mono">{shortId(String(item.taskId ?? item.id))}</span>
									<span className="nav-item-meta">
										<StatusDot tone={toneForStatus(String(item.qaVerdict ?? ''))} />{' '}
										{String(item.qaVerdict ?? t('app.runtime.card.unknown', 'unknown'))}
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
							<div className="nav-section-label">
								{t('app.explorer.projectFiles', 'Project files')}
							</div>
							<div className="empty-state-action">
								<EmptyState
									title={t('app.explorer.projectFilesUnavailable', 'Project files unavailable')}
									body={t(
										'app.explorer.projectFilesUnavailableBody',
										'This control plane does not index project files yet. Open the folder to browse it in your OS file manager.',
									)}
								/>
								<button className="button explorer-action" type="button" onClick={onCreateProject}>
									<FolderOpen aria-hidden="true" size={15} />
									{t('app.explorer.openFolder', 'Open folder')}
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
						<div className="nav-section-label">{t('app.explorer.navigate', 'Navigate')}</div>
						{links.map(renderLink)}
					</div>
				)}

				{activeArea === 'home' && activeProjects.length ? (
					<div className="explorer-section">
						<div className="nav-section-label">
							{t('app.explorer.switchProject', 'Switch project')}
						</div>
						{activeProjects.slice(0, 8).map((project) => (
							<button
								key={project.id}
								className="nav-item"
								type="button"
								aria-label={project.name}
								aria-current={selectedProject?.id === project.id ? 'page' : undefined}
								onClick={() => {
									onSelectProject(project.id);
									onNavigate('projects');
								}}
							>
								<FolderKanban aria-hidden="true" size={16} />
								<span>{project.name}</span>
							</button>
						))}
					</div>
				) : null}
			</nav>
		</m.aside>
	);
}
