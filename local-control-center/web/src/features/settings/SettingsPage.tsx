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
import { useI18n } from '../../i18n/I18nProvider';
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
	const { t } = useI18n();
	const activeProjects = overview.projects.filter((project) => project.status === 'active');

	// Deep link: bring the requested group card into view (instant under reduced-motion via global CSS).
	useEffect(() => {
		const element = document.getElementById(`settings-group-${section}`);
		element?.scrollIntoView({ block: 'start' });
	}, [section]);

	return (
		<>
			<PageHeader
				kicker={t('app.copy.features.settings.SettingsPage.9', 'Local runtime configuration')}
				title={t('app.copy.features.settings.SettingsPage.10', 'Settings')}
				summary={t('app.settings.summary', 'Grouped configuration: pick a project, get a runtime ready, and keep catalogs, defaults and audit out of the daily operation surface.')}
			/>
			<div className="settings-hub">
				<SettingsGroup id="project" status={projectStatus(selectedProject, t)}>
					<ProjectBody
						overview={overview}
						activeProjects={activeProjects}
						selectedProject={selectedProject}
						onSelectProject={onSelectProject}
						onNewProject={onCreateProject}
					/>
				</SettingsGroup>

				<SettingsGroup id="runtime" status={runtimeStatus(runtimeProviders, t)}>
					<RuntimeBody
						overview={overview}
						runtimeProviders={runtimeProviders}
						runtimeProviderConfiguration={runtimeProviderConfiguration}
						token={token}
						onRefresh={onRefresh}
					/>
				</SettingsGroup>

				<SettingsGroup id="agents" status={agentsStatus(overview, t)}>
					<AgentsBody overview={overview} selectedProject={selectedProject} />
				</SettingsGroup>

				<SettingsGroup id="security" status={{ tone: 'ok', label: t('app.settings.security.statusLabel', 'Local token') }}>
					<SecurityBody overview={overview} />
				</SettingsGroup>

				<SettingsGroup id="workspaces" status={workspacesStatus(overview, t)}>
					<WorkspacesBody overview={overview} />
				</SettingsGroup>

				<SettingsGroup id="integrations" status={{ tone: 'info', label: `${overview.mcpServers.length} MCP` }}>
					<IntegrationsBody overview={overview} />
				</SettingsGroup>

				<SettingsGroup id="advanced">
					<AdvancedBody overview={overview} selectedProject={selectedProject} mutate={mutate} language={language} />
				</SettingsGroup>
			</div>
		</>
	);
}

function SettingsGroup({
	id,
	status,
	children,
}: {
	id: SettingsGroupId;
	status?: { tone: Tone; label: string };
	children: ReactNode;
}) {
	const { t } = useI18n();
	const Icon = GROUP_ICON[id];
	const group = GROUP_COPY[id];
	return (
		<section id={`settings-group-${id}`} className="settings-group" aria-labelledby={`settings-group-${id}-title`}>
			<header className="settings-group-header">
				<Icon aria-hidden="true" size={20} />
				<div className="settings-group-heading">
					<h2 id={`settings-group-${id}-title`} className="surface-title">{t(group.titleKey, group.title)}</h2>
					<p className="settings-group-purpose">{t(group.purposeKey, group.purpose)}</p>
				</div>
				{status ? <Badge tone={status.tone}>{status.label}</Badge> : null}
			</header>
			<div className="settings-group-body">{children}</div>
		</section>
	);
}

const GROUP_COPY: Record<SettingsGroupId, { titleKey: string; title: string; purposeKey: string; purpose: string }> = {
	project: {
		titleKey: 'ui.static.project.f6f4da8d',
		title: 'Project',
		purposeKey: 'app.settings.group.project.purpose',
		purpose: 'Pick the active project that work runs against.',
	},
	runtime: {
		titleKey: 'settings.runtime.section.title',
		title: 'Runtime & Models',
		purposeKey: 'app.settings.group.runtime.purpose',
		purpose: 'Get a runtime provider ready and route models so an agent can actually run.',
	},
	agents: {
		titleKey: 'app.nav.agents',
		title: 'Agents',
		purposeKey: 'app.settings.group.agents.purpose',
		purpose: 'Agent profiles and runtime readiness.',
	},
	security: {
		titleKey: 'app.workbench.team.role.security',
		title: 'Security',
		purposeKey: 'app.settings.group.security.purpose',
		purpose: 'Policy, sandbox posture and the audit trail.',
	},
	workspaces: {
		titleKey: 'app.nav.workspaces',
		title: 'Workspaces',
		purposeKey: 'app.settings.group.workspaces.purpose',
		purpose: 'IDE-style workspace roots and import rules.',
	},
	integrations: {
		titleKey: 'app.nav.integrations',
		title: 'Integrations',
		purposeKey: 'app.settings.group.integrations.purpose',
		purpose: 'MCP servers and external tool connections.',
	},
	advanced: {
		titleKey: 'settings.runtime.advanced',
		title: 'Advanced',
		purposeKey: 'app.settings.group.advanced.purpose',
		purpose: 'Parameters, defaults, catalogs and personal preferences.',
	},
};

