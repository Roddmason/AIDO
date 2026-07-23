/**
 * Root container of the control center IDE: hash-based routing plus all shell state.
 *
 * Resolves the active page from the URL hash, owns selection/dialog/drawer/palette
 * flags, wires the control-plane data hook and global shortcuts, and dispatches each
 * route to its feature page. Every chrome piece lives in its own component here.
 * @author Rodrigo Mason
 */
import { AnimatePresence } from 'motion/react';
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ErrorState, useToast } from '../components/ui';
import type { Language } from '../features/projects/ProjectsPage';
import { RuntimeHealthModal } from '../features/runtime-setup/RuntimeHealthModal';
import { SettingsModal } from '../features/settings/SettingsModal';
import { NewWorkspaceDialog } from '../features/workspace/NewWorkspaceDialog';
import type { WorkspaceMode } from '../features/workspace/useProjectDiscovery';
import { useControlPlane } from '../hooks/useControlPlane';
import { useI18n } from '../i18n/I18nProvider';
import { MotionPage } from '../motion/MotionPage';
import { useMotionPreference } from '../motion/useControlMotion';
import { ApprovalsDrawer } from './ApprovalsDrawer';
import { AppShell } from './AppShell';
import { BootScreen } from './BootScreen';
import { CommandPalette } from './CommandPalette';
import type { CommandAction } from './commandActions';
import { useCommandActions } from './commandActions';
import { EventsDrawer } from './EventsDrawer';
import { areaForPage, titleForPage } from './navigation';
import { RouteErrorBoundary } from './RouteErrorBoundary';
import { RouteSkeleton } from './RouteSkeleton';
import type { RouteContext } from './routes';
import { renderRoute } from './routes';
import type { AppRoute } from './routing';
import { encodeHash, resolveHashState, settingsHashToSection, splitHash } from './routing';
import { deriveRuntimeAlerts } from './runtimeHealth';
import { useShellShortcuts } from './useShellShortcuts';

