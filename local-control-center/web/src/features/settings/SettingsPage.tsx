/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { Bot, ExternalLink, FolderKanban, FolderPlus, GitBranch, Network, PlugZap, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import { useEffect, useMemo } from 'react';
import type { ComponentType, ReactNode } from 'react';
import type { LucideProps } from 'lucide-react';

import type { Overview, Project, RuntimeProviderConfiguration, RuntimeProviders } from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader } from '../../components/primitives';
import { Disclosure } from '../../components/Disclosure';
import { TranslationMaintainer } from '../../i18n/TranslationMaintainer';
import { toneForStatus } from '../../lib/format';
import { RuntimeSetupPanel } from '../runtime-setup/RuntimeSetupPanel';
import type { Language } from '../active-projects/ActiveProjectsPage';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;
type Tone = 'ok' | 'warn' | 'danger' | 'info';

/**
 * The seven configuration groups of the Settings hub, in run-readiness order.
 * Each value maps 1:1 to a `settings-*` route via App's `settingsGroupByPage`.
 */
export type SettingsGroupId = 'project' | 'runtime' | 'agents' | 'security' | 'workspaces' | 'integrations' | 'advanced';

const GROUP_ICON: Record<SettingsGroupId, ComponentType<LucideProps>> = {
	project: FolderKanban,
	runtime: Network,
	agents: Bot,
	security: ShieldCheck,
	workspaces: GitBranch,
	integrations: PlugZap,
	advanced: SlidersHorizontal,
};

type Copy = {
	kicker: string;
	title: string;
	summary: string;
	newProject: string;
	groups: Record<SettingsGroupId, { title: string; purpose: string }>;
	openModelGateway: string;
	openMemory: string;
	openAgents: string;
	openPolicy: string;
	openEvidence: string;
	openAudit: string;
	openIntegrations: string;
	advAllProjects: string;
	advCatalogAgents: string;
	advWorkspaceRoots: string;
	advParameters: string;
	advUserPreferences: string;
	advDefaults: string;
	advCatalogs: string;
};

const COPY: Record<Language, Copy> = {
	en: {
		kicker: 'Local runtime configuration',
		title: 'Settings',
		summary: 'Grouped configuration: pick a project, get a runtime ready, and keep catalogs, defaults and audit out of the daily operation surface.',
		newProject: 'New project',
		groups: {
			project: { title: 'Project', purpose: 'Pick the active project that work runs against.' },
			runtime: { title: 'Runtime & Models', purpose: 'Get a runtime provider ready and route models so an agent can actually run.' },
			agents: { title: 'Agents', purpose: 'Agent profiles and runtime readiness.' },
			security: { title: 'Security', purpose: 'Policy, sandbox posture and the audit trail.' },
			workspaces: { title: 'Workspaces', purpose: 'IDE-style workspace roots and import rules.' },
			integrations: { title: 'Integrations', purpose: 'MCP servers and external tool connections.' },
			advanced: { title: 'Advanced', purpose: 'Parameters, defaults, catalogs and personal preferences.' },
		},
		openModelGateway: 'Open Model Gateway',
		openMemory: 'Open Memory & Retrieval',
		openAgents: 'Open Agents',
		openPolicy: 'Open Policy & Security',
		openEvidence: 'Open Evidence & QA',
		openAudit: 'Open Audit Log',
		openIntegrations: 'Open Integrations',
		advAllProjects: 'All projects',
		advCatalogAgents: 'Catalog agents',
		advWorkspaceRoots: 'Workspace roots',
		advParameters: 'Parameters',
		advUserPreferences: 'User preferences',
		advDefaults: 'Default configurations',
		advCatalogs: 'Catalogs',
	},
	es: {
		kicker: 'Configuración del runtime local',
		title: 'Configuraciones',
		summary: 'Configuración agrupada: elige un proyecto, deja un runtime listo y mantén catálogos, valores por defecto y auditoría fuera de la superficie diaria.',
		newProject: 'Nuevo proyecto',
		groups: {
			project: { title: 'Proyecto', purpose: 'Elige el proyecto activo sobre el que se ejecuta el trabajo.' },
			runtime: { title: 'Runtime y modelos', purpose: 'Deja un runtime listo y rutea modelos para que un agente pueda ejecutar.' },
			agents: { title: 'Agentes', purpose: 'Perfiles de agentes y disponibilidad de runtime.' },
			security: { title: 'Seguridad', purpose: 'Política, sandbox y la traza de auditoría.' },
			workspaces: { title: 'Workspaces', purpose: 'Raíces de workspace estilo IDE y reglas de importación.' },
			integrations: { title: 'Integraciones', purpose: 'Servidores MCP y conexiones de herramientas externas.' },
			advanced: { title: 'Avanzado', purpose: 'Parámetros, valores por defecto, catálogos y preferencias personales.' },
		},
		openModelGateway: 'Abrir Model Gateway',
		openMemory: 'Abrir Memoria y búsqueda',
		openAgents: 'Abrir Agentes',
		openPolicy: 'Abrir Política y seguridad',
		openEvidence: 'Abrir Evidencia y QA',
		openAudit: 'Abrir Auditoría',
		openIntegrations: 'Abrir Integraciones',
		advAllProjects: 'Todos los proyectos',
		advCatalogAgents: 'Agentes de catálogo',
		advWorkspaceRoots: 'Raíces de workspace',
		advParameters: 'Parámetros',
		advUserPreferences: 'Preferencias de usuario',
		advDefaults: 'Configuraciones por defecto',
		advCatalogs: 'Catálogos',
	},
};

