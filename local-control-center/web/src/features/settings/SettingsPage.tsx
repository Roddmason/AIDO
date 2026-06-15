/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { FolderPlus, SlidersHorizontal, TableProperties } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import type { Overview, Project } from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { TranslationMaintainer } from '../../i18n/TranslationMaintainer';
import { toneForStatus } from '../../lib/format';
import type { Language } from '../active-projects/ActiveProjectsPage';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;
export type SettingsTab = 'projects' | 'user' | 'cli' | 'api' | 'parameters' | 'maintainers' | 'workspaces' | 'defaults';

const settingsLabels: Record<Language, Record<SettingsTab, string>> = {
	en: {
		projects: 'Project selection',
		user: 'User settings',
		cli: 'CLI settings',
		api: 'API settings',
		parameters: 'Parameters',
		maintainers: 'Mantenedores',
		workspaces: 'Workspaces',
		defaults: 'Default configurations',
	},
	es: {
		projects: 'Seleccion de proyecto',
		user: 'Configuraciones de usuario',
		cli: 'Configuracion de cli',
		api: "Configuracion de api's",
		parameters: 'Configuraciones de parametros',
		maintainers: 'Mantenedores',
		workspaces: 'Workspaces',
		defaults: 'Configuraciones por defecto',
	},
};

const settingsChrome = {
	en: {
		kicker: 'Local runtime configuration',
		title: 'Settings',
		summary: 'Project selection, all-project audit visibility, technical mantenedores and runtime defaults live here instead of the daily operation surface.',
		newProject: 'New project',
	},
	es: {
		kicker: 'Configuracion del runtime local',
		title: 'Configuraciones',
		summary: 'La seleccion de proyecto, auditoria, mantenedores tecnicos y defaults viven aqui, fuera de la superficie diaria.',
		newProject: 'Nuevo proyecto',
	},
} satisfies Record<Language, Record<string, string>>;

export function SettingsPage({
	overview,
	selectedProject,
	onSelectProject,
	onCreateProject,
	mutate,
	section = 'projects',
	language = 'en',
}: {
	overview: Overview;
	selectedProject: Project | null;
	onSelectProject: (projectId: string) => void;
	onCreateProject: () => void;
	mutate: Mutate;
	section?: SettingsTab;
	language?: Language;
}) {
	const [tab, setTab] = useState<SettingsTab>(section);
	const activeProjects = overview.projects.filter((project) => project.status === 'active');
	const labels = settingsLabels[language];
	const chrome = settingsChrome[language];

	useEffect(() => {
		setTab(section);
	}, [section]);

	const tabOrder: SettingsTab[] = ['projects', 'user', 'cli', 'api', 'parameters', 'maintainers', 'workspaces'];

	return (
		<>
			<PageHeader
				kicker={chrome.kicker}
				title={chrome.title}
				summary={chrome.summary}
			/>
			<div className="tabs" role="tablist" aria-label="Settings sections">
				{tabOrder.map((item) => (
					<button key={item} className="button" type="button" role="tab" aria-selected={tab === item} onClick={() => setTab(item)}>
						{item === 'projects' ? <TableProperties aria-hidden="true" size={16} /> : null}
						{item === 'maintainers' ? <SlidersHorizontal aria-hidden="true" size={16} /> : null}
						{labels[item]}
					</button>
				))}
			</div>
			{tab === 'projects' ? (
				<ProjectsSettings
					overview={overview}
					activeProjects={activeProjects}
					selectedProject={selectedProject}
					onSelectProject={onSelectProject}
					onNewProject={onCreateProject}
					newProjectLabel={chrome.newProject}
				/>
			) : null}
			{tab === 'user' ? <UserSettings selectedProject={selectedProject} language={language} /> : null}
			{tab === 'cli' ? <CliSettings /> : null}
			{tab === 'api' ? <ApiSettings overview={overview} /> : null}
			{tab === 'parameters' ? <ParameterSettings overview={overview} /> : null}
			{tab === 'maintainers' ? <MaintainersSettings overview={overview} selectedProject={selectedProject} mutate={mutate} /> : null}
			{tab === 'workspaces' ? <WorkspaceSettings overview={overview} /> : null}
			<DefaultConfigurations label={labels.defaults} defaultOpen={tab === 'defaults'} />
		</>
	);
}

