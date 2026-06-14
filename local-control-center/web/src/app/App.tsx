import {
	Archive,
	Ban,
	Bot,
	Brain,
	CheckCircle2,
	ClipboardCheck,
	Code2,
	FileCheck2,
	FolderKanban,
	Gauge,
	GitBranch,
	History,
	Home,
	KeyRound,
	ListChecks,
	Network,
	PlugZap,
	RefreshCw,
	Settings as SettingsIcon,
	ShieldCheck,
	SlidersHorizontal,
	TerminalSquare,
	UserRound,
	Workflow,
	XCircle,
} from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from 'react';

import { useControlPlane } from '../hooks/useControlPlane';
import { useMotionPreference, usePageMotion } from '../motion/useControlMotion';
import { createWorkflowWithBody } from '../api/client';
import type { Overview } from '../api/types';
import { Badge, DataTable, Drawer, EmptyState, StatusDot } from '../components/primitives';
import { ActiveProjectsPage } from '../features/active-projects/ActiveProjectsPage';
import type { Language, ProjectStatusView } from '../features/active-projects/ActiveProjectsPage';
import { WorkbenchPage } from '../features/workbench/WorkbenchPage';
import { useI18n } from '../i18n/I18nProvider';
import { JobsApprovalsPage } from '../features/jobs-approvals/JobsApprovalsPage';
import { WorkflowsPage } from '../features/workflows/WorkflowsPage';
import { AgentsPage } from '../features/agents/AgentsPage';
import { ModelGatewayPage } from '../features/model-gateway/ModelGatewayPage';
import { SettingsPage } from '../features/settings/SettingsPage';
import type { SettingsTab } from '../features/settings/SettingsPage';
import {
	AuditPage,
	CommandCenterPage,
	EvidencePage,
	GovernancePage,
	IntegrationsPage,
	MemoryPage,
	PolicySecurityPage,
	WorkspacesPage,
} from '../features/pages';
import { countByStatus, shortId, toneForStatus } from '../lib/format';

const SELECTED_PROJECT_STORAGE_KEY = 'aido:selectedProjectId';

function sumRecordedCost(rows: Overview['costUsage']) {
	const amounts = rows.map((row) => Number(row.amountUsd)).filter((amount) => Number.isFinite(amount));
	return amounts.length ? amounts.reduce((sum, amount) => sum + amount, 0) : null;
}

const pageIds = [
	'workbench',
	'projects-active',
	'projects-finished',
	'projects-error',
	'projects-cancelled',
	'command',
	'workflows',
	'jobs',
	'agents',
	'workspaces',
	'policy',
	'memory',
	'evidence',
	'models',
	'governance',
	'audit',
	'integrations',
	'settings-projects',
	'settings-user',
	'settings-cli',
	'settings-api',
	'settings-parameters',
	'settings-maintainers',
	'settings-workspaces',
	'settings-defaults',
] as const;

type PageId = (typeof pageIds)[number];

const projectStatusByPage: Partial<Record<PageId, ProjectStatusView>> = {
	'projects-active': 'active',
	'projects-finished': 'finished',
	'projects-error': 'error',
	'projects-cancelled': 'cancelled',
};

const settingsSectionByPage: Partial<Record<PageId, SettingsTab>> = {
	'settings-projects': 'projects',
	'settings-user': 'user',
	'settings-cli': 'cli',
	'settings-api': 'api',
	'settings-parameters': 'parameters',
	'settings-maintainers': 'maintainers',
	'settings-workspaces': 'workspaces',
	'settings-defaults': 'defaults',
};

const routeAliases: Record<string, PageId> = {
	active: 'projects-active',
	home: 'workbench',
	ide: 'workbench',
	settings: 'settings-projects',
	workspace: 'workbench',
};

