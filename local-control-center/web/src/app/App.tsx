/**
 * Root container of the control center IDE: hash-based routing plus all shell state.
 *
 * Resolves the active page from the URL hash, owns selection/dialog/drawer/palette
 * flags, wires the control-plane data hook and global shortcuts, and dispatches each
 * route to its feature page. Every chrome piece lives in its own component here.
 */
import { AnimatePresence } from 'motion/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { EmptyState, ErrorState, useToast } from '../components/ui';
import type { Language, ProjectStatusView } from '../features/active-projects/ActiveProjectsPage';
import { ActiveProjectsPage } from '../features/active-projects/ActiveProjectsPage';
import { AgentsPage } from '../features/agents/AgentsPage';
import { HomePage } from '../features/home/HomePage';
import { ModelGatewayPage } from '../features/model-gateway/ModelGatewayPage';
import {
	AuditPage,
	EvidencePage,
	GovernancePage,
	IntegrationsPage,
	MemoryPage,
	PolicySecurityPage,
	WorkspacesPage,
} from '../features/pages';
import { ReviewPage } from '../features/review/ReviewPage';
import type { SettingsGroupId } from '../features/settings/SettingsPage';
import { SettingsPage } from '../features/settings/SettingsPage';
import { WorkbenchPage } from '../features/workbench/WorkbenchPage';
import { WorkflowsPage } from '../features/workflows/WorkflowsPage';
import { NewWorkspaceDialog } from '../features/workspace/NewWorkspaceDialog';
import type { WorkspaceMode } from '../features/workspace/useProjectDiscovery';
import { useControlPlane } from '../hooks/useControlPlane';
import { useI18n } from '../i18n/I18nProvider';
import { MotionPage } from '../motion/MotionPage';
import { useMotionPreference } from '../motion/useControlMotion';
import { ApprovalsDrawer } from './ApprovalsDrawer';
import { AppShell } from './AppShell';
import { CommandPalette } from './CommandPalette';
import type { CommandAction } from './commandActions';
import { useCommandActions } from './commandActions';
import { EventsDrawer } from './EventsDrawer';
import { areaForPage, titleForPage } from './navigation';
import type { AppRoute } from './routing';
import { resolveHashRoute } from './routing';
import { useShellShortcuts } from './useShellShortcuts';

const SELECTED_PROJECT_STORAGE_KEY = 'aido:selectedProjectId';

/** Status-filtered project pages map to an ActiveProjectsPage status view. */
const projectStatusByPage: Partial<Record<AppRoute, ProjectStatusView>> = {
	'projects-active': 'active',
	'projects-finished': 'finished',
	'projects-error': 'error',
	'projects-cancelled': 'cancelled',
};

/** Settings routes map to the SettingsPage group they should open. */
const settingsGroupByPage: Partial<Record<AppRoute, SettingsGroupId>> = {
	'settings-project': 'project',
	'settings-runtime': 'runtime',
	'settings-agents': 'agents',
	'settings-security': 'security',
	'settings-workspaces': 'workspaces',
	'settings-integrations': 'integrations',
	'settings-advanced': 'advanced',
};

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

/**
 * Root of the control center. Owns global shell state (active {@link AppRoute},
 * selected project, dialog/drawer/palette flags), wires the control-plane data
 * hook and the global keyboard layer, and routes the active page to its feature
 * component. All chrome — navigation, header, status bar, drawers, command
 * palette — lives in dedicated single-responsibility components.
 */