function ConsoleLink({ page, label }: { page: string; label: string }) {
	return (
		<a className="settings-console-link" href={`#${page}`}>
			<ExternalLink aria-hidden="true" size={15} />
			{label}
		</a>
	);
}

function projectStatus(selectedProject: Project | null, t: (key: string, fallback: string) => string): { tone: Tone; label: string } {
	if (selectedProject) return { tone: 'ok', label: t('app.home.copy.15', 'Selected') };
	return { tone: 'warn', label: t('app.settings.project.noneSelected', 'None selected') };
}

function runtimeStatus(runtimeProviders: RuntimeProviders | null, t: (key: string, fallback: string) => string): { tone: Tone; label: string } {
	const executable = runtimeProviders?.providers?.filter((provider) => provider.executable).length ?? 0;
	const word = t('app.runtime.executable', 'executable');
	return { tone: executable > 0 ? 'ok' : 'warn', label: `${executable} ${word}` };
}

function agentsStatus(overview: Overview, t: (key: string, fallback: string) => string): { tone: Tone; label: string } {
	const count = overview.agentProfiles.length;
	const word = t('app.settings.agents.profilesWord', 'profiles');
	return { tone: count > 0 ? 'info' : 'warn', label: `${count} ${word}` };
}

function workspacesStatus(overview: Overview, t: (key: string, fallback: string) => string): { tone: Tone; label: string } {
	const count = overview.runtimeWorkspaces.length || overview.projects.length;
	const word = t('app.settings.workspaces.rootsWord', 'roots');
	return { tone: 'ok', label: `${count} ${word}` };
}

