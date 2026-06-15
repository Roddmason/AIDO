/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { useControlPlane } from '../hooks/useControlPlane';
import { useMotionPreference, usePageMotion } from '../motion/useControlMotion';
import type { Overview } from '../api/types';
import { Badge, DataTable, Drawer, EmptyState } from '../components/primitives';
import { CommandPalette } from './CommandPalette';
import { matchesShortcut, useCommandActions } from './commandActions';
import type { CommandAction } from './commandActions';
import { ActiveProjectsPage } from '../features/active-projects/ActiveProjectsPage';
import type { Language, ProjectStatusView } from '../features/active-projects/ActiveProjectsPage';
import { WorkbenchPage } from '../features/workbench/WorkbenchPage';
import { HomePage } from '../features/home/HomePage';
import { NewWorkspaceDialog } from '../features/workspace/NewWorkspaceDialog';
import type { WorkspaceMode } from '../features/workspace/useProjectDiscovery';
import { useI18n } from '../i18n/I18nProvider';
import { JobsApprovalsPage } from '../features/jobs-approvals/JobsApprovalsPage';
import { ReviewPage } from '../features/review/ReviewPage';
import { WorkflowsPage } from '../features/workflows/WorkflowsPage';
import { AgentsPage } from '../features/agents/AgentsPage';
import { ModelGatewayPage } from '../features/model-gateway/ModelGatewayPage';
import { SettingsPage } from '../features/settings/SettingsPage';
import type { SettingsGroupId } from '../features/settings/SettingsPage';
import {
	AuditPage,
	EvidencePage,
	GovernancePage,
	IntegrationsPage,
	MemoryPage,
	PolicySecurityPage,
	WorkspacesPage,
} from '../features/pages';
import { countByStatus, shortId, toneForStatus } from '../lib/format';
import { AppShell } from './AppShell';
import { areaForPage, pageIds, titleForPage } from './navigation';
import type { PageId } from './navigation';

const SELECTED_PROJECT_STORAGE_KEY = 'aido:selectedProjectId';

function sumRecordedCost(rows: Overview['costUsage']) {
	const amounts = rows.map((row) => Number(row.amountUsd)).filter((amount) => Number.isFinite(amount));
	return amounts.length ? amounts.reduce((sum, amount) => sum + amount, 0) : null;
}

const projectStatusByPage: Partial<Record<PageId, ProjectStatusView>> = {
	'projects-active': 'active',
	'projects-finished': 'finished',
	'projects-error': 'error',
	'projects-cancelled': 'cancelled',
};

const settingsGroupByPage: Partial<Record<PageId, SettingsGroupId>> = {
	'settings-project': 'project',
	'settings-runtime': 'runtime',
	'settings-agents': 'agents',
	'settings-security': 'security',
	'settings-workspaces': 'workspaces',
	'settings-integrations': 'integrations',
	'settings-advanced': 'advanced',
};

const routeAliases: Record<string, PageId> = {
	active: 'projects-active',
	ide: 'workbench',
	workspace: 'workbench',
	command: 'workbench',
	runs: 'workflows',
	review: 'jobs',
	settings: 'settings-project',
	// Backward-compat: resolve the retired per-tab settings hashes to their owning group.
	'settings-projects': 'settings-project',
	'settings-user': 'settings-advanced',
	'settings-cli': 'settings-runtime',
	'settings-api': 'settings-runtime',
	'settings-parameters': 'settings-advanced',
	'settings-maintainers': 'settings-advanced',
	'settings-defaults': 'settings-advanced',
};

function currentHash(): PageId {
	const value = window.location.hash.replace('#', '');
	return routeAliases[value] ?? (pageIds.includes(value as PageId) ? (value as PageId) : 'home');
}

function readStoredSelectedProjectId() {
	try {
		return window.localStorage.getItem(SELECTED_PROJECT_STORAGE_KEY) ?? '';
	} catch {
		return '';
	}
}

function persistSelectedProjectId(projectId: string) {
	try {
		if (projectId) {
			window.localStorage.setItem(SELECTED_PROJECT_STORAGE_KEY, projectId);
		} else {
			window.localStorage.removeItem(SELECTED_PROJECT_STORAGE_KEY);
		}
	} catch {
		// localStorage is optional in restricted browser contexts.
	}
}