function ProjectsSettings({
	overview,
	activeProjects,
	selectedProject,
	onSelectProject,
	onNewProject,
	newProjectLabel,
}: {
	overview: Overview;
	activeProjects: Project[];
	selectedProject: Project | null;
	onSelectProject: (projectId: string) => void;
	onNewProject: () => void;
	newProjectLabel: string;
}) {
	return (
		<>
			<div className="grid two">
				<Surface title="Operational project">
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
						</div>
						<div className="field-help">Only active projects can be selected for operational mutations.</div>
						<button className="button primary settings-action" type="button" onClick={onNewProject}>
							<FolderPlus aria-hidden="true" size={16} />
							{newProjectLabel}
						</button>
					</div>
				</Surface>
				<Surface title="Selection guard">
					<div className="stack">
						<Badge tone={selectedProject ? 'ok' : 'warn'}>{selectedProject ? selectedProject.name : 'no operational project'}</Badge>
						<span className="muted">Command Center and Governance use this explicit selection. They no longer target the first project returned by the API.</span>
					</div>
				</Surface>
			</div>
			<Surface title="All projects">
				<DataTable
					rows={overview.projects}
					empty={<EmptyState title="No project records" body="The runtime project is created automatically at startup." />}
					columns={[
						{ key: 'name', label: 'Name', render: (row) => row.name },
						{ key: 'path', label: 'Path', render: (row) => <span className="mono">{row.path}</span> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
						{ key: 'template', label: 'Template', render: (row) => <span className="mono">{row.templateId}</span> },
						{ key: 'source', label: 'Source', render: (row) => row.source },
					]}
				/>
			</Surface>
		</>
	);
}

function UserSettings({ selectedProject, language }: { selectedProject: Project | null; language: Language }) {
	return (
		<div className="grid two">
			<Surface title="User preferences">
				<div className="stack">
					<div><strong>Interface language</strong><br /><span className="muted">{language === 'es' ? 'Español' : 'English'}</span></div>
					<div><strong>Display density</strong><br /><span className="muted">Operational compact</span></div>
					<div><strong>Motion preference</strong><br /><span className="muted">Respects system reduced-motion settings</span></div>
				</div>
			</Surface>
			<Surface title="Current operational context">
				<div className="stack">
					<Badge tone={selectedProject ? 'ok' : 'warn'}>{selectedProject ? selectedProject.name : 'no operational project'}</Badge>
					<span className="muted">The selected project remains a runtime guard, not a hidden default.</span>
				</div>
			</Surface>
		</div>
	);
}

function CliSettings() {
	return (
		<div className="grid two">
			<Surface title="CLI configuration">
				<div className="stack">
					<div><strong>PNPM package manager</strong><br /><span className="mono">corepack pnpm@10.24.0</span></div>
					<div><strong>Python runner</strong><br /><span className="mono">uv run</span></div>
					<div><strong>Default shell</strong><br /><span className="mono">PowerShell</span></div>
				</div>
			</Surface>
			<Surface title="Runtime CLI lanes">
				<div className="stack">
					<span>Codex CLI, Claude Code, OpenHands and manual runtimes are managed as technical providers.</span>
					<span className="muted">Secrets and provider tokens stay outside the UI state.</span>
				</div>
			</Surface>
		</div>
	);
}

function ApiSettings({ overview }: { overview: Overview }) {
	return (
		<div className="grid two">
			<Surface title="API configuration">
				<div className="stack">
					<div><strong>Local Control API</strong><br /><span className="mono">/api/v1</span></div>
					<div><strong>Providers</strong><br /><span className="muted">{overview.providers.length} provider catalog records</span></div>
					<div><strong>MCP servers</strong><br /><span className="muted">{overview.mcpServers.length} configured servers</span></div>
				</div>
			</Surface>
			<Surface title="API safety">
				<div className="stack">
					<Badge tone="ok">local token required</Badge>
					<span className="muted">Write operations continue to use the local security handshake.</span>
				</div>
			</Surface>
		</div>
	);
}

function ParameterSettings({ overview }: { overview: Overview }) {
	return (
		<div className="grid two">
			<Surface title="Parameter configuration">
				<DataTable
					rows={overview.modelPolicies}
					empty={<EmptyState title="No model policies" body="Model parameters appear here when routing policies are configured." />}
					columns={[
						{ key: 'name', label: 'Name', render: (row) => row.name },
						{ key: 'tokens', label: 'Max tokens', render: (row) => <span className="mono">{row.maxTokens}</span> },
						{ key: 'cost', label: 'Max cost', render: (row) => <span className="mono">{row.maxCostUsd}</span> },
						{ key: 'temperature', label: 'Temperature', render: (row) => <span className="mono">{row.temperature}</span> },
					]}
				/>
			</Surface>
			<Surface title="Guardrails">
				<div className="stack">
					<span>Parameter changes stay explicit and typed. No raw JSON editor is introduced here.</span>
					<Badge tone="ok">strict forms</Badge>
				</div>
			</Surface>
		</div>
	);
}

function MaintainersSettings({ overview, selectedProject, mutate }: { overview: Overview; selectedProject: Project | null; mutate: Mutate }) {
	const teams = useMemo(
		() => (selectedProject ? overview.teams.filter((team) => team.projectId === selectedProject.id) : []),
		[overview.teams, selectedProject],
	);
	const teamIds = new Set(teams.map((team) => team.id));
	const agents = overview.agents.filter((agent) => teamIds.has(agent.teamId));

	return (
		<>
			<TranslationMaintainer mutate={mutate} />
			<div className="grid two">
				<Surface title="Project templates">
					<DataTable
						rows={overview.projectTemplates}
						empty={<EmptyState title="No templates" body="Project templates feed the new project wizard." />}
						columns={[
							{ key: 'id', label: 'ID', render: (row) => <span className="mono">{row.id}</span> },
							{ key: 'name', label: 'Name', render: (row) => row.name },
							{ key: 'kind', label: 'Kind', render: (row) => row.kind },
						]}
					/>
				</Surface>
				<Surface title="Providers">
					<DataTable
						rows={overview.providers}
						empty={<EmptyState title="No providers" body="Provider catalogs seed agent and runtime choices." />}
						columns={[
							{ key: 'id', label: 'ID', render: (row) => <span className="mono">{row.id}</span> },
							{ key: 'kind', label: 'Kind', render: (row) => row.kind },
							{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
						]}
					/>
				</Surface>
				<Surface title="Teams">
					<DataTable
						rows={teams}
						empty={<EmptyState title="No teams for selected project" body="Team catalogs will appear here when they are seeded for the selected project." />}
						columns={[
							{ key: 'name', label: 'Name', render: (row) => row.name },
							{ key: 'version', label: 'Version', render: (row) => <span className="mono">{row.version}</span> },
							{ key: 'capabilities', label: 'Capabilities', render: (row) => row.capabilities.length },
						]}
					/>
				</Surface>
				<Surface title="Agents">
					<DataTable
						rows={agents}
						empty={<EmptyState title="No catalog agents" body="Agent profiles remain in the Agents page. This panel shows catalog agents owned by selected project teams." />}
						columns={[
							{ key: 'name', label: 'Name', render: (row) => row.name },
							{ key: 'role', label: 'Role', render: (row) => row.role },
							{ key: 'provider', label: 'Provider', render: (row) => <span className="mono">{row.providerId}</span> },
						]}
					/>
				</Surface>
			</div>
		</>
	);
}

function WorkspaceSettings({ overview }: { overview: Overview }) {
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
		<div className="grid two">
			<Surface title="Settings Workspaces">
				<div className="stack">
					<p className="muted">IDE-style workspace roots, imported folders and detected project lanes live here instead of the runtime Workspaces queue.</p>
					<DataTable
						rows={workspaceRoots}
						empty={<EmptyState title="No workspace roots" body="Import an existing workspace or create one from the project wizard." />}
						columns={[
							{ key: 'root', label: 'Workspace root', render: (row) => <span className="mono">{row.path}</span> },
							{ key: 'project', label: 'Detected project', render: (row) => projectById.get(String(row.projectId))?.name ?? String(row.projectId ?? '') },
							{ key: 'mode', label: 'Mode', render: (row) => <span className="mono">{String(row.isolationType ?? 'directory')}</span> },
							{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
						]}
					/>
				</div>
			</Surface>
			<Surface title="Workspace import rules">
				<div className="stack">
					<div><strong>Existing workspace</strong><br /><span className="muted">Open a folder and detect manifests such as package.json, pom.xml, pyproject.toml, requirements.txt and go.mod.</span></div>
					<div><strong>Create from zero</strong><br /><span className="muted">Validate the workspace name before creating a new directory and project record.</span></div>
					<Badge tone="ok">duplicate-name guard active</Badge>
				</div>
			</Surface>
		</div>
	);
}

function DefaultConfigurations({ label, defaultOpen = false }: { label: string; defaultOpen?: boolean }) {
	const [open, setOpen] = useState(defaultOpen);
	useEffect(() => {
		if (defaultOpen) setOpen(true);
	}, [defaultOpen]);
	return (
		<div className="settings-defaults">
			<button className="settings-defaults-trigger" type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
				{label}
			</button>
			<div hidden={!open}>
				<Surface>
				<div className="stack">
					<span>Backend: FastAPI v1</span>
					<span>Frontend: Vite + React + TypeScript</span>
					<span>Autostart: user-scoped Task Scheduler</span>
					<span>Package manager: corepack pnpm@10.24.0</span>
					<span>Python runner: uv</span>
				</div>
				</Surface>
			</div>
		</div>
	);
}