function ProjectBody({
	overview,
	activeProjects,
	selectedProject,
	onSelectProject,
	onNewProject,
}: {
	overview: Overview;
	activeProjects: Project[];
	selectedProject: Project | null;
	onSelectProject: (projectId: string) => void;
	onNewProject: () => void;
}) {
	const { t } = useI18n();
	const advLabel = t('ui.static.all.projects.403b2169', 'All projects');
	return (
		<>
			<div className="form-grid">
				<div className="field">
					<label htmlFor="operational-project">{t('ui.static.operational.project.8c3b31f6', 'Operational project')}</label>
					<select
						id="operational-project"
						className="select"
						value={selectedProject?.id ?? ''}
						disabled={!activeProjects.length}
						onChange={(event) => onSelectProject(event.target.value)}
					>
						{activeProjects.length ? null : <option value="">{t('ui.static.no.active.projects.e6823ecd', 'No active projects')}</option>}
						{activeProjects.map((project) => (
							<option key={project.id} value={project.id}>{project.name}</option>
						))}
					</select>
					<div className="field-help">{t('ui.static.only.active.projects.can.be.selected.for.operational.mutatio.3f45da88', 'Only active projects can run work and receive changes.')}</div>
				</div>
				<div className="inline">
					<Badge tone={selectedProject ? 'ok' : 'warn'}>{selectedProject ? selectedProject.name : t('app.settings.project.noOperationalBadge', 'no operational project')}</Badge>
					<span className="field-help">{t('settings.project.selectionHint', 'Command Center and Governance use this explicit selection, not the first project the API returns.')}</span>
				</div>
				<button className="button primary settings-action" type="button" onClick={onNewProject}>
					<FolderPlus aria-hidden="true" size={16} />
					{t('app.copy.features.settings.SettingsPage.12', 'New project')}
				</button>
			</div>
			<Disclosure title={advLabel}>
				<DataTable
					rows={overview.projects}
					caption={advLabel}
					empty={<EmptyState title={t('ui.static.no.project.records.4f3947bb', 'No project records')} body={t('ui.static.the.runtime.project.is.created.automatically.at.startup.17ee3a10', 'The runtime project is created automatically at startup.')} />}
					columns={[
						{ key: 'name', label: t('ui.static.name.709a2322', 'Name'), render: (row) => row.name },
						{ key: 'path', label: t('app.workspace.summary.path', 'Path'), render: (row) => <span className="mono">{row.path}</span> },
						{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
						{ key: 'template', label: t('ui.static.template.3ec1ae06', 'Template'), render: (row) => <span className="mono">{row.templateId}</span> },
						{ key: 'source', label: t('ui.static.source.6da13add', 'Source'), render: (row) => row.source },
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
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	token: string;
	onRefresh: () => Promise<unknown> | void;
}) {
	const { t } = useI18n();
	return (
		<>
			<RuntimeSetupPanel
				runtimeProviders={runtimeProviders}
				runtimeProviderConfiguration={runtimeProviderConfiguration}
				token={token}
				onRefresh={onRefresh}
			/>
			<div className="settings-readouts">
				<div><strong>{t('ui.static.local.control.api.f1f86c0e', 'Local Control API')}</strong><span className="mono">/api/v1</span></div>
				<div><strong>{t('settings.runtime.providerCatalog', 'Provider catalog')}</strong><span className="muted">{overview.providers.length} {t('app.settings.runtime.recordsWord', 'records')}</span></div>
				<div><strong>{t('settings.runtime.packageManager', 'Package manager')}</strong><span className="mono">corepack pnpm@10.24.0</span></div>
				<div><strong>{t('app.settings.runtime.pythonRunnerLabel', 'Python runner')}</strong><span className="mono">uv run</span></div>
				<div><strong>{t('ui.static.default.shell.27dfc2a7', 'Default shell')}</strong><span className="mono">PowerShell</span></div>
			</div>
			<div className="settings-deeplinks">
				<ConsoleLink page="models" label={t('app.settings.openModelGateway', 'Open Model Gateway')} />
				<ConsoleLink page="memory" label={t('app.settings.openMemory', 'Open Memory & Retrieval')} />
			</div>
		</>
	);
}

function AgentsBody({ overview, selectedProject }: { overview: Overview; selectedProject: Project | null }) {
	const { t } = useI18n();
	const catalogAgents = useMemo(() => {
		if (!selectedProject) return [];
		const teamIds = new Set(overview.teams.filter((team) => team.projectId === selectedProject.id).map((team) => team.id));
		return overview.agents.filter((agent) => teamIds.has(agent.teamId));
	}, [overview.agents, overview.teams, selectedProject]);

	const advLabel = t('app.settings.advCatalogAgents', 'Catalog agents');
	return (
		<>
			<p className="muted">{overview.agentProfiles.length} {t('app.settings.agents.modesNote', 'agent profiles. Runtime modes (CLI / API / Ollama / hybrid) sit behind the same governance layer.')}</p>
			<div className="settings-deeplinks">
				<ConsoleLink page="agents" label={t('app.settings.openAgents', 'Open Agents')} />
			</div>
			<Disclosure title={advLabel}>
				<DataTable
					rows={catalogAgents}
					caption={advLabel}
					empty={<EmptyState title={t('ui.static.no.catalog.agents.49a260d2', 'No catalog agents')} body={t('settings.agents.catalogHint', "Agent profiles live in the Agents console. This shows catalog agents owned by the selected project's teams.")} />}
					columns={[
						{ key: 'name', label: t('ui.static.name.709a2322', 'Name'), render: (row) => row.name },
						{ key: 'role', label: t('ui.static.role.c3f104d1', 'Role'), render: (row) => row.role },
						{ key: 'provider', label: t('ui.static.provider.7ceee3f3', 'Provider'), render: (row) => <span className="mono">{row.providerId}</span> },
					]}
				/>
			</Disclosure>
		</>
	);
}

function SecurityBody({ overview }: { overview: Overview }) {
	const { t } = useI18n();
	const sandboxProfile = overview.sandboxProfiles[0];
	return (
		<>
			<div className="inline">
				<Badge tone="ok">{t('app.settings.security.localTokenRequired', 'local token required')}</Badge>
				<Badge tone="ok">{t('app.settings.security.strictForms', 'strict forms')}</Badge>
				<Badge tone={sandboxProfile ? 'ok' : 'warn'}>{t('app.settings.security.sandboxWord', 'sandbox')} {sandboxProfile ? String(sandboxProfile.id) : t('app.settings.security.sandboxDefault', 'default')}</Badge>
			</div>
			<p className="muted">{t('app.settings.security.consoleNote', 'Command classification, path boundaries, human gates and sandbox posture are managed in the Policy & Security console. Write operations use the local security handshake.')}</p>
			<div className="settings-deeplinks">
				<ConsoleLink page="policy" label={t('app.settings.openPolicy', 'Open Policy & Security')} />
				<ConsoleLink page="evidence" label={t('app.settings.openEvidence', 'Open Evidence & QA')} />
				<ConsoleLink page="audit" label={t('app.settings.openAudit', 'Open Audit Log')} />
			</div>
		</>
	);
}

function WorkspacesBody({ overview }: { overview: Overview }) {
	const { t } = useI18n();
	const advLabel = t('app.settings.advWorkspaceRoots', 'Workspace roots');
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
			<p className="muted">{t('ui.static.ide.style.workspace.roots.imported.folders.and.detected.proj.e38f3d15', 'IDE-style workspace roots, imported folders and detected project lanes live here instead of the runtime Workspaces queue.')}</p>
			<div className="settings-readouts">
				<div><strong>{t('ui.static.existing.workspace.270f5e27', 'Existing workspace')}</strong><span className="muted">{t('app.settings.workspaces.existingHint', 'Open a folder; detect package.json, pom.xml, pyproject.toml, requirements.txt, go.mod.')}</span></div>
				<div><strong>{t('ui.static.create.from.zero.5cd2f082', 'Create from zero')}</strong><span className="muted">{t('app.settings.workspaces.validateHelp', 'Validate the workspace name before creating a directory and project record.')}</span></div>
			</div>
			<Badge tone="ok">{t('app.settings.workspaces.duplicateGuard', 'duplicate-name guard active')}</Badge>
			<Disclosure title={advLabel}>
				<DataTable
					rows={workspaceRoots}
					caption={advLabel}
					empty={<EmptyState title={t('ui.static.no.workspace.roots.e38dc3e1', 'No workspace roots')} body={t('ui.static.import.an.existing.workspace.or.create.one.from.the.project.aa5fed6b', 'Import an existing workspace or create one from the project wizard.')} />}
					columns={[
						{ key: 'root', label: t('ui.static.workspace.root.12c2483d', 'Workspace root'), render: (row) => <span className="mono">{row.path}</span> },
						{ key: 'project', label: t('ui.static.detected.project.0effc850', 'Detected project'), render: (row) => projectById.get(String(row.projectId))?.name ?? String(row.projectId ?? '') },
						{ key: 'mode', label: t('ui.static.mode.a7b93d21', 'Mode'), render: (row) => <span className="mono">{String(row.isolationType ?? 'directory')}</span> },
						{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					]}
				/>
			</Disclosure>
		</>
	);
}

function IntegrationsBody({ overview }: { overview: Overview }) {
	const { t } = useI18n();
	return (
		<>
			<p className="muted">{overview.mcpServers.length} {t('app.settings.integrations.registeredNote', 'MCP servers registered. Registration, transports and IDE connections live in the Integrations console; execution still goes through broker, policy and sandbox.')}</p>
			<div className="settings-deeplinks">
				<ConsoleLink page="integrations" label={t('app.settings.openIntegrations', 'Open Integrations')} />
			</div>
		</>
	);
}

function AdvancedBody({
	overview,
	selectedProject,
	mutate,
	language,
}: {
	overview: Overview;
	selectedProject: Project | null;
	mutate: Mutate;
	language: Language;
}) {
	const { t } = useI18n();
	const teams = useMemo(
		() => (selectedProject ? overview.teams.filter((team) => team.projectId === selectedProject.id) : []),
		[overview.teams, selectedProject],
	);

	return (
		<>
			<Disclosure title={t('app.copy.features.settings.SettingsPage.5', 'Parameters')}>
				<DataTable
					rows={overview.modelPolicies}
					caption={t('app.copy.features.settings.SettingsPage.5', 'Parameters')}
					empty={<EmptyState title={t('ui.static.no.model.policies.993f8301', 'No model policies')} body={t('ui.static.model.parameters.appear.here.when.routing.policies.are.confi.71a70605', 'Model parameters appear here when routing policies are configured.')} />}
					columns={[
						{ key: 'name', label: t('ui.static.name.709a2322', 'Name'), render: (row) => row.name },
						{ key: 'tokens', label: t('ui.static.max.tokens.4bd246e3', 'Max tokens'), render: (row) => <span className="mono">{row.maxTokens}</span> },
						{ key: 'cost', label: t('ui.static.max.cost.cd7a5d86', 'Max cost'), render: (row) => <span className="mono">{row.maxCostUsd}</span> },
						{ key: 'temperature', label: t('ui.static.temperature.0a9062a9', 'Temperature'), render: (row) => <span className="mono">{row.temperature}</span> },
					]}
				/>
			</Disclosure>

			<Disclosure title={t('ui.static.user.preferences.c3c46a58', 'User preferences')}>
				<div className="stack">
					<div><strong>{t('ui.static.interface.language.9407e9ca', 'Interface language')}</strong><br /><span className="muted">{language === 'es' ? t('app.settings.prefs.languageSpanish', 'Spanish') : t('app.settings.prefs.languageEnglish', 'English')}</span></div>
					<div><strong>{t('ui.static.display.density.a1b8d01b', 'Display density')}</strong><br /><span className="muted">{t('ui.static.operational.compact.180d847b', 'Operational compact')}</span></div>
					<div><strong>{t('ui.static.motion.preference.f216d061', 'Motion preference')}</strong><br /><span className="muted">{t('ui.static.respects.system.reduced.motion.settings.53b5b03a', 'Respects system reduced-motion settings')}</span></div>
				</div>
			</Disclosure>

			<Disclosure title={t('app.copy.features.settings.SettingsPage.8', 'Default configurations')}>
				<div className="stack">
					<span>{t('ui.static.backend.fastapi.v1.aac8dc1e', 'Backend: FastAPI v1')}</span>
					<span>{t('ui.static.frontend.vite.react.typescript.b3d4c705', 'Frontend: Vite + React + TypeScript')}</span>
					<span>{t('ui.static.autostart.user.scoped.task.scheduler.9d01bf00', 'Autostart: user-scoped Task Scheduler')}</span>
					<span>{t('ui.static.package.manager.corepack.pnpm.10.24.0.2e9f3ce1', 'Package manager: corepack pnpm@10.24.0')}</span>
					<span>{t('ui.static.python.runner.uv.8c1511e8', 'Python runner: uv')}</span>
				</div>
			</Disclosure>

			<Disclosure title={t('app.copy.features.settings.SettingsPage.6', 'Catalogs')}>
				<div className="stack">
					<TranslationMaintainer mutate={mutate} />
					<DataTable
						rows={overview.projectTemplates}
						caption={t('ui.static.project.templates.0bcf1705', 'Project templates')}
						empty={<EmptyState title={t('ui.static.no.templates.00cbea20', 'No templates')} body={t('ui.static.project.templates.feed.the.new.project.wizard.4394b0ac', 'Project templates feed the new project wizard.')} />}
						columns={[
							{ key: 'id', label: 'ID', render: (row) => <span className="mono">{row.id}</span> },
							{ key: 'name', label: t('ui.static.name.709a2322', 'Name'), render: (row) => row.name },
							{ key: 'kind', label: t('ui.static.kind.e00ac23f', 'Kind'), render: (row) => row.kind },
						]}
					/>
					<DataTable
						rows={overview.providers}
						caption={t('ui.static.providers.87b7c08b', 'Providers')}
						empty={<EmptyState title={t('ui.static.no.providers.d239f867', 'No providers')} body={t('ui.static.provider.catalogs.seed.agent.and.runtime.choices.87203dc0', 'Provider catalogs seed agent and runtime choices.')} />}
						columns={[
							{ key: 'id', label: 'ID', render: (row) => <span className="mono">{row.id}</span> },
							{ key: 'kind', label: t('ui.static.kind.e00ac23f', 'Kind'), render: (row) => row.kind },
							{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
						]}
					/>
					<DataTable
						rows={teams}
						caption={t('ui.static.teams.cbfd44d9', 'Teams')}
						empty={<EmptyState title={t('ui.static.no.teams.for.selected.project.eb3c5572', 'No teams for selected project')} body={t('settings.teams.emptyHint', 'Team catalogs appear here when they are seeded for the selected project.')} />}
						columns={[
							{ key: 'name', label: t('ui.static.name.709a2322', 'Name'), render: (row) => row.name },
							{ key: 'version', label: t('ui.static.version.2da600bf', 'Version'), render: (row) => <span className="mono">{row.version}</span> },
							{ key: 'capabilities', label: t('ui.static.capabilities.ca09c54b', 'Capabilities'), render: (row) => row.capabilities.length },
						]}
					/>
				</div>
			</Disclosure>
		</>
	);
}