export function SettingsPage({
	overview,
	selectedProject,
	onSelectProject,
	onCreateProject,
	mutate,
	section = 'project',
	language = 'en',
	runtimeProviders,
	runtimeProviderConfiguration,
	token,
	onRefresh,
}: {
	overview: Overview;
	selectedProject: Project | null;
	onSelectProject: (projectId: string) => void;
	onCreateProject: () => void;
	mutate: Mutate;
	section?: SettingsGroupId;
	language?: Language;
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	token: string;
	onRefresh: () => Promise<unknown> | void;
}) {
	const copy = COPY[language];
	const activeProjects = overview.projects.filter((project) => project.status === 'active');

	// Deep link: bring the requested group card into view (instant under reduced-motion via global CSS).
	useEffect(() => {
		const element = document.getElementById(`settings-group-${section}`);
		element?.scrollIntoView({ block: 'start' });
	}, [section]);

	return (
		<>
			<PageHeader kicker={copy.kicker} title={copy.title} summary={copy.summary} />
			<div className="settings-hub">
				<SettingsGroup id="project" copy={copy} status={projectStatus(selectedProject, language)}>
					<ProjectBody
						overview={overview}
						activeProjects={activeProjects}
						selectedProject={selectedProject}
						onSelectProject={onSelectProject}
						onNewProject={onCreateProject}
						newProjectLabel={copy.newProject}
						advLabel={copy.advAllProjects}
					/>
				</SettingsGroup>

				<SettingsGroup id="runtime" copy={copy} status={runtimeStatus(runtimeProviders, language)}>
					<RuntimeBody
						overview={overview}
						runtimeProviders={runtimeProviders}
						runtimeProviderConfiguration={runtimeProviderConfiguration}
						token={token}
						onRefresh={onRefresh}
						copy={copy}
					/>
				</SettingsGroup>

				<SettingsGroup id="agents" copy={copy} status={agentsStatus(overview, language)}>
					<AgentsBody overview={overview} selectedProject={selectedProject} copy={copy} />
				</SettingsGroup>

				<SettingsGroup id="security" copy={copy} status={{ tone: 'ok', label: language === 'es' ? 'Token local' : 'Local token' }}>
					<SecurityBody overview={overview} copy={copy} />
				</SettingsGroup>

				<SettingsGroup id="workspaces" copy={copy} status={workspacesStatus(overview, language)}>
					<WorkspacesBody overview={overview} advLabel={copy.advWorkspaceRoots} />
				</SettingsGroup>

				<SettingsGroup id="integrations" copy={copy} status={{ tone: 'info', label: `${overview.mcpServers.length} MCP` }}>
					<IntegrationsBody overview={overview} copy={copy} />
				</SettingsGroup>

				<SettingsGroup id="advanced" copy={copy}>
					<AdvancedBody overview={overview} selectedProject={selectedProject} mutate={mutate} language={language} copy={copy} />
				</SettingsGroup>
			</div>
		</>
	);
}