export function App() {
	useMotionPreference();
	const { language, languages, setLanguage, t } = useI18n();
	const { notify } = useToast();
	const bilingualLanguage: Language = language === 'es' ? 'es' : 'en';
	const [page, setPage] = useState<AppRoute>(resolveHashRoute());
	const [selectedProjectId, setSelectedProjectId] = useState(readStoredSelectedProjectId);
	const [workspaceDialogOpen, setWorkspaceDialogOpen] = useState(false);
	const [workspaceDialogMode, setWorkspaceDialogMode] = useState<WorkspaceMode>('open_folder');
	const [approvalDrawerOpen, setApprovalDrawerOpen] = useState(false);
	const [eventDrawerOpen, setEventDrawerOpen] = useState(false);
	const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
	const state = useControlPlane();
	const commandActionsRef = useRef<CommandAction[]>([]);

	const navigateTo = useCallback((nextPage: AppRoute) => {
		window.location.hash = nextPage;
		setPage(nextPage);
	}, []);

	const openWorkspaceDialog = useCallback((mode: WorkspaceMode = 'open_folder') => {
		setWorkspaceDialogMode(mode);
		setWorkspaceDialogOpen(true);
	}, []);

	const closeCommandPalette = useCallback(() => setCommandPaletteOpen(false), []);
	const toggleCommandPalette = useCallback(() => setCommandPaletteOpen((open) => !open), []);
	const openApprovals = useCallback(() => setApprovalDrawerOpen(true), []);
	const openEvents = useCallback(() => setEventDrawerOpen(true), []);
	const closeOverlays = useCallback(() => {
		setApprovalDrawerOpen(false);
		setEventDrawerOpen(false);
		setCommandPaletteOpen(false);
	}, []);

	const changeLanguage = useCallback(
		(nextLanguage: string) => {
			setLanguage(nextLanguage);
		},
		[setLanguage],
	);

	useEffect(() => {
		document.documentElement.lang = language;
	}, [language]);

	useEffect(() => {
		document.title = `${titleForPage(page, language)} · AIDO Control Center`;
	}, [page, language]);

	useEffect(() => {
		const onHash = () => setPage(resolveHashRoute());
		window.addEventListener('hashchange', onHash);
		return () => window.removeEventListener('hashchange', onHash);
	}, []);

	useShellShortcuts({
		commandActionsRef,
		navigateTo,
		onEscape: closeOverlays,
		onToggleCommandPalette: toggleCommandPalette,
		onOpenEvents: openEvents,
	});

	const overview = state.overview;
	const activeProjects = useMemo(
		() => overview?.projects.filter((project) => project.status === 'active') ?? [],
		[overview?.projects],
	);
	const selectedProject =
		activeProjects.find((project) => project.id === selectedProjectId) ?? activeProjects[0] ?? null;
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
	const pendingReviewCount = useMemo(
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
		pendingReviewCount,
		evidenceCount: overview?.evidencePackages.length ?? 0,
		connected: state.connected,
		close: closeCommandPalette,
	});
	commandActionsRef.current = commandActions;

	const pageContent = () => {
		if (!overview) return null;
		if (state.error) {
			return (
				<ErrorState
					title={t('app.boot.controlPlaneUnavailable', 'Control plane unavailable')}
					body={state.error}
				/>
			);
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
					onOpenReview={() => navigateTo('review-board')}
					onOpenRuns={() => navigateTo('workflows')}
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
					onOpenJobs={() => navigateTo('review-board')}
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
				return <WorkflowsPage overview={overview} token={state.token} mutate={state.mutate} />;
			case 'review-board':
				return (
					<ReviewPage
						overview={overview}
						token={state.token}
						mutate={state.mutate}
						refresh={state.refresh}
					/>
				);
			case 'agents':
				return (
					<AgentsPage
						overview={overview}
						runtimeProviders={state.runtimeProviders}
						mutate={state.mutate}
					/>
				);
			case 'workspaces':
				return <WorkspacesPage overview={overview} />;
			case 'policy':
				return <PolicySecurityPage overview={overview} mutate={state.mutate} />;
			case 'memory':
				return <MemoryPage overview={overview} retrievalStatus={state.retrievalStatus} />;
			case 'evidence':
				return <EvidencePage overview={overview} token={state.token} />;
			case 'models':
				return (
					<ModelGatewayPage
						overview={overview}
						runtimeProviders={state.runtimeProviders}
						token={state.token}
						onRefreshRuntimeProviders={() => state.refresh(true)}
					/>
				);
			case 'governance':
				return (
					<GovernancePage
						overview={overview}
						selectedProject={selectedProject}
						mutate={state.mutate}
					/>
				);
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
				return (
					<ActiveProjectsPage
						overview={overview}
						selectedProject={selectedProject}
						language={bilingualLanguage}
						onSelectProject={setOperationalProject}
						onOpenSettings={() => navigateTo('settings-project')}
						onCreateProject={() => openWorkspaceDialog('open_folder')}
					/>
				);
		}
	};

	if (state.loading || !overview) {
		const failed = Boolean(state.error);
		return (
			<div className="app-shell-ide">
				<main className="workbench main-area">
					<section className="content-frame">
						{failed ? (
							<ErrorState
								title={t('app.boot.controlPlaneUnavailable', 'Control plane unavailable')}
								body={state.error ?? ''}
							/>
						) : (
							<EmptyState
								title={t('app.boot.loading', 'Loading control plane')}
								body={t(
									'app.boot.loadingBody',
									'Waiting for FastAPI v1, SQLite and runtime providers.',
								)}
							/>
						)}
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
			>
				<AnimatePresence mode="wait">
					<MotionPage key={page}>{pageContent()}</MotionPage>
				</AnimatePresence>
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
					notify({
						title: t('app.toast.workspaceReady', 'Workspace ready'),
						body: t('app.toast.workspaceReadyBody', 'Opened in the workbench.'),
						tone: 'ok',
					});
				}}
			/>

			<ApprovalsDrawer
				open={approvalDrawerOpen}
				onClose={() => setApprovalDrawerOpen(false)}
				actionRequests={overview.actionRequests}
			/>
			<EventsDrawer
				open={eventDrawerOpen}
				onClose={() => setEventDrawerOpen(false)}
				events={overview.events}
			/>
			<CommandPalette
				open={commandPaletteOpen}
				onClose={closeCommandPalette}
				actions={commandActions}
			/>
		</>
	);
}