const SELECTED_PROJECT_STORAGE_KEY = 'aido:selectedProjectId';

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
	} catch {}
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
	const [page, setPage] = useState<AppRoute>(() => resolveHashState().page);
	const [selectedRunId, setSelectedRunId] = useState<string | null>(() => resolveHashState().runId);
	const [selectedProjectId, setSelectedProjectId] = useState(readStoredSelectedProjectId);
	const [selectedSessionId, setSelectedSessionId] = useState('');
	const [workspaceDialogOpen, setWorkspaceDialogOpen] = useState(false);
	const [workspaceDialogMode, setWorkspaceDialogMode] = useState<WorkspaceMode>('open_folder');
	const [approvalDrawerOpen, setApprovalDrawerOpen] = useState(false);
	const [eventDrawerOpen, setEventDrawerOpen] = useState(false);
	const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
	const [settingsModalOpen, setSettingsModalOpen] = useState(false);
	const [settingsSection, setSettingsSection] = useState('general');
	const [settingsProviderId, setSettingsProviderId] = useState<string | null>(null);
	const [runtimeHealthOpen, setRuntimeHealthOpen] = useState(false);
	/** Provider ids already flagged as blocked, so a re-poll only toasts a genuine new degradation. */
	const knownBlockedRuntimesRef = useRef<Set<string> | null>(null);
	const state = useControlPlane();
	const commandActionsRef = useRef<CommandAction[]>([]);

	const navigateTo = useCallback((nextPage: AppRoute, runId?: string) => {
		window.location.hash = encodeHash(nextPage, runId);
		setPage(nextPage);
		setSelectedRunId(runId ?? null);
	}, []);
	const openRun = useCallback((runId: string) => navigateTo('workflows', runId), [navigateTo]);
	const clearRun = useCallback(() => navigateTo('workflows'), [navigateTo]);

	const openWorkspaceDialog = useCallback((mode: WorkspaceMode = 'open_folder') => {
		setWorkspaceDialogMode(mode);
		setWorkspaceDialogOpen(true);
	}, []);

	/** Opens the Settings modal at a given section (defaults to 'general'). */
	const openSettings = useCallback((section?: string, providerId?: string) => {
		setSettingsSection(section ?? 'general');
		setSettingsProviderId(providerId?.trim() || null);
		setSettingsModalOpen(true);
	}, []);

	const closeCommandPalette = useCallback(() => setCommandPaletteOpen(false), []);
	const toggleCommandPalette = useCallback(() => setCommandPaletteOpen((open) => !open), []);
	const openApprovals = useCallback(() => setApprovalDrawerOpen(true), []);
	const openEvents = useCallback(() => setEventDrawerOpen(true), []);
	const closeOverlays = useCallback(() => {
		setApprovalDrawerOpen(false);
		setEventDrawerOpen(false);
		setCommandPaletteOpen(false);
		setSettingsModalOpen(false);
		setRuntimeHealthOpen(false);
	}, []);

	const runtimeAlerts = useMemo(
		() => deriveRuntimeAlerts(state.runtimeProviders),
		[state.runtimeProviders],
	);

	// Proactive nudge: when the live poll shows an AI degrade from healthy to blocked (an expired CLI
	// token, an exhausted quota) toast it once, edge-triggered. The first poll only seeds the baseline
	// so a reload never spams a toast per already-broken provider.
	useEffect(() => {
		if (!state.runtimeProviders) return;
		const current = new Set(runtimeAlerts.map((alert) => alert.providerId));
		const seen = knownBlockedRuntimesRef.current;
		knownBlockedRuntimesRef.current = current;
		if (seen === null) return;
		for (const alert of runtimeAlerts) {
			if (seen.has(alert.providerId)) continue;
			notify({
				title: t('app.runtime.health.toastTitle', 'An AI needs attention'),
				body: `${alert.displayName} — ${t('app.runtime.health.toastBody', 'open AI health to see how to fix it.')}`,
				tone: 'warn',
				action: {
					label: t('app.runtime.health.review', 'Review'),
					onPress: () => setRuntimeHealthOpen(true),
				},
			});
		}
	}, [state.runtimeProviders, runtimeAlerts, notify, t]);

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
		const onHash = () => {
			const { token } = splitHash();
			const settingsSection = settingsHashToSection(token);
			if (settingsSection !== undefined) {
				openSettings(settingsSection);
				window.location.hash = 'home';
				setPage('home');
				setSelectedRunId(null);
				return;
			}
			const next = resolveHashState();
			setPage(next.page);
			setSelectedRunId(next.runId);
		};
		window.addEventListener('hashchange', onHash);
		return () => window.removeEventListener('hashchange', onHash);
	}, [openSettings]);

	useEffect(() => {
		const { token } = splitHash();
		const section = settingsHashToSection(token);
		if (section !== undefined) {
			openSettings(section);
			window.location.hash = 'home';
			setPage('home');
		}
	}, [openSettings]);

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
		setSelectedProjectId((current) => {
			if (current !== projectId) setSelectedSessionId('');
			return projectId;
		});
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
		openSettings,
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

	if (state.loading || !overview) {
		return (
			<div className="app-shell-ide">
				<main className="workbench main-area">
					<section className="content-frame">
						<BootScreen error={state.error ?? ''} />
					</section>
				</main>
			</div>
		);
	}

	const routeContext: RouteContext = {
		overview,
		selectedProject,
		runtimeProviders: state.runtimeProviders,
		runtimeProviderConfiguration: state.runtimeProviderConfiguration,
		retrievalStatus: state.retrievalStatus,
		token: state.token,
		mutate: state.mutate,
		refresh: state.refresh,
		language: bilingualLanguage,
		navigateTo,
		openRun,
		onSelectProject: setOperationalProject,
		openWorkspaceDialog,
		openSettings,
		selectedSessionId,
		onSelectSession: setSelectedSessionId,
	};

	return (
		<>
			<AppShell
				area={areaForPage(page)}
				language={bilingualLanguage}
				languages={languages}
				t={t}
				navigateTo={navigateTo}
				onChangeLanguage={changeLanguage}
				overview={overview}
				runtimeProviders={state.runtimeProviders}
				selectedProject={selectedProject}
				selectedRunId={selectedRunId}
				token={state.token}
				mutate={state.mutate}
				onClearRun={clearRun}
				selectedSessionId={selectedSessionId}
				onSelectSession={setSelectedSessionId}
				connected={state.connected}
				onSelectProject={setOperationalProject}
				onCreateProject={() => openWorkspaceDialog('open_folder')}
				onOpenWorkspaceDialog={openWorkspaceDialog}
				onOpenCommandPalette={() => setCommandPaletteOpen(true)}
				onOpenApprovals={() => setApprovalDrawerOpen(true)}
				onOpenEvents={() => setEventDrawerOpen(true)}
				onRefresh={() => void state.refresh()}
				onOpenSettings={openSettings}
				onOpenRuntimeHealth={() => setRuntimeHealthOpen(true)}
			>
				<AnimatePresence mode="wait">
					<MotionPage key={page}>
						<RouteErrorBoundary
							title={t('app.route.loadErrorTitle', 'This view could not be loaded')}
							body={t(
								'app.route.loadErrorBody',
								'Something went wrong opening this page. Retry or pick another view.',
							)}
							retryLabel={t('app.route.retry', 'Retry')}
						>
							<Suspense fallback={<RouteSkeleton />}>
								{state.error ? (
									<ErrorState
										title={t('app.boot.controlPlaneUnavailable', 'Control plane unavailable')}
										body={state.error}
									/>
								) : (
									renderRoute(page, routeContext)
								)}
							</Suspense>
						</RouteErrorBoundary>
					</MotionPage>
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

			{/* Settings modal: global overlay, portaled via Dialog. */}
			<SettingsModal
				open={settingsModalOpen}
				onClose={() => setSettingsModalOpen(false)}
				projectId={selectedProject?.id}
				initialSection={settingsSection}
				initialProviderId={settingsProviderId}
				overview={overview}
				selectedProject={selectedProject}
				runtimeProviders={state.runtimeProviders}
				runtimeProviderConfiguration={state.runtimeProviderConfiguration}
				token={state.token}
				onRefresh={() => state.refresh(true)}
				onCreateProject={() => openWorkspaceDialog('open_folder')}
				onSelectProject={setOperationalProject}
				mutate={state.mutate}
				language={bilingualLanguage}
			/>

			{/* AI-health modal: global overlay listing every runtime that needs attention. */}
			<RuntimeHealthModal
				open={runtimeHealthOpen}
				onClose={() => setRuntimeHealthOpen(false)}
				alerts={runtimeAlerts}
				onOpenSettings={(section, providerId) => {
					setRuntimeHealthOpen(false);
					openSettings(section, providerId);
				}}
			/>
		</>
	);
}
