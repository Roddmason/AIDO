import { FolderPlus, SlidersHorizontal, TableProperties } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { createProject, discoverProject, selectLocalDirectory } from '../../api/client';
import type { JsonValue } from '../../api/generated/openapi';
import type { Overview, Project } from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { TranslationMaintainer } from '../../i18n/TranslationMaintainer';
import { toneForStatus } from '../../lib/format';
import type { Language } from '../active-projects/ActiveProjectsPage';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;
export type SettingsTab = 'projects' | 'user' | 'cli' | 'api' | 'parameters' | 'maintainers' | 'workspaces' | 'defaults';
type WizardStep = 'identity' | 'template' | 'directory' | 'review';
type CreationMode = 'import_existing_workspace' | 'create_from_zero';

const wizardSteps: WizardStep[] = ['identity', 'template', 'directory', 'review'];
const technologyManifestTargets = ['package.json', 'pom.xml', 'pyproject.toml', 'requirements.txt', 'go.mod', 'Cargo.toml'];

type ProjectDiscovery = {
	path?: string;
	exists?: boolean;
	suggestedName?: string;
	templateId?: string;
	manifestSources?: Array<Record<string, unknown>>;
	detectedRuntimes?: Array<Record<string, unknown>>;
};

function prettyNameFromDirectory(value: string) {
	return value
		.trim()
		.replace(/[_-]+/g, ' ')
		.replace(/\s+/g, ' ')
		.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function slugFromName(value: string) {
	return value
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9._-]+/g, '-')
		.replace(/^-+|-+$/g, '')
		.slice(0, 80);
}

function lastPathSegment(value: string) {
	const segments = value.trim().replace(/[\\/]+$/g, '').split(/[\\/]/).filter(Boolean);
	return segments.length ? segments[segments.length - 1] : '';
}

function joinLocalPath(basePath: string, directoryName: string) {
	const base = basePath.trim().replace(/[\\/]+$/g, '');
	const child = directoryName.trim().replace(/^[\\/]+/g, '');
	if (!base) return child;
	const separator = base.includes('\\') && !base.includes('/') ? '\\' : '/';
	return `${base}${separator}${child}`;
}

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
		wizardTitle: 'New Project Wizard',
		importExisting: 'Import existing workspace',
		createFromZero: 'Create from zero',
		next: 'Next',
		cancel: 'Cancel',
		back: 'Back',
		importWorkspace: 'Import workspace',
		createWorkspace: 'Create workspace',
		projectNameRequired: 'Project name is required.',
		workspaceBaseRequired: 'Workspace base path is required.',
		workspaceNameRequired: 'Workspace name is required.',
		workspaceNameExists: 'Workspace name already exists.',
		workspaceFolderRequired: 'Workspace folder is required.',
		workspaceBaseAndNameRequired: 'Workspace base path and workspace name are required.',
		projectDiscoveryFailed: 'Project discovery failed.',
		directoryPickerUnavailable: 'Native directory picker is unavailable. Enter the path manually.',
		directoryPickerFailed: 'Directory picker failed.',
		templateInvalid: 'Project template is invalid.',
		projectCreationFailed: 'Project creation failed.',
	},
	es: {
		kicker: 'Configuracion del runtime local',
		title: 'Configuraciones',
		summary: 'La seleccion de proyecto, auditoria, mantenedores tecnicos y defaults viven aqui, fuera de la superficie diaria.',
		newProject: 'Nuevo proyecto',
		wizardTitle: 'Wizard de nuevo proyecto',
		importExisting: 'Importar workspace existente',
		createFromZero: 'Crear desde cero',
		next: 'Siguiente',
		cancel: 'Cancelar',
		back: 'Volver',
		importWorkspace: 'Importar workspace',
		createWorkspace: 'Crear workspace',
		projectNameRequired: 'El nombre del proyecto es obligatorio.',
		workspaceBaseRequired: 'La carpeta base del workspace es obligatoria.',
		workspaceNameRequired: 'El nombre del workspace es obligatorio.',
		workspaceNameExists: 'Workspace name already exists.',
		workspaceFolderRequired: 'La carpeta del workspace es obligatoria.',
		workspaceBaseAndNameRequired: 'La carpeta base y el nombre del workspace son obligatorios.',
		projectDiscoveryFailed: 'Fallo la deteccion del proyecto.',
		directoryPickerUnavailable: 'El selector nativo de carpetas no esta disponible. Ingresa la ruta manualmente.',
		directoryPickerFailed: 'Fallo el selector de carpetas.',
		templateInvalid: 'El template del proyecto no es valido.',
		projectCreationFailed: 'Fallo la creacion del proyecto.',
	},
} satisfies Record<Language, Record<string, string>>;