const navCopy = {
	en: {
		workbench: 'Workbench',
		workspace: 'Workspace',
		projects: 'Projects',
		active: 'Active',
		finished: 'Finished',
		error: 'With error',
		cancelled: 'Cancelled',
		command: 'Command Center',
		operations: 'Operations',
		workflows: 'Workflows',
		jobs: 'Jobs & Approvals',
		agents: 'Agents',
		workspaces: 'Workspaces',
		policy: 'Policy & Security',
		memory: 'Memory & Retrieval',
		evidence: 'Evidence & QA',
		models: 'Model Gateway',
		governance: 'Governance',
		audit: 'Audit Log',
		integrations: 'Integrations',
		settings: 'Settings',
		projectSelection: 'Project selection',
		userSettings: 'User settings',
		cliSettings: 'CLI settings',
		apiSettings: 'API settings',
		parameters: 'Parameters',
		maintainers: 'Mantenedores',
		defaults: 'Defaults',
		execute: 'Execute',
		guard: 'Guard',
		history: 'History',
	},
	es: {
		workbench: 'Workbench',
		workspace: 'Workspace',
		projects: 'Proyectos',
		active: 'Activos',
		finished: 'Finalizados',
		error: 'Con error',
		cancelled: 'Cancelados',
		command: 'Centro de comandos',
		operations: 'Operaciones',
		workflows: 'Flujos de trabajo',
		jobs: 'Trabajos y aprobaciones',
		agents: 'Agentes',
		workspaces: 'Workspaces',
		policy: 'Politica y seguridad',
		memory: 'Memoria y busqueda',
		evidence: 'Evidencia y QA',
		models: 'Gateway de modelos',
		governance: 'Gobierno',
		audit: 'Auditoria',
		integrations: 'Integraciones',
		settings: 'Configuraciones',
		projectSelection: 'Seleccion de proyecto',
		userSettings: 'Configuraciones de usuario',
		cliSettings: 'Configuracion de cli',
		apiSettings: "Configuracion de api's",
		parameters: 'Configuraciones de parametros',
		maintainers: 'Mantenedores',
		defaults: 'Defaults',
		execute: 'Ejecucion',
		guard: 'Guardia',
		history: 'Historial',
	},
} satisfies Record<Language, Record<string, string>>;

