/**
 * Typed route table: maps every {@link AppRoute} to a render function plus an optional
 * chunk-preload hook. Most heavy feature pages are declared with `React.lazy` at module scope
 * (never inside a component) so each becomes its own build chunk; Home, the projects views, the
 * Workbench, review and the runtime gateway stay eager. `preloadRoute` warms a lazy chunk on intent.
 * @author Rodrigo Mason
 */
import type { ReactNode } from 'react';
import { lazy } from 'react';

import type {
	Overview,
	Project,
	RetrievalStatus,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../api/types';
import { HomePage } from '../features/home/HomePage';
import { ModelGatewayPage } from '../features/model-gateway/ModelGatewayPage';
import type { Language } from '../features/projects/ProjectsPage';
import { ProjectsPage } from '../features/projects/ProjectsPage';
import { ReviewPage } from '../features/review/ReviewPage';
import { WorkbenchPage } from '../features/workbench/WorkbenchPage';
import type { WorkspaceMode } from '../features/workspace/useProjectDiscovery';
import type { AppRoute } from './routing';

const importShell = () => import('../features/shell/ShellPage');
const ShellPage = lazy(() => importShell().then((m) => ({ default: m.ShellPage })));

const importWorkbench = () => Promise.resolve({ WorkbenchPage });

const importWorkflows = () => import('../features/workflows/WorkflowsPage');
const WorkflowsPage = lazy(() => importWorkflows().then((m) => ({ default: m.WorkflowsPage })));

const importReview = () => Promise.resolve({ ReviewPage });

const importModelGateway = () => Promise.resolve({ ModelGatewayPage });

const importAgents = () => import('../features/agents/AgentsPage');
const AgentsPage = lazy(() => importAgents().then((m) => ({ default: m.AgentsPage })));

const importPolicy = () => import('../features/policy/PolicySecurityPage');
const PolicySecurityPage = lazy(() =>
	importPolicy().then((m) => ({ default: m.PolicySecurityPage })),
);
const importEvidence = () => import('../features/evidence/EvidencePage');
const EvidencePage = lazy(() => importEvidence().then((m) => ({ default: m.EvidencePage })));
const importGovernance = () => import('../features/governance/GovernancePage');
const GovernancePage = lazy(() => importGovernance().then((m) => ({ default: m.GovernancePage })));

const importIntegrations = () => import('../features/integrations/IntegrationsPage');
const IntegrationsPage = lazy(() =>
	importIntegrations().then((m) => ({ default: m.IntegrationsPage })),
);
const importAssessment = () => import('../features/assessment/AssessmentPage');
const AssessmentPage = lazy(() => importAssessment().then((m) => ({ default: m.AssessmentPage })));
const importAudit = () => import('../features/audit/AuditPage');
const AuditPage = lazy(() => importAudit().then((m) => ({ default: m.AuditPage })));
const importMemory = () => import('../features/memory/MemoryPage');
const MemoryPage = lazy(() => importMemory().then((m) => ({ default: m.MemoryPage })));
const importWorkspaces = () => import('../features/workspaces/WorkspacesPage');
const WorkspacesPage = lazy(() => importWorkspaces().then((m) => ({ default: m.WorkspacesPage })));

/** Token-injecting write runner from the control plane. */
export type Mutate = <T>(
	operation: (token: string) => Promise<T>,
	options?: { awaitRefresh?: boolean },
) => Promise<T>;

/** Everything a route render needs, assembled by App once `overview` is loaded. */
export interface RouteContext {
	overview: Overview;
	selectedProject: Project | null;
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	retrievalStatus: RetrievalStatus | null;
	token: string;
	mutate: Mutate;
	refresh: (silent?: boolean) => Promise<void>;
	language: Language;
	navigateTo: (route: AppRoute, runId?: string) => void;
	/** Deep-links a concrete workflow run into the shell Inspector (`#workflows?run=<id>`). */
	openRun: (runId: string) => void;
	onSelectProject: (projectId: string) => void;
	openWorkspaceDialog: (mode?: WorkspaceMode) => void;
	/** Opens the Settings modal at the given section. */
	openSettings: (section?: string) => void;
	/** Shell-owned active session selection: drives the thread/loop shell center (the Workbench). */
	selectedSessionId: string;
	onSelectSession: (sessionId: string) => void;
}

interface RouteEntry {
	render: (ctx: RouteContext, route: AppRoute) => ReactNode;
	/** Fetches the route's chunk ahead of navigation; absent for eager routes. */
	preload?: () => Promise<unknown>;
}

/** The single source of truth mapping each route to its (possibly lazy) page. */
export const routeTable: Record<AppRoute, RouteEntry> = {
	threads: {
		render: (ctx) => <ShellPage ctx={ctx} />,
		preload: importShell,
	},
	home: {
		render: (ctx) => (
			<HomePage
				overview={ctx.overview}
				runtimeProviders={ctx.runtimeProviders}
				selectedProject={ctx.selectedProject}
				language={ctx.language}
				onSelectProject={ctx.onSelectProject}
				onCreateProject={() => ctx.openWorkspaceDialog('create_workspace')}
				onOpenFolder={() => ctx.openWorkspaceDialog('open_folder')}
				onOpenWorkbench={() => ctx.navigateTo('workbench')}
				onOpenProjects={() => ctx.navigateTo('projects')}
				onOpenReview={() => ctx.navigateTo('review-board')}
				onOpenRuns={() => ctx.openSettings('advanced')}
				onOpenRuntimes={() => ctx.openSettings('providers-cli')}
			/>
		),
	},
	workbench: {
		render: (ctx) => (
			<WorkbenchPage
				overview={ctx.overview}
				selectedProject={ctx.selectedProject}
				runtimeProviders={ctx.runtimeProviders}
				runtimeProviderConfiguration={ctx.runtimeProviderConfiguration}
				mutate={ctx.mutate}
				token={ctx.token}
				onSelectProject={ctx.onSelectProject}
				onCreateProject={() => ctx.openWorkspaceDialog('open_folder')}
				onOpenJobs={() => ctx.navigateTo('review-board')}
				onOpenEvidence={() => ctx.navigateTo('evidence')}
				onOpenSettings={() => ctx.openSettings('goal')}
				onOpenRuntimeSetup={() => ctx.openSettings('providers-cli')}
				onRefresh={() => ctx.refresh(true)}
			/>
		),
		preload: importWorkbench,
	},
	projects: {
		render: (ctx) => (
			<ProjectsPage
				overview={ctx.overview}
				selectedProject={ctx.selectedProject}
				onSelectProject={ctx.onSelectProject}
				onOpenSettings={() => ctx.openSettings('goal')}
				onCreateProject={() => ctx.openWorkspaceDialog('open_folder')}
			/>
		),
	},
	workflows: {
		render: (ctx) => <WorkflowsPage overview={ctx.overview} onOpenRun={ctx.openRun} />,
		preload: importWorkflows,
	},
	'review-board': {
		render: (ctx) => (
			<ReviewPage
				overview={ctx.overview}
				token={ctx.token}
				mutate={ctx.mutate}
				refresh={ctx.refresh}
			/>
		),
		preload: importReview,
	},
	agents: {
		render: (ctx) => (
			<AgentsPage
				overview={ctx.overview}
				runtimeProviders={ctx.runtimeProviders}
				mutate={ctx.mutate}
			/>
		),
		preload: importAgents,
	},
	workspaces: {
		render: (ctx) => <WorkspacesPage overview={ctx.overview} mutate={ctx.mutate} />,
		preload: importWorkspaces,
	},
	policy: {
		render: (ctx) => <PolicySecurityPage overview={ctx.overview} mutate={ctx.mutate} />,
		preload: importPolicy,
	},
	memory: {
		render: (ctx) => (
			<MemoryPage
				overview={ctx.overview}
				retrievalStatus={ctx.retrievalStatus}
				selectedProject={ctx.selectedProject}
			/>
		),
		preload: importMemory,
	},
	evidence: {
		render: (ctx) => <EvidencePage overview={ctx.overview} token={ctx.token} />,
		preload: importEvidence,
	},
	models: {
		render: (ctx) => (
			<ModelGatewayPage
				overview={ctx.overview}
				runtimeProviders={ctx.runtimeProviders}
				token={ctx.token}
				onRefreshRuntimeProviders={() => ctx.refresh(true)}
			/>
		),
		preload: importModelGateway,
	},
	governance: {
		render: (ctx) => (
			<GovernancePage
				overview={ctx.overview}
				selectedProject={ctx.selectedProject}
				mutate={ctx.mutate}
			/>
		),
		preload: importGovernance,
	},
	assessment: {
		render: (ctx) => (
			<AssessmentPage
				overview={ctx.overview}
				selectedProject={ctx.selectedProject}
				token={ctx.token}
			/>
		),
		preload: importAssessment,
	},
	audit: {
		render: (ctx) => <AuditPage overview={ctx.overview} />,
		preload: importAudit,
	},
	integrations: {
		render: (ctx) => <IntegrationsPage overview={ctx.overview} mutate={ctx.mutate} />,
		preload: importIntegrations,
	},
};

/** Renders the page for `route`, supplying it the shared context. */
export function renderRoute(route: AppRoute, ctx: RouteContext): ReactNode {
	return routeTable[route].render(ctx, route);
}

/** Warms a lazy route's chunk ahead of navigation (no-op for eager routes). */
export function preloadRoute(route: AppRoute): void {
	void routeTable[route]?.preload?.();
}
