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
import { Badge, DataTable, Drawer, EmptyState, StatusDot } from '../components/primitives';
import { ActiveProjectsPage } from '../features/active-projects/ActiveProjectsPage';
import type { Language, ProjectStatusView } from '../features/active-projects/ActiveProjectsPage';
import { useI18n } from '../i18n/I18nProvider';
import { RadialNavigation } from './RadialNavigation';
import type { RadialNavigationModule } from './RadialNavigation';
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

const pageIds = [
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
	settings: 'settings-projects',
};

const navCopy = {
	en: {
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
	return routeAliases[value] ?? (pageIds.includes(value as PageId) ? (value as PageId) : 'projects-active');
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
	const costToday = useMemo(
		() => overview?.costUsage.reduce((total, row) => total + Number(row.amountUsd ?? 0), 0) ?? 0,
		[overview],
	);
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
	const navigationModules = useMemo<RadialNavigationModule<PageId>[]>(() => {
		const copy = {
			active: bilingualLanguage === 'es' ? 'Proyectos activos listos para recibir trabajo operacional.' : 'Active projects ready to receive operational work.',
			finished: bilingualLanguage === 'es' ? 'Entregas cerradas para auditoria y trazabilidad.' : 'Closed delivery records for audit and traceability.',
			error: bilingualLanguage === 'es' ? 'Proyectos aislados para triage de excepciones.' : 'Exception lanes isolated for triage.',
			cancelled: bilingualLanguage === 'es' ? 'Registros cancelados visibles, no mutables.' : 'Cancelled records remain visible but non-operational.',
			execute: bilingualLanguage === 'es' ? 'Inicia workflows y comandos contra el proyecto seleccionado.' : 'Start workflows and commands against the selected project.',
			guard: bilingualLanguage === 'es' ? 'Revisa politicas, permisos y postura antes de ejecutar.' : 'Review policy, permissions and execution posture.',
			workflows: bilingualLanguage === 'es' ? 'Runs, pasos, evidencias y estados de los flujos.' : 'Runs, steps, evidence and workflow status.',
			jobs: bilingualLanguage === 'es' ? 'Cola operacional con aprobaciones granulares.' : 'Operational queue with granular approvals.',
			agents: bilingualLanguage === 'es' ? 'Perfiles tecnicos, permisos y runtimes de agentes.' : 'Technical profiles, permissions and agent runtimes.',
			workspaces: bilingualLanguage === 'es' ? 'Workspaces asignados por tarea y aislamiento.' : 'Task-owned workspaces and isolation.',
			policy: bilingualLanguage === 'es' ? 'Sandbox, tool calls y decisiones de seguridad.' : 'Sandbox, tool calls and security decisions.',
			memory: bilingualLanguage === 'es' ? 'Memoria local, recuperacion y estado del backend.' : 'Local memory, retrieval and backend status.',
			evidence: bilingualLanguage === 'es' ? 'Resultados de QA, artefactos y paquetes de evidencia.' : 'QA results, artifacts and evidence packages.',
			models: bilingualLanguage === 'es' ? 'Ruteo de modelos, proveedores, costos y benchmarks.' : 'Model routing, providers, cost and benchmarks.',
			governance: bilingualLanguage === 'es' ? 'Riesgos, decisiones y proximos pasos del proyecto.' : 'Risks, decisions and project next steps.',
			audit: bilingualLanguage === 'es' ? 'Eventos auditables de la operacion local.' : 'Auditable events from local operations.',
			history: bilingualLanguage === 'es' ? 'Ejecuciones recientes con resultado, evento y evidencia.' : 'Recent executions with result, event and evidence.',
			integrations: bilingualLanguage === 'es' ? 'MCP, proveedores y adaptadores externos.' : 'MCP, providers and external adapters.',
			projectSelection: bilingualLanguage === 'es' ? 'Selecciona el proyecto activo para mutaciones.' : 'Select the active project used by mutation surfaces.',
			userSettings: bilingualLanguage === 'es' ? 'Idioma, densidad, movimiento y preferencias.' : 'Language, density, motion and user preferences.',
			cliSettings: bilingualLanguage === 'es' ? 'Terminal y shell local para la operacion diaria.' : 'Terminal and local shell settings for daily operations.',
			apiSettings: bilingualLanguage === 'es' ? 'Endpoints, proveedores y postura de API local.' : 'Endpoints, providers and local API posture.',
			parameters: bilingualLanguage === 'es' ? 'Politicas de modelos y parametros operativos.' : 'Model policies and operational parameters.',
			maintainers: bilingualLanguage === 'es' ? 'Catalogos tecnicos: templates, equipos, agentes y providers.' : 'Technical catalogs: templates, teams, agents and providers.',
			settingsWorkspaces: bilingualLanguage === 'es' ? 'Raices importadas y proyectos detectados dentro de cada workspace.' : 'Imported roots and detected projects inside each workspace.',
			defaults: bilingualLanguage === 'es' ? 'Configuraciones base en un collapse separado.' : 'Baseline settings kept in a separate collapse.',
		};
		return [
			{
				id: 'projects',
				title: labels.projects,
				ariaLabel: 'Project navigation wheel',
				kicker: bilingualLanguage === 'es' ? 'Rueda de proyectos' : 'Project wheel',
				centerLabel: 'STATUS',
				primaryPage: 'projects-active',
				icon: FolderKanban,
				options: [
					{ id: 'active', label: labels.active, description: copy.active, page: 'projects-active', icon: Gauge, tone: 'moss' },
					{ id: 'finished', label: labels.finished, description: copy.finished, page: 'projects-finished', icon: CheckCircle2, tone: 'blue' },
					{ id: 'error', label: labels.error, description: copy.error, page: 'projects-error', icon: XCircle, tone: 'oxblood' },
					{ id: 'cancelled', label: labels.cancelled, description: copy.cancelled, page: 'projects-cancelled', icon: Ban, tone: 'amber' },
				],
			},
			{
				id: 'command',
				title: labels.command,
				ariaLabel: 'Command center navigation wheel',
				kicker: bilingualLanguage === 'es' ? 'Rueda operacional' : 'Command wheel',
				centerLabel: 'OPS',
				primaryPage: 'command',
				icon: TerminalSquare,
				options: [
					{ id: 'execute', label: labels.execute, shortLabel: 'Exec', description: copy.execute, page: 'command', icon: TerminalSquare, tone: 'moss' },
					{ id: 'guard', label: labels.guard, description: copy.guard, page: 'policy', icon: ShieldCheck, tone: 'amber' },
					{ id: 'workflows', label: labels.workflows, shortLabel: 'Flows', description: copy.workflows, page: 'workflows', icon: Workflow, tone: 'blue' },
					{ id: 'jobs', label: labels.jobs, shortLabel: 'Jobs', description: copy.jobs, page: 'jobs', icon: ClipboardCheck, tone: 'amber' },
					{ id: 'agents', label: labels.agents, description: copy.agents, page: 'agents', icon: Bot, tone: 'violet' },
					{ id: 'workspaces', label: labels.workspaces, shortLabel: 'Spaces', description: copy.workspaces, page: 'workspaces', icon: GitBranch, tone: 'moss' },
					{ id: 'policy', label: labels.policy, shortLabel: 'Policy', description: copy.policy, page: 'policy', icon: ShieldCheck, tone: 'oxblood' },
					{ id: 'memory', label: labels.memory, shortLabel: 'Memory', description: copy.memory, page: 'memory', icon: Brain, tone: 'violet' },
					{ id: 'evidence', label: labels.evidence, shortLabel: 'QA', description: copy.evidence, page: 'evidence', icon: FileCheck2, tone: 'moss' },
					{ id: 'models', label: labels.models, shortLabel: 'Models', description: copy.models, page: 'models', icon: Network, tone: 'blue' },
					{ id: 'governance', label: labels.governance, shortLabel: 'Gov', description: copy.governance, page: 'governance', icon: KeyRound, tone: 'amber' },
					{ id: 'history', label: labels.history, description: copy.history, page: 'audit', icon: History, tone: 'blue' },
					{ id: 'audit', label: labels.audit, description: copy.audit, page: 'audit', icon: History, tone: 'blue' },
					{ id: 'integrations', label: labels.integrations, shortLabel: 'MCP', description: copy.integrations, page: 'integrations', icon: Archive, tone: 'violet' },
				],
			},
			{
				id: 'settings',
				title: labels.settings,
				ariaLabel: 'Settings navigation wheel',
				kicker: bilingualLanguage === 'es' ? 'Rueda de configuracion' : 'Settings wheel',
				centerLabel: 'CONFIG',
				primaryPage: 'settings-projects',
				icon: SettingsIcon,
				options: [
					{ id: 'projects', label: labels.projectSelection, shortLabel: bilingualLanguage === 'es' ? 'Proyecto' : 'Project', description: copy.projectSelection, page: 'settings-projects', icon: FolderKanban, tone: 'moss' },
					{ id: 'user', label: labels.userSettings, shortLabel: 'User', description: copy.userSettings, page: 'settings-user', icon: UserRound, tone: 'blue' },
					{ id: 'cli', label: labels.cliSettings, description: copy.cliSettings, page: 'settings-cli', icon: Code2, tone: 'amber' },
					{ id: 'api', label: labels.apiSettings, description: copy.apiSettings, page: 'settings-api', icon: PlugZap, tone: 'violet' },
					{ id: 'parameters', label: labels.parameters, shortLabel: 'Params', description: copy.parameters, page: 'settings-parameters', icon: ListChecks, tone: 'blue' },
					{ id: 'maintainers', label: labels.maintainers, shortLabel: 'Catalogs', description: copy.maintainers, page: 'settings-maintainers', icon: SlidersHorizontal, tone: 'moss' },
					{ id: 'workspaces', label: labels.workspaces, shortLabel: 'Spaces', ariaLabel: 'Workspace settings', description: copy.settingsWorkspaces, page: 'settings-workspaces', icon: GitBranch, tone: 'violet' },
					{ id: 'defaults', label: labels.defaults, description: copy.defaults, page: 'settings-defaults', icon: SettingsIcon, tone: 'amber' },
				],
			},
		];
	}, [bilingualLanguage, labels]);

	const pageContent = () => {
		if (state.loading || !overview) {
			return <EmptyState title="Loading control plane" body="Waiting for FastAPI v1, SQLite and runtime providers." />;
		}
		if (state.error) {
			return <EmptyState title="Control plane unavailable" body={state.error} />;
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
				return <CommandCenterPage overview={overview} selectedProject={selectedProject} runtimeProviders={state.runtimeProviders} mutate={state.mutate} />;
			case 'workflows':
				return <WorkflowsPage overview={overview} token={state.token} />;
			case 'jobs':
				return <JobsApprovalsPage overview={overview} mutate={state.mutate} />;
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
				return <ModelGatewayPage overview={overview} runtimeProviders={state.runtimeProviders} token={state.token} />;
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
				<RadialNavigation modules={navigationModules} currentPage={page} onNavigate={navigateTo} />
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
							<Badge>{costToday.toFixed(2)} {t('app.global.usdToday', 'USD today')}</Badge>
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