function SettingsGroup({
	id,
	copy,
	status,
	children,
}: {
	id: SettingsGroupId;
	copy: Copy;
	status?: { tone: Tone; label: string };
	children: ReactNode;
}) {
	const Icon = GROUP_ICON[id];
	const group = copy.groups[id];
	return (
		<section id={`settings-group-${id}`} className="settings-group" aria-labelledby={`settings-group-${id}-title`}>
			<header className="settings-group-header">
				<Icon aria-hidden="true" size={20} />
				<div className="settings-group-heading">
					<h2 id={`settings-group-${id}-title`} className="surface-title">{group.title}</h2>
					<p className="settings-group-purpose">{group.purpose}</p>
				</div>
				{status ? <Badge tone={status.tone}>{status.label}</Badge> : null}
			</header>
			<div className="settings-group-body">{children}</div>
		</section>
	);
}

function ConsoleLink({ page, label }: { page: string; label: string }) {
	return (
		<a className="settings-console-link" href={`#${page}`}>
			<ExternalLink aria-hidden="true" size={15} />
			{label}
		</a>
	);
}

function projectStatus(selectedProject: Project | null, language: Language): { tone: Tone; label: string } {
	if (selectedProject) return { tone: 'ok', label: language === 'es' ? 'Seleccionado' : 'Selected' };
	return { tone: 'warn', label: language === 'es' ? 'Sin proyecto' : 'None selected' };
}

function runtimeStatus(runtimeProviders: RuntimeProviders | null, language: Language): { tone: Tone; label: string } {
	const executable = runtimeProviders?.providers?.filter((provider) => provider.executable).length ?? 0;
	const word = language === 'es' ? 'ejecutables' : 'executable';
	return { tone: executable > 0 ? 'ok' : 'warn', label: `${executable} ${word}` };
}

function agentsStatus(overview: Overview, language: Language): { tone: Tone; label: string } {
	const count = overview.agentProfiles.length;
	const word = language === 'es' ? 'perfiles' : 'profiles';
	return { tone: count > 0 ? 'info' : 'warn', label: `${count} ${word}` };
}

function workspacesStatus(overview: Overview, language: Language): { tone: Tone; label: string } {
	const count = overview.runtimeWorkspaces.length || overview.projects.length;
	const word = language === 'es' ? 'raíces' : 'roots';
	return { tone: 'ok', label: `${count} ${word}` };
}