function currentHash(): PageId {
	const value = window.location.hash.replace('#', '');
	return routeAliases[value] ?? (pageIds.includes(value as PageId) ? (value as PageId) : 'workbench');
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
	const [settingsWizardRequest, setSettingsWizardRequest] = useState(0);
	const [approvalDrawerOpen, setApprovalDrawerOpen] = useState(false);
	const [eventDrawerOpen, setEventDrawerOpen] = useState(false);
	const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
	const [commandFilter, setCommandFilter] = useState('');
	const [eventFilter, setEventFilter] = useState('');
	const state = useControlPlane();
	const motionRef = usePageMotion(page);
	const labels = navCopy[bilingualLanguage];

	const navigateTo = useCallback((nextPage: PageId) => {
		window.location.hash = nextPage;
		setPage(nextPage);
	}, []);

	const openNewProjectWizard = useCallback(() => {
		setSettingsWizardRequest((value) => value + 1);
		navigateTo('settings-projects');
	}, [navigateTo]);

	const changeLanguage = useCallback((nextLanguage: string) => {
		setLanguage(nextLanguage);
	}, [setLanguage]);

	useEffect(() => {
		document.documentElement.lang = language;
	}, [language]);

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
			}
			if (!editableTarget && event.ctrlKey && event.altKey) {
				const key = event.key.toLowerCase();
				const code = event.code;
				if (key === 'a' || code === 'KeyA') {
					event.preventDefault();
					setApprovalDrawerOpen(true);
				}
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
	const runningJobs = overview ? countByStatus(overview.jobs, 'running') : 0;
	const pendingApprovals = overview?.actionRequests.filter((item) => item.status === 'pending').length ?? 0;
	const executableRuntimes = state.runtimeProviders?.providers.filter((provider) => provider.executable).length ?? 0;
	const recordedCost = useMemo(() => (overview ? sumRecordedCost(overview.costUsage) : null), [overview]);
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
	const commandActions = useMemo(
		() => [
			{
				id: 'create-workflow',
				label: 'Create Workflow',
				hint: 'Create an idea-to-PR workflow for the selected project',
				run: async () => {
					if (!selectedProject?.id) return;
					const title = `Palette workflow ${new Date().toISOString()}`;
					await state.mutate((token) =>
						createWorkflowWithBody(token, {
							projectId: selectedProject.id,
							title,
							kind: 'idea_to_pr',
							metadata: { source: 'command_palette' },
						}),
					);
					navigateTo('workflows');
					setCommandPaletteOpen(false);
				},
			},
			{ id: 'go-workflows', label: 'Go to Workflows', hint: 'Inspect workflow runs, steps, evidence and tool calls', run: () => { navigateTo('workflows'); setCommandPaletteOpen(false); } },
			{ id: 'open-approvals', label: 'Open Pending Approvals', hint: 'Review pending granular action requests', run: () => { setApprovalDrawerOpen(true); setCommandPaletteOpen(false); } },
			{ id: 'go-jobs', label: 'Go to Jobs & Approvals', hint: 'Open the queue and approval surface', run: () => { navigateTo('jobs'); setCommandPaletteOpen(false); } },
			{ id: 'go-governance', label: 'Go to Governance', hint: 'Review risks, decisions and next steps', run: () => { navigateTo('governance'); setCommandPaletteOpen(false); } },
			{ id: 'go-policy', label: 'Go to Policy & Security', hint: 'Inspect policy decisions and sandbox posture', run: () => { navigateTo('policy'); setCommandPaletteOpen(false); } },
			{ id: 'open-events', label: 'Open Event Drawer', hint: 'Inspect recent operational events', run: () => { setEventDrawerOpen(true); setCommandPaletteOpen(false); } },
			{ id: 'search-events', label: 'Search Events', hint: 'Search events by type, severity, id or payload', run: () => { setEventDrawerOpen(true); setCommandPaletteOpen(false); } },
		],
		[navigateTo, selectedProject?.id, state.mutate],
	);
	const filteredCommands = commandActions.filter((action) => {
		const query = commandFilter.trim().toLowerCase();
		return !query || action.label.toLowerCase().includes(query) || action.hint.toLowerCase().includes(query);
	});
	const navigationSections = useMemo(
		() => [
			{
				id: 'workspace',
				label: labels.workspace,
				items: [
					{ page: 'workbench' as PageId, label: labels.workbench, description: bilingualLanguage === 'es' ? 'Chat, contexto y ejecucion' : 'Chat, context and execution', icon: Home, primary: true },
				],
			},
			{
				id: 'projects',
				label: labels.projects,
				items: [
					{ page: 'projects-active' as PageId, label: labels.projects, description: bilingualLanguage === 'es' ? 'Catalogo operacional' : 'Operational catalog', icon: FolderKanban, primary: true },
					{ page: 'projects-active' as PageId, label: labels.active, description: bilingualLanguage === 'es' ? 'Listos para trabajar' : 'Ready to work', icon: Gauge },
					{ page: 'projects-finished' as PageId, label: labels.finished, description: bilingualLanguage === 'es' ? 'Cerrados' : 'Closed', icon: CheckCircle2 },
					{ page: 'projects-error' as PageId, label: labels.error, description: bilingualLanguage === 'es' ? 'Con excepciones' : 'With exceptions', icon: XCircle },
					{ page: 'projects-cancelled' as PageId, label: labels.cancelled, description: bilingualLanguage === 'es' ? 'Cancelados' : 'Cancelled', icon: Ban },
				],
			},
			{
				id: 'operations',
				label: labels.operations,
				items: [
					{ page: 'command' as PageId, label: labels.command, description: bilingualLanguage === 'es' ? 'Ejecucion gobernada' : 'Governed execution', icon: TerminalSquare, primary: true },
					{ page: 'workflows' as PageId, label: labels.workflows, description: bilingualLanguage === 'es' ? 'Runs y pasos' : 'Runs and steps', icon: Workflow },
					{ page: 'jobs' as PageId, label: labels.jobs, description: bilingualLanguage === 'es' ? 'Cola y permisos' : 'Queue and gates', icon: ClipboardCheck },
					{ page: 'agents' as PageId, label: labels.agents, description: bilingualLanguage === 'es' ? 'Perfiles runtime' : 'Runtime profiles', icon: Bot },
					{ page: 'workspaces' as PageId, label: labels.workspaces, description: bilingualLanguage === 'es' ? 'Aislamiento por tarea' : 'Task isolation', icon: GitBranch },
					{ page: 'audit' as PageId, label: labels.history, description: bilingualLanguage === 'es' ? 'Eventos recientes' : 'Recent events', icon: History },
				],
			},
			{
				id: 'knowledge',
				label: bilingualLanguage === 'es' ? 'Gobierno' : 'Governance',
				items: [
					{ page: 'policy' as PageId, label: labels.policy, description: bilingualLanguage === 'es' ? 'Sandbox y permisos' : 'Sandbox and permissions', icon: ShieldCheck },
					{ page: 'memory' as PageId, label: labels.memory, description: bilingualLanguage === 'es' ? 'Contexto local' : 'Local context', icon: Brain },
					{ page: 'evidence' as PageId, label: labels.evidence, description: bilingualLanguage === 'es' ? 'QA y artefactos' : 'QA and artifacts', icon: FileCheck2 },
					{ page: 'models' as PageId, label: labels.models, description: bilingualLanguage === 'es' ? 'Modelos y costo' : 'Models and cost', icon: Network },
					{ page: 'governance' as PageId, label: labels.governance, description: bilingualLanguage === 'es' ? 'Riesgos y ADR' : 'Risks and ADRs', icon: KeyRound },
					{ page: 'audit' as PageId, label: labels.audit, description: bilingualLanguage === 'es' ? 'Trazabilidad' : 'Traceability', icon: History },
					{ page: 'integrations' as PageId, label: labels.integrations, description: bilingualLanguage === 'es' ? 'MCP y proveedores' : 'MCP and providers', icon: Archive },
				],
			},
			{
				id: 'settings',
				label: labels.settings,
				items: [
					{ page: 'settings-projects' as PageId, label: labels.settings, description: bilingualLanguage === 'es' ? 'Preferencias base' : 'Baseline preferences', icon: SettingsIcon, primary: true },
					{ page: 'settings-projects' as PageId, label: labels.projectSelection, description: bilingualLanguage === 'es' ? 'Proyecto operativo' : 'Operational project', icon: FolderKanban },
					{ page: 'settings-user' as PageId, label: labels.userSettings, description: bilingualLanguage === 'es' ? 'Idioma y densidad' : 'Language and density', icon: UserRound },
					{ page: 'settings-cli' as PageId, label: labels.cliSettings, description: bilingualLanguage === 'es' ? 'Terminal local' : 'Local terminal', icon: Code2 },
					{ page: 'settings-api' as PageId, label: labels.apiSettings, description: bilingualLanguage === 'es' ? 'Endpoints v1' : 'v1 endpoints', icon: PlugZap },
					{ page: 'settings-parameters' as PageId, label: labels.parameters, description: bilingualLanguage === 'es' ? 'Politicas de modelo' : 'Model policies', icon: ListChecks },
					{ page: 'settings-maintainers' as PageId, label: labels.maintainers, description: bilingualLanguage === 'es' ? 'Catalogos' : 'Catalogs', icon: SlidersHorizontal },
					{ page: 'settings-workspaces' as PageId, label: labels.workspaces, ariaLabel: bilingualLanguage === 'es' ? 'Configuracion de workspaces' : 'Workspace settings', description: bilingualLanguage === 'es' ? 'Raices IDE' : 'IDE roots', icon: GitBranch },
					{ page: 'settings-defaults' as PageId, label: labels.defaults, description: bilingualLanguage === 'es' ? 'Colapsado' : 'Collapsed', icon: SettingsIcon },
				],
			},
		],
		[bilingualLanguage, labels],
	);
	const pageContent = () => {
		if (state.loading || !overview) {
			return <EmptyState title="Loading control plane" body="Waiting for FastAPI v1, SQLite and runtime providers." />;
		}
		if (state.error) {
			return <EmptyState title="Control plane unavailable" body={state.error} />;
		}
		if (page === 'workbench') {
			return (
				<WorkbenchPage
					overview={overview}
					selectedProject={selectedProject}
					runtimeProviders={state.runtimeProviders}
					mutate={state.mutate}
					onSelectProject={setOperationalProject}
					onCreateProject={openNewProjectWizard}
					onOpenCommandCenter={() => navigateTo('command')}
					onOpenWorkflows={() => navigateTo('workflows')}
					onOpenJobs={() => navigateTo('jobs')}
					onOpenWorkspaces={() => navigateTo('workspaces')}
					onOpenEvidence={() => navigateTo('evidence')}
				/>
			);
		}
		const projectStatusView = projectStatusByPage[page];
		if (projectStatusView) {
			return (
				<ActiveProjectsPage
					overview={overview}
					runtimeProviders={state.runtimeProviders}
					selectedProject={selectedProject}
					statusView={projectStatusView}
					language={bilingualLanguage}
					onSelectProject={setOperationalProject}
					onOpenSettings={() => navigateTo('settings-projects')}
					onCreateProject={openNewProjectWizard}
				/>
			);
		}
		switch (page) {
			case 'command':
				return <CommandCenterPage overview={overview} selectedProject={selectedProject} runtimeProviders={state.runtimeProviders} mutate={state.mutate} onSelectProject={setOperationalProject} />;
			case 'workflows':
				return <WorkflowsPage overview={overview} token={state.token} />;
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
			case 'settings-projects':
			case 'settings-user':
			case 'settings-cli':
			case 'settings-api':
			case 'settings-parameters':
			case 'settings-maintainers':
			case 'settings-workspaces':
			case 'settings-defaults':
				return (
					<SettingsPage
						overview={overview}
						selectedProject={selectedProject}
						onSelectProject={setOperationalProject}
						onProjectCreated={setOperationalProject}
						mutate={state.mutate}
						wizardRequest={settingsWizardRequest}
						section={settingsSectionByPage[page]}
						language={bilingualLanguage}
					/>
				);
			default:
				return <ActiveProjectsPage overview={overview} runtimeProviders={state.runtimeProviders} selectedProject={selectedProject} language={bilingualLanguage} onSelectProject={setOperationalProject} onOpenSettings={() => navigateTo('settings-projects')} onCreateProject={openNewProjectWizard} />;
		}
	};

	return (
		<div className="app-shell">
			<span className="console-grid" aria-hidden="true" />
			<aside className="sidebar" aria-label={t('app.global.primaryNavigation', 'Primary navigation')}>
				<div className="brand-mark">
					<div className="brand-orb" aria-hidden="true"><span /></div>
					<div>
						<div className="brand-kicker">{t('app.brand.kicker', 'Windows native · v1 only')}</div>
						<h1 className="brand-title">{t('app.brand.title', 'AIDO Control Center')}</h1>
					</div>
				</div>
				<nav className="nav-list ide-nav" aria-label={t('app.global.primaryNavigation', 'Primary navigation')}>
					{navigationSections.map((section) => (
						<section className="nav-section" key={section.id} aria-label={section.label}>
							<div className="nav-section-label">{section.label}</div>
							<div className="nav-sublist">
								{section.items.map((item) => {
									const Icon = item.icon;
									return (
										<button
											key={`${section.id}-${item.page}-${item.label}`}
											className={`nav-item${item.primary ? ' nav-primary' : ''}`}
											type="button"
											aria-label={item.ariaLabel ?? item.label}
											aria-current={page === item.page ? 'page' : undefined}
											onClick={() => navigateTo(item.page)}
										>
											<Icon aria-hidden="true" size={16} />
											<span>{item.label}</span>
											<small className="nav-item-meta" aria-hidden="true">{item.description}</small>
										</button>
									);
								})}
							</div>
						</section>
					))}
				</nav>
				<div className="sidebar-footer">
					<div className="inline"><StatusDot tone={state.connected ? 'ok' : 'warn'} /> {state.connected ? t('app.global.sseConnected', 'SSE connected') : t('app.global.pollingFallback', 'polling fallback')}</div>
					<div>{state.lastUpdatedAt ? `${t('app.global.updated', 'Updated')} ${new Date(state.lastUpdatedAt).toLocaleTimeString()}` : t('app.global.waitingForData', 'Waiting for data')}</div>
				</div>
			</aside>
			<main className="main-area">
				<header className="topbar">
					<div className="topbar-primary">
						<div>
							<div className="page-kicker">{t('app.global.projectWorkspaceRuntime', 'Project · Workspace · Runtime')}</div>
							<h2 className="topbar-title">{selectedProject?.name ?? t('app.global.runtimeProject', 'Runtime project')}</h2>
						</div>
						<div className="topbar-actions">
							<div className="language-switch" role="group" aria-label={t('app.global.languageControl', 'Language control')}>
								{(languages.length ? languages : [
									{ code: 'es', name: 'Spanish', nativeName: 'Espanol', enabled: true },
									{ code: 'en', name: 'English', nativeName: 'English', enabled: true },
								]).map((item) => (
									<button key={item.code} className="button" type="button" aria-pressed={language === item.code} onClick={() => changeLanguage(item.code)}>
										{item.code.toUpperCase()}
									</button>
								))}
							</div>
							<Badge tone={pendingApprovals ? 'warn' : 'ok'}>{pendingApprovals} {t('app.global.approvals', 'approvals')}</Badge>
							<Badge tone={runningJobs ? 'warn' : 'ok'}>{runningJobs} {t('app.global.running', 'running')}</Badge>
							<Badge>
								{recordedCost === null
									? t('app.global.costUnavailable', 'cost unavailable')
									: `${recordedCost.toFixed(2)} ${t('app.global.recordedUsd', 'recorded USD')}`}
							</Badge>
							<button className="button" type="button" onClick={() => setCommandPaletteOpen(true)}>
								{t('app.global.openCommandPalette', 'Open command palette')}
							</button>
							<button className="button" type="button" onClick={() => setApprovalDrawerOpen(true)}>
								{t('app.global.openApprovalsDrawer', 'Open approvals drawer')}
							</button>
							<button className="button" type="button" onClick={() => setEventDrawerOpen(true)}>
								{t('app.global.openEventDrawer', 'Open event drawer')}
							</button>
							<button className="icon-button" type="button" aria-label={t('app.global.refreshState', 'Refresh state')} onClick={() => void state.refresh()}>
								<RefreshCw aria-hidden="true" size={18} />
							</button>
						</div>
					</div>
				</header>
				<div className="status-strip" aria-label={t('app.global.globalStatus', 'Global status')}>
					<Badge tone={state.connected ? 'ok' : 'warn'}>SSE {state.connected ? 'connected' : 'fallback'}</Badge>
					<Badge tone="ok">{t('app.global.sqliteCanonical', 'SQLite canonical')}</Badge>
					<Badge tone={executableRuntimes ? 'ok' : 'warn'}>{executableRuntimes} executable runtimes</Badge>
					<Badge tone="ok">{t('app.global.policyEngineActive', 'Policy engine active')}</Badge>
					<Badge>{t('app.global.sandboxGated', 'Sandbox gated')}</Badge>
				</div>
				<section ref={motionRef} className="content-frame motion-scope" aria-live="polite">
					{pageContent()}
				</section>
			</main>
			{overview ? (
				<>
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
					<Drawer label="Command palette" open={commandPaletteOpen} onClose={() => setCommandPaletteOpen(false)}>
						<div className="drawer-body">
							<div className="field">
								<label htmlFor="command-palette-filter">Command palette filter</label>
								<input
									id="command-palette-filter"
									className="input"
									value={commandFilter}
									onChange={(event) => setCommandFilter(event.target.value)}
									placeholder="Filter actions"
									autoFocus
								/>
							</div>
							<div className="command-list" role="list">
								{filteredCommands.map((action) => (
									<button key={action.id} className="command-item" type="button" onClick={() => void action.run()}>
										<span>{action.label}</span>
										<small>{action.hint}</small>
									</button>
								))}
								{filteredCommands.length === 0 ? <EmptyState title="No commands" body="Try workflows, approvals, governance, policy or events." /> : null}
							</div>
						</div>
					</Drawer>
				</>
			) : null}
		</div>
	);
}