export function App() {
	useMotionPreference();
	const { language, languages, setLanguage, t } = useI18n();
	const bilingualLanguage: Language = language === 'es' ? 'es' : 'en';
	const [page, setPage] = useState<PageId>(currentHash());
	const [selectedProjectId, setSelectedProjectId] = useState(readStoredSelectedProjectId);
	const [workspaceDialogOpen, setWorkspaceDialogOpen] = useState(false);
	const [workspaceDialogMode, setWorkspaceDialogMode] = useState<WorkspaceMode>('open_folder');
	const [approvalDrawerOpen, setApprovalDrawerOpen] = useState(false);
	const [eventDrawerOpen, setEventDrawerOpen] = useState(false);
	const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
	const [eventFilter, setEventFilter] = useState('');
	const state = useControlPlane();
	const motionRef = usePageMotion(page);
	const commandActionsRef = useRef<CommandAction[]>([]);

	const navigateTo = useCallback((nextPage: PageId) => {
		window.location.hash = nextPage;
		setPage(nextPage);
	}, []);

	const openWorkspaceDialog = useCallback((mode: WorkspaceMode = 'open_folder') => {
		setWorkspaceDialogMode(mode);
		setWorkspaceDialogOpen(true);
	}, []);

	const closeCommandPalette = useCallback(() => setCommandPaletteOpen(false), []);
	const openApprovals = useCallback(() => setApprovalDrawerOpen(true), []);

	const changeLanguage = useCallback((nextLanguage: string) => {
		setLanguage(nextLanguage);
	}, [setLanguage]);

	useEffect(() => {
		document.documentElement.lang = language;
	}, [language]);

	useEffect(() => {
		document.title = `${titleForPage(page, language)} · AIDO Control Center`;
	}, [page, language]);

	useEffect(() => {
		const onHash = () => setPage(currentHash());
		window.addEventListener('hashchange', onHash);
		return () => window.removeEventListener('hashchange', onHash);
	}, []);

	useLayoutEffect(() => {
		const onKeyDown = (event: KeyboardEvent) => {
			const target = event.target as HTMLElement | null;
			const editableTarget =
				target?.isContentEditable ||
				target?.tagName === 'INPUT' ||
				target?.tagName === 'TEXTAREA' ||
				target?.tagName === 'SELECT';
			if (event.key === 'Escape') {
				setApprovalDrawerOpen(false);
				setEventDrawerOpen(false);
				setCommandPaletteOpen(false);
			}
			if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
				event.preventDefault();
				setCommandPaletteOpen((open) => !open);
				return;
			}
			if (editableTarget) return;
			if (event.ctrlKey && event.altKey) {
				for (const action of commandActionsRef.current) {
					if (action.shortcut && !action.disabled && matchesShortcut(event, action.shortcut)) {
						event.preventDefault();
						action.run();
						return;
					}
				}
				const key = event.key.toLowerCase();
				const code = event.code;
				if (key === 'e' || code === 'KeyE') {
					event.preventDefault();
					setEventDrawerOpen(true);
				}
				if (key === 'w' || code === 'KeyW') {
					event.preventDefault();
					navigateTo('workflows');
				}
			}
		};
		window.addEventListener('keydown', onKeyDown, true);
		return () => window.removeEventListener('keydown', onKeyDown, true);
	}, [navigateTo]);

	const overview = state.overview;
	const activeProjects = useMemo(() => overview?.projects.filter((project) => project.status === 'active') ?? [], [overview?.projects]);
	const selectedProject = activeProjects.find((project) => project.id === selectedProjectId) ?? activeProjects[0] ?? null;
	const setOperationalProject = useCallback((projectId: string) => {
		setSelectedProjectId(projectId);
		persistSelectedProjectId(projectId);
	}, []);
	useEffect(() => {
		if (!overview) return;
		if (selectedProjectId && activeProjects.some((project) => project.id === selectedProjectId)) {
			persistSelectedProjectId(selectedProjectId);
			return;
		}
		const fallbackProjectId = activeProjects[0]?.id ?? '';
		setSelectedProjectId(fallbackProjectId);
		persistSelectedProjectId(fallbackProjectId);
	}, [activeProjects, overview, selectedProjectId]);
	const filteredEvents = useMemo(() => {
		const query = eventFilter.trim().toLowerCase();
		const events = overview?.events ?? [];
		if (!query) return events;
		return events.filter((event) => {
			const payload = JSON.stringify(event.payload ?? {}).toLowerCase();
			return (
				event.type.toLowerCase().includes(query) ||
				String(event.severity ?? '').toLowerCase().includes(query) ||
				String(event.projectId ?? '').toLowerCase().includes(query) ||
				String(event.jobId ?? '').toLowerCase().includes(query) ||
				payload.includes(query)
			);
		});
	}, [eventFilter, overview?.events]);
	const pendingApprovalsCount = useMemo(
		() => overview?.actionRequests.filter((item) => item.status === 'pending').length ?? 0,
		[overview?.actionRequests],
	);
	const commandActions = useCommandActions({
		navigateTo,
		openWorkspaceDialog,
		onOpenApprovals: openApprovals,
		refresh: state.refresh,
		selectedProject,
		runtimeProviders: state.runtimeProviders,
		pendingApprovalsCount,
		evidenceCount: overview?.evidencePackages.length ?? 0,
		connected: state.connected,
		close: closeCommandPalette,
	});
	commandActionsRef.current = commandActions;

	const pageContent = () => {
		if (!overview) return null;
		if (state.error) {
			return <EmptyState title="Control plane unavailable" body={state.error} />;
		}
		if (page === 'home') {
			return (
				<HomePage
					overview={overview}
					runtimeProviders={state.runtimeProviders}
					selectedProject={selectedProject}
					language={bilingualLanguage}
					onSelectProject={setOperationalProject}
					onCreateProject={() => openWorkspaceDialog('create_workspace')}
					onOpenFolder={() => openWorkspaceDialog('open_folder')}
					onOpenWorkbench={() => navigateTo('workbench')}
					onOpenProjects={() => navigateTo('projects-active')}
					onOpenReview={() => navigateTo('jobs')}
					onOpenRuns={() => navigateTo('workflows')}
					onOpenEvidence={() => navigateTo('evidence')}
					onOpenRuntimes={() => navigateTo('models')}
				/>
			);
		}
		if (page === 'workbench') {
			return (
				<WorkbenchPage
					overview={overview}
					selectedProject={selectedProject}
					runtimeProviders={state.runtimeProviders}
					runtimeProviderConfiguration={state.runtimeProviderConfiguration}
					mutate={state.mutate}
					token={state.token}
					onSelectProject={setOperationalProject}
					onCreateProject={() => openWorkspaceDialog('open_folder')}
					onOpenJobs={() => navigateTo('jobs')}
					onOpenEvidence={() => navigateTo('evidence')}
					onOpenSettings={() => navigateTo('settings-project')}
					onOpenRuntimeSetup={() => navigateTo('settings-runtime')}
					onRefresh={() => state.refresh(true)}
				/>
			);
		}
		const projectStatusView = projectStatusByPage[page];
		if (projectStatusView) {
			return (
				<ActiveProjectsPage
					overview={overview}
					selectedProject={selectedProject}
					statusView={projectStatusView}
					language={bilingualLanguage}
					onSelectProject={setOperationalProject}
					onOpenSettings={() => navigateTo('settings-project')}
					onCreateProject={() => openWorkspaceDialog('open_folder')}
				/>
			);
		}
		switch (page) {
			case 'workflows':
				return <WorkflowsPage overview={overview} token={state.token} />;
			case 'review-board':
				return <ReviewPage overview={overview} token={state.token} mutate={state.mutate} refresh={state.refresh} />;
			case 'jobs':
				return <JobsApprovalsPage overview={overview} token={state.token} mutate={state.mutate} refresh={state.refresh} />;
			case 'agents':
				return <AgentsPage overview={overview} runtimeProviders={state.runtimeProviders} mutate={state.mutate} />;
			case 'workspaces':
				return <WorkspacesPage overview={overview} />;
			case 'policy':
				return <PolicySecurityPage overview={overview} mutate={state.mutate} />;
			case 'memory':
				return <MemoryPage overview={overview} retrievalStatus={state.retrievalStatus} />;
			case 'evidence':
				return <EvidencePage overview={overview} token={state.token} />;
			case 'models':
				return <ModelGatewayPage overview={overview} runtimeProviders={state.runtimeProviders} token={state.token} onRefreshRuntimeProviders={() => state.refresh(true)} />;
			case 'governance':
				return <GovernancePage overview={overview} selectedProject={selectedProject} mutate={state.mutate} />;
			case 'audit':
				return <AuditPage overview={overview} />;
			case 'integrations':
				return <IntegrationsPage overview={overview} mutate={state.mutate} />;
			case 'settings-project':
			case 'settings-runtime':
			case 'settings-agents':
			case 'settings-security':
			case 'settings-integrations':
			case 'settings-advanced':
			case 'settings-workspaces':
				return (
					<SettingsPage
						overview={overview}
						selectedProject={selectedProject}
						onSelectProject={setOperationalProject}
						onCreateProject={() => openWorkspaceDialog('open_folder')}
						mutate={state.mutate}
						section={settingsGroupByPage[page]}
							runtimeProviders={state.runtimeProviders}
							runtimeProviderConfiguration={state.runtimeProviderConfiguration}
							token={state.token}
							onRefresh={() => state.refresh(true)}
						language={bilingualLanguage}
					/>
				);
			default:
				return <ActiveProjectsPage overview={overview} selectedProject={selectedProject} language={bilingualLanguage} onSelectProject={setOperationalProject} onOpenSettings={() => navigateTo('settings-project')} onCreateProject={() => openWorkspaceDialog('open_folder')} />;
		}
	};

	if (state.loading || !overview) {
		const failed = Boolean(state.error);
		return (
			<div className="app-shell-ide" data-explorer="false" data-inspector="false">
				<span className="console-grid" aria-hidden="true" />
				<main className="workbench main-area">
					<section className="content-frame">
						<EmptyState
							title={failed ? 'Control plane unavailable' : 'Loading control plane'}
							body={failed ? state.error : 'Waiting for FastAPI v1, SQLite and runtime providers.'}
						/>
					</section>
				</main>
			</div>
		);
	}

	return (
		<>
			<AppShell
				area={areaForPage(page)}
				page={page}
				language={bilingualLanguage}
				languages={languages}
				t={t}
				navigateTo={navigateTo}
				onChangeLanguage={changeLanguage}
				overview={overview}
				runtimeProviders={state.runtimeProviders}
				selectedProject={selectedProject}
				connected={state.connected}
				onSelectProject={setOperationalProject}
				onCreateProject={() => openWorkspaceDialog('open_folder')}
				onOpenCommandPalette={() => setCommandPaletteOpen(true)}
				onOpenApprovals={() => setApprovalDrawerOpen(true)}
				onOpenEvents={() => setEventDrawerOpen(true)}
				onRefresh={() => void state.refresh()}
				headerKicker={t('app.global.projectWorkspaceRuntime', 'Project · Workspace · Runtime')}
				headerTitle={selectedProject?.name ?? t('app.global.runtimeProject', 'Runtime project')}
				contentRef={motionRef}
			>
				{pageContent()}
			</AppShell>

			<NewWorkspaceDialog
				open={workspaceDialogOpen}
				overview={overview}
				mutate={state.mutate}
				initialMode={workspaceDialogMode}
				onClose={() => setWorkspaceDialogOpen(false)}
				onCreated={(projectId) => {
					setOperationalProject(projectId);
					setWorkspaceDialogOpen(false);
					navigateTo('workbench');
				}}
			/>

			<Drawer label="Approval drawer" open={approvalDrawerOpen} onClose={() => setApprovalDrawerOpen(false)}>
				<DataTable
					rows={overview.actionRequests.filter((item) => item.status === 'pending').slice(0, 12)}
					empty={<EmptyState title="No pending approvals" body="Action requests appear here when policy gates execution." />}
					columns={[
						{ key: 'action', label: 'Action', render: (row) => <span className="mono">{row.actionType}</span> },
						{ key: 'risk', label: 'Risk', render: (row) => <Badge tone={toneForStatus(row.riskLevel)}>{row.riskLevel}</Badge> },
						{ key: 'command', label: 'Command', render: (row) => <span className="mono">{row.command || 'n/a'}</span> },
						{ key: 'job', label: 'Job', render: (row) => <span className="mono">{shortId(row.jobId)}</span> },
					]}
				/>
			</Drawer>
			<Drawer label="Event drawer" open={eventDrawerOpen} onClose={() => setEventDrawerOpen(false)}>
				<div className="drawer-body">
					<div className="field">
						<label htmlFor="event-filter">Event filter</label>
						<input
							id="event-filter"
							className="input"
							value={eventFilter}
							onChange={(event) => setEventFilter(event.target.value)}
							placeholder="Filter by type, severity, id or payload"
						/>
					</div>
				</div>
				<DataTable
					rows={filteredEvents.slice(0, 16)}
					empty={<EmptyState title="No events" body="Workflow, job, policy, and evidence events appear here." />}
					columns={[
						{ key: 'type', label: 'Type', render: (row) => <span className="mono">{row.type}</span> },
						{ key: 'severity', label: 'Severity', render: (row) => <Badge tone={toneForStatus(row.severity)}>{row.severity ?? 'info'}</Badge> },
						{ key: 'id', label: 'Event', render: (row) => <span className="mono">{shortId(row.id)}</span> },
					]}
				/>
			</Drawer>
			<CommandPalette open={commandPaletteOpen} onClose={closeCommandPalette} actions={commandActions} />
		</>
	);
}