function ProjectBody({
	overview,
	activeProjects,
	selectedProject,
	onSelectProject,
	onNewProject,
	newProjectLabel,
	advLabel,
}: {
	overview: Overview;
	activeProjects: Project[];
	selectedProject: Project | null;
	onSelectProject: (projectId: string) => void;
	onNewProject: () => void;
	newProjectLabel: string;
	advLabel: string;
}) {
	return (
		<>
			<div className="form-grid">
				<div className="field">
					<label htmlFor="operational-project">Operational project</label>
					<select
						id="operational-project"
						className="select"
						value={selectedProject?.id ?? ''}
						disabled={!activeProjects.length}
						onChange={(event) => onSelectProject(event.target.value)}
					>
						{activeProjects.length ? null : <option value="">No active projects</option>}
						{activeProjects.map((project) => (
							<option key={project.id} value={project.id}>{project.name}</option>
						))}
					</select>
					<div className="field-help">Only active projects can run work and receive changes.</div>
				</div>
				<div className="inline">
					<Badge tone={selectedProject ? 'ok' : 'warn'}>{selectedProject ? selectedProject.name : 'no operational project'}</Badge>
					<span className="field-help">Command Center and Governance use this explicit selection, not the first project the API returns.</span>
				</div>
				<button className="button primary settings-action" type="button" onClick={onNewProject}>
					<FolderPlus aria-hidden="true" size={16} />
					{newProjectLabel}
				</button>
			</div>
			<Disclosure title={advLabel}>
				<DataTable
					rows={overview.projects}
					caption={advLabel}
					empty={<EmptyState title="No project records" body="The runtime project is created automatically at startup." />}
					columns={[
						{ key: 'name', label: 'Name', render: (row) => row.name },
						{ key: 'path', label: 'Path', render: (row) => <span className="mono">{row.path}</span> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
						{ key: 'template', label: 'Template', render: (row) => <span className="mono">{row.templateId}</span> },
						{ key: 'source', label: 'Source', render: (row) => row.source },
					]}
				/>
			</Disclosure>
		</>
	);
}

function RuntimeBody({
	overview,
	runtimeProviders,
	runtimeProviderConfiguration,
	token,
	onRefresh,
	copy,
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	token: string;
	onRefresh: () => Promise<unknown> | void;
	copy: Copy;
}) {
	return (
		<>
			<RuntimeSetupPanel
				runtimeProviders={runtimeProviders}
				runtimeProviderConfiguration={runtimeProviderConfiguration}
				token={token}
				onRefresh={onRefresh}
			/>
			<div className="settings-readouts">
				<div><strong>Local Control API</strong><span className="mono">/api/v1</span></div>
				<div><strong>Provider catalog</strong><span className="muted">{overview.providers.length} records</span></div>
				<div><strong>Package manager</strong><span className="mono">corepack pnpm@10.24.0</span></div>
				<div><strong>Python runner</strong><span className="mono">uv run</span></div>
				<div><strong>Default shell</strong><span className="mono">PowerShell</span></div>
			</div>
			<div className="settings-deeplinks">
				<ConsoleLink page="models" label={copy.openModelGateway} />
				<ConsoleLink page="memory" label={copy.openMemory} />
			</div>
		</>
	);
}

function AgentsBody({ overview, selectedProject, copy }: { overview: Overview; selectedProject: Project | null; copy: Copy }) {
	const catalogAgents = useMemo(() => {
		if (!selectedProject) return [];
		const teamIds = new Set(overview.teams.filter((team) => team.projectId === selectedProject.id).map((team) => team.id));
		return overview.agents.filter((agent) => teamIds.has(agent.teamId));
	}, [overview.agents, overview.teams, selectedProject]);

	return (
		<>
			<p className="muted">{overview.agentProfiles.length} agent profiles. Runtime modes (CLI / API / Ollama / hybrid) sit behind the same governance layer.</p>
			<div className="settings-deeplinks">
				<ConsoleLink page="agents" label={copy.openAgents} />
			</div>
			<Disclosure title={copy.advCatalogAgents}>
				<DataTable
					rows={catalogAgents}
					caption={copy.advCatalogAgents}
					empty={<EmptyState title="No catalog agents" body="Agent profiles live in the Agents console. This shows catalog agents owned by the selected project's teams." />}
					columns={[
						{ key: 'name', label: 'Name', render: (row) => row.name },
						{ key: 'role', label: 'Role', render: (row) => row.role },
						{ key: 'provider', label: 'Provider', render: (row) => <span className="mono">{row.providerId}</span> },
					]}
				/>
			</Disclosure>
		</>
	);
}

function SecurityBody({ overview, copy }: { overview: Overview; copy: Copy }) {
	const sandboxProfile = overview.sandboxProfiles[0];
	return (
		<>
			<div className="inline">
				<Badge tone="ok">local token required</Badge>
				<Badge tone="ok">strict forms</Badge>
				<Badge tone={sandboxProfile ? 'ok' : 'warn'}>sandbox {sandboxProfile ? String(sandboxProfile.id) : 'default'}</Badge>
			</div>
			<p className="muted">Command classification, path boundaries, human gates and sandbox posture are managed in the Policy &amp; Security console. Write operations use the local security handshake.</p>
			<div className="settings-deeplinks">
				<ConsoleLink page="policy" label={copy.openPolicy} />
				<ConsoleLink page="evidence" label={copy.openEvidence} />
				<ConsoleLink page="audit" label={copy.openAudit} />
			</div>
		</>
	);
}

function WorkspacesBody({ overview, advLabel }: { overview: Overview; advLabel: string }) {
	const workspaceRoots = overview.runtimeWorkspaces.length
		? overview.runtimeWorkspaces
		: overview.projects.map((project) => ({
			id: project.id,
			path: project.path,
			projectId: project.id,
			taskId: 'project-root',
			ownerAgentId: project.source,
			isolationType: 'directory',
			status: project.status,
		}));
	const projectById = new Map(overview.projects.map((project) => [project.id, project]));

	return (
		<>
			<p className="muted">IDE-style workspace roots, imported folders and detected project lanes live here instead of the runtime Workspaces queue.</p>
			<div className="settings-readouts">
				<div><strong>Existing workspace</strong><span className="muted">Open a folder; detect package.json, pom.xml, pyproject.toml, requirements.txt, go.mod.</span></div>
				<div><strong>Create from zero</strong><span className="muted">Validate the workspace name before creating a directory and project record.</span></div>
			</div>
			<Badge tone="ok">duplicate-name guard active</Badge>
			<Disclosure title={advLabel}>
				<DataTable
					rows={workspaceRoots}
					caption={advLabel}
					empty={<EmptyState title="No workspace roots" body="Import an existing workspace or create one from the project wizard." />}
					columns={[
						{ key: 'root', label: 'Workspace root', render: (row) => <span className="mono">{row.path}</span> },
						{ key: 'project', label: 'Detected project', render: (row) => projectById.get(String(row.projectId))?.name ?? String(row.projectId ?? '') },
						{ key: 'mode', label: 'Mode', render: (row) => <span className="mono">{String(row.isolationType ?? 'directory')}</span> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					]}
				/>
			</Disclosure>
		</>
	);
}

function IntegrationsBody({ overview, copy }: { overview: Overview; copy: Copy }) {
	return (
		<>
			<p className="muted">{overview.mcpServers.length} MCP servers registered. Registration, transports and IDE connections live in the Integrations console; execution still goes through broker, policy and sandbox.</p>
			<div className="settings-deeplinks">
				<ConsoleLink page="integrations" label={copy.openIntegrations} />
			</div>
		</>
	);
}

function AdvancedBody({
	overview,
	selectedProject,
	mutate,
	language,
	copy,
}: {
	overview: Overview;
	selectedProject: Project | null;
	mutate: Mutate;
	language: Language;
	copy: Copy;
}) {
	const teams = useMemo(
		() => (selectedProject ? overview.teams.filter((team) => team.projectId === selectedProject.id) : []),
		[overview.teams, selectedProject],
	);

	return (
		<>
			<Disclosure title={copy.advParameters}>
				<DataTable
					rows={overview.modelPolicies}
					caption={copy.advParameters}
					empty={<EmptyState title="No model policies" body="Model parameters appear here when routing policies are configured." />}
					columns={[
						{ key: 'name', label: 'Name', render: (row) => row.name },
						{ key: 'tokens', label: 'Max tokens', render: (row) => <span className="mono">{row.maxTokens}</span> },
						{ key: 'cost', label: 'Max cost', render: (row) => <span className="mono">{row.maxCostUsd}</span> },
						{ key: 'temperature', label: 'Temperature', render: (row) => <span className="mono">{row.temperature}</span> },
					]}
				/>
			</Disclosure>

			<Disclosure title={copy.advUserPreferences}>
				<div className="stack">
					<div><strong>Interface language</strong><br /><span className="muted">{language === 'es' ? 'Español' : 'English'}</span></div>
					<div><strong>Display density</strong><br /><span className="muted">Operational compact</span></div>
					<div><strong>Motion preference</strong><br /><span className="muted">Respects system reduced-motion settings</span></div>
				</div>
			</Disclosure>

			<Disclosure title={copy.advDefaults}>
				<div className="stack">
					<span>Backend: FastAPI v1</span>
					<span>Frontend: Vite + React + TypeScript</span>
					<span>Autostart: user-scoped Task Scheduler</span>
					<span>Package manager: corepack pnpm@10.24.0</span>
					<span>Python runner: uv</span>
				</div>
			</Disclosure>

			<Disclosure title={copy.advCatalogs}>
				<div className="stack">
					<TranslationMaintainer mutate={mutate} />
					<DataTable
						rows={overview.projectTemplates}
						caption="Project templates"
						empty={<EmptyState title="No templates" body="Project templates feed the new project wizard." />}
						columns={[
							{ key: 'id', label: 'ID', render: (row) => <span className="mono">{row.id}</span> },
							{ key: 'name', label: 'Name', render: (row) => row.name },
							{ key: 'kind', label: 'Kind', render: (row) => row.kind },
						]}
					/>
					<DataTable
						rows={overview.providers}
						caption="Providers"
						empty={<EmptyState title="No providers" body="Provider catalogs seed agent and runtime choices." />}
						columns={[
							{ key: 'id', label: 'ID', render: (row) => <span className="mono">{row.id}</span> },
							{ key: 'kind', label: 'Kind', render: (row) => row.kind },
							{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
						]}
					/>
					<DataTable
						rows={teams}
						caption="Teams"
						empty={<EmptyState title="No teams for selected project" body="Team catalogs appear here when they are seeded for the selected project." />}
						columns={[
							{ key: 'name', label: 'Name', render: (row) => row.name },
							{ key: 'version', label: 'Version', render: (row) => <span className="mono">{row.version}</span> },
							{ key: 'capabilities', label: 'Capabilities', render: (row) => row.capabilities.length },
						]}
					/>
				</div>
			</Disclosure>
		</>
	);
}