export function SettingsPage({
	overview,
	selectedProject,
	onSelectProject,
	onProjectCreated,
	mutate,
	wizardRequest = 0,
	section = 'projects',
	language = 'en',
}: {
	overview: Overview;
	selectedProject: Project | null;
	onSelectProject: (projectId: string) => void;
	onProjectCreated: (projectId: string) => void;
	mutate: Mutate;
	wizardRequest?: number;
	section?: SettingsTab;
	language?: Language;
}) {
	const [tab, setTab] = useState<SettingsTab>(section);
	const [wizardOpen, setWizardOpen] = useState(false);
	const activeProjects = overview.projects.filter((project) => project.status === 'active');
	const labels = settingsLabels[language];
	const chrome = settingsChrome[language];

	useEffect(() => {
		if (wizardRequest > 0) {
			setWizardOpen(true);
			setTab('projects');
		}
	}, [wizardRequest]);

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
					onNewProject={() => setWizardOpen(true)}
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
			{wizardOpen ? (
				<NewProjectWizard
					overview={overview}
					mutate={mutate}
					onClose={() => setWizardOpen(false)}
					onCreated={(projectId) => {
						onProjectCreated(projectId);
						setWizardOpen(false);
					}}
					language={language}
				/>
			) : null}
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

function NewProjectWizard({
	overview,
	mutate,
	onClose,
	onCreated,
	language,
}: {
	overview: Overview;
	mutate: Mutate;
	onClose: () => void;
	onCreated: (projectId: string) => void;
	language: Language;
}) {
	const chrome = settingsChrome[language];
	const [step, setStep] = useState<WizardStep>('identity');
	const [creationMode, setCreationMode] = useState<CreationMode>('import_existing_workspace');
	const [name, setName] = useState('');
	const [nameTouched, setNameTouched] = useState(false);
	const [workspaceFolder, setWorkspaceFolder] = useState('');
	const [workspaceBasePath, setWorkspaceBasePath] = useState('');
	const [workspaceName, setWorkspaceName] = useState('');
	const [templateId, setTemplateId] = useState(overview.projectTemplates[0]?.id ?? 'other');
	const [createDirectory, setCreateDirectory] = useState(false);
	const [discovery, setDiscovery] = useState<ProjectDiscovery | null>(null);
	const [error, setError] = useState('');
	const [busy, setBusy] = useState(false);
	const [discoveryBusy, setDiscoveryBusy] = useState(false);
	const [pickerBusy, setPickerBusy] = useState(false);
	const stepIndex = wizardSteps.indexOf(step);
	const finalPath = creationMode === 'create_from_zero' ? joinLocalPath(workspaceBasePath, workspaceName) : workspaceFolder.trim();
	const detectedRuntimes = discovery?.detectedRuntimes ?? [];
	const manifestSources = discovery?.manifestSources ?? [];
	const workspaceNameConflict = useMemo(() => {
		if (creationMode !== 'create_from_zero') return false;
		const normalized = workspaceName.trim().toLowerCase();
		if (!normalized) return false;
		const projectConflict = overview.projects.some((project) => {
			return project.name.trim().toLowerCase() === normalized || lastPathSegment(project.path).toLowerCase() === normalized;
		});
		const workspaceConflict = overview.runtimeWorkspaces.some((workspace) => {
			return lastPathSegment(workspace.path).toLowerCase() === normalized || String(workspace.taskId ?? '').trim().toLowerCase() === normalized;
		});
		return projectConflict || workspaceConflict;
	}, [creationMode, overview.projects, overview.runtimeWorkspaces, workspaceName]);

	const applyDiscovery = (nextDiscovery: ProjectDiscovery) => {
		setDiscovery(nextDiscovery);
		if (nextDiscovery.suggestedName && !nameTouched) {
			setName(nextDiscovery.suggestedName);
		}
		if (nextDiscovery.templateId && overview.projectTemplates.some((template) => template.id === nextDiscovery.templateId)) {
			setTemplateId(nextDiscovery.templateId);
		}
	};

	const updateWorkspaceName = (value: string) => {
		setWorkspaceName(value);
		if (!nameTouched && value.trim()) {
			setName(prettyNameFromDirectory(value));
		}
	};

	const runDiscovery = async (targetPath = finalPath) => {
		if (!targetPath.trim()) {
			setError(creationMode === 'create_from_zero' ? chrome.workspaceBaseAndNameRequired : chrome.workspaceFolderRequired);
			return;
		}
		setDiscoveryBusy(true);
		setError('');
		try {
			const result = await mutate((token) => discoverProject(token, { path: targetPath.trim() }));
			applyDiscovery(result.discovery as ProjectDiscovery);
		} catch (error) {
			setError(error instanceof Error ? error.message : chrome.projectDiscoveryFailed);
		} finally {
			setDiscoveryBusy(false);
		}
	};

	const browseDirectory = async (target: 'workspaceBasePath' | 'workspaceFolder') => {
		setPickerBusy(true);
		setError('');
		try {
			const initialPath = target === 'workspaceBasePath' ? workspaceBasePath || workspaceFolder || undefined : workspaceFolder || workspaceBasePath || undefined;
			const result = await mutate((token) => selectLocalDirectory(token, { title: 'Select workspace folder', initialPath }));
			if (result.status === 'selected' && result.selectedPath) {
				if (target === 'workspaceBasePath') {
					setWorkspaceBasePath(result.selectedPath);
				} else {
					setWorkspaceFolder(result.selectedPath);
					await runDiscovery(result.selectedPath);
				}
				return;
			}
			if (result.status === 'unavailable') {
				setError(result.reason ?? chrome.directoryPickerUnavailable);
			}
		} catch (error) {
			setError(error instanceof Error ? error.message : chrome.directoryPickerFailed);
		} finally {
			setPickerBusy(false);
		}
	};

	const setMode = (mode: CreationMode) => {
		setCreationMode(mode);
		setDiscovery(null);
		setError('');
		setCreateDirectory(mode === 'create_from_zero');
	};

	const validateCurrent = () => {
		if (step === 'identity') {
			if (!name.trim()) return chrome.projectNameRequired;
			if (creationMode === 'create_from_zero') {
				if (!workspaceBasePath.trim()) return chrome.workspaceBaseRequired;
				if (!workspaceName.trim()) return chrome.workspaceNameRequired;
				if (workspaceNameConflict) return chrome.workspaceNameExists;
			} else if (!workspaceFolder.trim()) {
				return chrome.workspaceFolderRequired;
			}
		}
		if (step === 'template' && !overview.projectTemplates.some((template) => template.id === templateId)) {
			return chrome.templateInvalid;
		}
		return '';
	};
	const goNext = () => {
		const message = validateCurrent();
		if (message) {
			setError(message);
			return;
		}
		setError('');
		setStep(wizardSteps[Math.min(stepIndex + 1, wizardSteps.length - 1)]);
	};
	const goBack = () => {
		setError('');
		setStep(wizardSteps[Math.max(stepIndex - 1, 0)]);
	};
	const submit = async () => {
		const message = validateCurrent();
		if (message) {
			setError(message);
			return;
		}
		setBusy(true);
		setError('');
		try {
			const metadata: Record<string, JsonValue> = {
				source: 'settings_project_wizard',
				creationMode,
				workspaceFlow: creationMode,
				detectedRuntimes: detectedRuntimes as JsonValue[],
				manifestSources: manifestSources as JsonValue[],
			};
			if (workspaceBasePath.trim()) metadata.workspaceBasePath = workspaceBasePath.trim();
			if (workspaceName.trim()) metadata.projectDirectoryName = workspaceName.trim();
			if (workspaceFolder.trim()) metadata.workspaceFolder = workspaceFolder.trim();
			const result = await mutate((token) =>
				createProject(token, {
					name: name.trim(),
					path: creationMode === 'import_existing_workspace' ? workspaceFolder.trim() : undefined,
					workspaceBasePath: creationMode === 'create_from_zero' ? workspaceBasePath.trim() : undefined,
					projectDirectoryName: creationMode === 'create_from_zero' ? workspaceName.trim() : undefined,
					templateId,
					createDirectory,
					metadata,
				}),
			);
			onCreated(result.project.id);
		} catch (error) {
			setError(error instanceof Error ? error.message : chrome.projectCreationFailed);
		} finally {
			setBusy(false);
		}
	};

	return (
		<Surface title={chrome.wizardTitle}>
			<div className="wizard-layout">
				<ol className="wizard-steps" aria-label="New project steps">
					{wizardSteps.map((item, index) => (
						<li key={item} className={item === step ? 'current' : ''}>
							<span className="mono">0{index + 1}</span>
							<strong>{item === 'identity' ? 'workspace' : item}</strong>
						</li>
					))}
				</ol>
				<div className="form-grid">
					{step === 'identity' ? (
						<>
							<div className="field">
								<span className="field-label">Workspace flow</span>
								<div className="wizard-mode-toggle" role="group" aria-label="Workspace setup mode">
									<button className="button" type="button" aria-pressed={creationMode === 'import_existing_workspace'} onClick={() => setMode('import_existing_workspace')}>
										{chrome.importExisting}
									</button>
									<button className="button" type="button" aria-pressed={creationMode === 'create_from_zero'} onClick={() => setMode('create_from_zero')}>
										{chrome.createFromZero}
									</button>
								</div>
								<span className="field-help">Matches IDE flows: open a folder that already contains projects, or create a new workspace folder first.</span>
							</div>
							<div className="field">
								<label htmlFor="project-name">Project name</label>
								<input
									id="project-name"
									className="input"
									value={name}
									maxLength={140}
									autoComplete="off"
									onChange={(event) => {
										setNameTouched(true);
										setName(event.target.value);
										if (!workspaceName && event.target.value.trim()) {
											setWorkspaceName(slugFromName(event.target.value));
										}
									}}
								/>
							</div>
							{creationMode === 'create_from_zero' ? (
								<>
									<div className="field">
										<label htmlFor="workspace-base-path">Workspace base path</label>
										<div className="inline">
											<input id="workspace-base-path" className="input" value={workspaceBasePath} autoComplete="off" onChange={(event) => setWorkspaceBasePath(event.target.value)} />
											<button className="button" type="button" onClick={() => void browseDirectory('workspaceBasePath')} disabled={pickerBusy}>
												Select folder
											</button>
										</div>
									</div>
									<div className="field">
										<label htmlFor="workspace-name">Workspace name</label>
										<input id="workspace-name" className="input" value={workspaceName} autoComplete="off" onChange={(event) => updateWorkspaceName(event.target.value)} />
										<span className="field-help">Final path: <span className="mono">{finalPath || 'workspace/workspace-name'}</span></span>
										{workspaceNameConflict ? <span className="form-error" role="alert">{chrome.workspaceNameExists}</span> : null}
									</div>
								</>
							) : (
								<>
									<div className="field">
										<label htmlFor="workspace-folder">Workspace folder</label>
										<div className="inline">
											<input id="workspace-folder" className="input" value={workspaceFolder} autoComplete="off" onChange={(event) => setWorkspaceFolder(event.target.value)} />
											<button className="button" type="button" onClick={() => void browseDirectory('workspaceFolder')} disabled={pickerBusy}>
												Select folder
											</button>
										</div>
									</div>
									<div className="wizard-tech-strip" aria-label="Technology detection targets">
										{technologyManifestTargets.map((manifest) => (
											<Badge key={manifest}>{manifest}</Badge>
										))}
									</div>
								</>
							)}
							<button className="button" type="button" onClick={() => void runDiscovery()} disabled={discoveryBusy || pickerBusy}>
								Detect technologies
							</button>
							{discovery ? (
								<div className="review-grid">
									<div><span className="muted">Suggested</span><strong>{discovery.suggestedName ?? 'Project'}</strong></div>
									<div><span className="muted">Path exists</span><strong>{discovery.exists ? 'yes' : 'no'}</strong></div>
									<div><span className="muted">Runtimes</span><strong>{detectedRuntimes.length}</strong></div>
									<div><span className="muted">Manifests</span><strong>{manifestSources.length}</strong></div>
								</div>
							) : null}
						</>
					) : null}
					{step === 'template' ? (
						<>
							<div className="field">
								<label htmlFor="project-template">Project template</label>
								<select id="project-template" className="select" value={templateId} onChange={(event) => setTemplateId(event.target.value)}>
									{overview.projectTemplates.map((template) => (
										<option key={template.id} value={template.id}>{template.name}</option>
									))}
								</select>
								<span className="field-help">Existing workspaces can override this when manifests are detected. New workspaces use it as the starter technology profile.</span>
							</div>
							<div className="wizard-tech-strip" aria-label="Available technology templates">
								{overview.projectTemplates.map((template) => (
									<Badge key={template.id} tone={template.id === templateId ? 'ok' : undefined}>{template.kind}</Badge>
								))}
							</div>
						</>
					) : null}
					{step === 'directory' ? (
						<label className="checkbox-row" htmlFor="create-directory">
							<input id="create-directory" type="checkbox" checked={createDirectory} disabled={creationMode === 'import_existing_workspace'} onChange={(event) => setCreateDirectory(event.target.checked)} />
							<span>
								<strong>Create directory</strong>
								<span className="field-help">
									{creationMode === 'create_from_zero'
										? 'Create the workspace directory before the project is registered.'
										: 'Existing workspace imports reuse the selected folder and do not create a new directory.'}
								</span>
							</span>
						</label>
					) : null}
					{step === 'review' ? (
						<div className="review-grid">
							<div><span className="muted">Name</span><strong>{name.trim()}</strong></div>
							<div><span className="muted">Mode</span><strong>{creationMode === 'create_from_zero' ? 'create from zero' : 'import existing workspace'}</strong></div>
							<div><span className="muted">Path</span><strong className="mono">{finalPath}</strong></div>
							<div><span className="muted">Template</span><strong className="mono">{templateId}</strong></div>
							<div><span className="muted">Runtimes</span><strong>{detectedRuntimes.length || 'none detected'}</strong></div>
							<div><span className="muted">Manifests</span><strong>{manifestSources.length || technologyManifestTargets.join(', ')}</strong></div>
						</div>
					) : null}
					{error ? <div className="form-error" role="alert">{error}</div> : null}
					<div className="wizard-actions">
						<button className="button" type="button" onClick={stepIndex === 0 ? onClose : goBack} disabled={busy}>
							{stepIndex === 0 ? chrome.cancel : chrome.back}
						</button>
						{step === 'review' ? (
							<button className="button primary" type="button" onClick={() => void submit()} disabled={busy}>
								{creationMode === 'create_from_zero' ? chrome.createWorkspace : chrome.importWorkspace}
							</button>
						) : (
							<button className="button primary" type="button" onClick={goNext} disabled={busy || discoveryBusy || pickerBusy || workspaceNameConflict}>
								{chrome.next}
							</button>
						)}
					</div>
				</div>
			</div>
		</Surface>
	);
}
