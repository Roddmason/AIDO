/**
 * Per-section body components for the Settings modal (runtime, team roster cards,
 * workspaces, advanced/developer). Each renders a compact body over real control-plane
 * data and deep-links to the heavy operational consoles (Policy, Evidence, Audit,
 * Model Gateway…) rather than duplicating them. The modal's section registry
 * (sections.tsx) imports these bodies; stateful panels (credentials, plugins,
 * project team) live in their own sibling modules.
 *
 * SecurityBody is intentionally retained as this module's credential-surface anchor: the secret-leak
 * tripwire (tests_py/test_ci_and_openapi_client.py) asserts this file references CredentialManagerPanel
 * and never inlines raw secret fields. The live modal renders credentials via sections.tsx's
 * `credentials` section (<CredentialManagerPanel/>), so SecurityBody is not imported elsewhere.
 * @author Rodrigo Mason
 */

import { ExternalLink } from 'lucide-react';
import { useMemo } from 'react';

import type {
	AgentProfile,
	Overview,
	Project,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../../api/types';
import { DEVELOPER_PAGE_GROUPS, pickLabel } from '../../app/navigation';
import { Disclosure } from '../../components/Disclosure';
import { Badge, DataTable, EmptyState } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { TranslationMaintainer } from '../../i18n/TranslationMaintainer';
import { toneForStatus } from '../../lib/format';
import type { Language } from '../projects/ProjectsPage';
import { RuntimeSetupPanel } from '../runtime-setup/RuntimeSetupPanel';
import { CredentialManagerPanel } from './CredentialManagerPanel';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;

export function ConsoleLink({
	page,
	label,
	onNavigate,
}: {
	page: string;
	label: string;
	/** Invoked on click so the Settings overlay closes before the hash navigation lands. */
	onNavigate?: () => void;
}) {
	return (
		<a className="settings-console-link" href={`#${page}`} onClick={onNavigate}>
			<ExternalLink aria-hidden="true" size={15} />
			{label}
		</a>
	);
}

export function RuntimeBody({
	overview,
	runtimeProviders,
	runtimeProviderConfiguration,
	token,
	onRefresh,
	onNavigate,
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	token: string;
	onRefresh: () => Promise<unknown> | undefined;
	onNavigate?: () => void;
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
				<div>
					<strong>{t('ui.static.local.control.api.f1f86c0e', 'Local Control API')}</strong>
					<span className="mono">/api/v1</span>
				</div>
				<div>
					<strong>{t('settings.runtime.providerCatalog', 'Provider catalog')}</strong>
					<span className="muted">
						{overview.providers.length} {t('app.settings.runtime.recordsWord', 'records')}
					</span>
				</div>
				<div>
					<strong>{t('settings.runtime.packageManager', 'Package manager')}</strong>
					<span className="mono">corepack pnpm@10.24.0</span>
				</div>
				<div>
					<strong>{t('app.settings.runtime.pythonRunnerLabel', 'Python runner')}</strong>
					<span className="mono">uv run</span>
				</div>
				<div>
					<strong>{t('ui.static.default.shell.27dfc2a7', 'Default shell')}</strong>
					<span className="mono">PowerShell</span>
				</div>
			</div>
			<div className="settings-deeplinks">
				<ConsoleLink
					page="models"
					label={t('app.settings.openModelGateway', 'Open Model Gateway')}
					onNavigate={onNavigate}
				/>
				<ConsoleLink
					page="memory"
					label={t('app.settings.openMemory', 'Open Memory & Retrieval')}
					onNavigate={onNavigate}
				/>
			</div>
		</>
	);
}

/**
 * Shared roster card grid for the Default Team (general) and project Team sections:
 * one card per agent profile with runtime availability, providers and quality gates.
 * Cards over tables: the roster is scanned by role, not compared column-by-column.
 */
export function TeamRosterCards({
	profiles,
	showOverride = false,
}: {
	profiles: AgentProfile[];
	/** Marks profiles carrying a project-scope override (project Team section). */
	showOverride?: boolean;
}) {
	const { t } = useI18n();
	if (profiles.length === 0) {
		return (
			<EmptyState
				title={t('ui.static.no.catalog.agents.49a260d2', 'No available team profiles')}
				body={t(
					'settings.agents.catalogHint',
					'Agent profiles live in the Agents console and appear here after the base team is seeded.',
				)}
			/>
		);
	}
	return (
		<div className="settings-team-grid">
			{profiles.map((profile) => {
				const availability = profile.runtimeAvailability?.status ?? profile.status;
				return (
					<article key={profile.id} className="settings-team-card">
						<div className="settings-team-card-head">
							<div className="settings-team-card-id">
								<strong>{profile.name}</strong>
								<span className="muted mono">{profile.role}</span>
							</div>
							<span className="inline">
								{showOverride && profile.projectOverride ? (
									<Badge tone="info">{t('app.agents.projectOverride', 'Project override')}</Badge>
								) : null}
								<Badge tone={toneForStatus(availability)}>{availability}</Badge>
							</span>
						</div>
						<dl className="settings-team-card-meta">
							<div>
								<dt>{t('ui.static.mode.a7b93d21', 'Mode')}</dt>
								<dd className="mono">{profile.runtimeMode}</dd>
							</div>
							<div>
								<dt>{t('app.settings.team.providers', 'Providers')}</dt>
								<dd className="mono">{profile.allowedProviders.join(', ') || '—'}</dd>
							</div>
							<div>
								<dt>{t('app.settings.team.qualityGates', 'Quality gates')}</dt>
								<dd>{profile.qualityGates.length}</dd>
							</div>
						</dl>
						{profile.runtimeAvailability?.blockedReason ? (
							<p className="settings-team-card-blocked">
								{profile.runtimeAvailability.blockedReason}
							</p>
						) : null}
					</article>
				);
			})}
		</div>
	);
}

/** Default Team section: the full available agent-profile roster as cards. */
export function DefaultTeamBody({
	overview,
	onNavigate,
}: {
	overview: Overview;
	onNavigate?: () => void;
}) {
	const { t } = useI18n();
	const agentProfiles = useMemo(
		() => [...overview.agentProfiles].sort((a, b) => a.role.localeCompare(b.role)),
		[overview.agentProfiles],
	);

	return (
		<>
			<p className="settings-section-intro">
				{agentProfiles.length}{' '}
				{t(
					'app.settings.agents.modesNote',
					'agent profiles available. TeamScheduler selects only the roles needed for the current intent and risk.',
				)}
			</p>
			<TeamRosterCards profiles={agentProfiles} />
			<div className="settings-deeplinks">
				<ConsoleLink
					page="agents"
					label={t('app.settings.openAgents', 'Open Agents')}
					onNavigate={onNavigate}
				/>
			</div>
		</>
	);
}

/**
 * Credential/security surface kept as the secret-leak tripwire anchor (see file header).
 * Not imported by the modal section registry; the modal renders credentials directly.
 */
export function SecurityBody({ overview, token }: { overview: Overview; token: string }) {
	const { t } = useI18n();
	const sandboxProfile = overview.sandboxProfiles[0];
	return (
		<>
			<div className="inline">
				<Badge tone="ok">
					{t('app.settings.security.localTokenRequired', 'local token required')}
				</Badge>
				<Badge tone="ok">{t('app.settings.security.strictForms', 'strict forms')}</Badge>
				<Badge tone={sandboxProfile ? 'ok' : 'warn'}>
					{t('app.settings.security.sandboxWord', 'sandbox')}{' '}
					{sandboxProfile
						? String(sandboxProfile.id)
						: t('app.settings.security.sandboxDefault', 'default')}
				</Badge>
			</div>
			<p className="muted">
				{t(
					'app.settings.security.consoleNote',
					'Command classification, path boundaries, human gates and sandbox posture are managed in the Policy & Security console. Write operations use the local security handshake.',
				)}
			</p>
			<div className="settings-deeplinks">
				<ConsoleLink page="policy" label={t('app.settings.openPolicy', 'Open Policy & Security')} />
				<ConsoleLink page="evidence" label={t('app.settings.openEvidence', 'Open Evidence & QA')} />
				<ConsoleLink page="audit" label={t('app.settings.openAudit', 'Open Audit Log')} />
			</div>
			<CredentialManagerPanel token={token} />
		</>
	);
}

export function WorkspacesBody({ overview }: { overview: Overview }) {
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
			<p className="muted">
				{t(
					'ui.static.ide.style.workspace.roots.imported.folders.and.detected.proj.e38f3d15',
					'IDE-style workspace roots, imported folders and detected project lanes live here instead of the runtime Workspaces queue.',
				)}
			</p>
			<div className="settings-readouts">
				<div>
					<strong>{t('ui.static.existing.workspace.270f5e27', 'Existing workspace')}</strong>
					<span className="muted">
						{t(
							'app.settings.workspaces.existingHint',
							'Open a folder; detect package.json, pom.xml, pyproject.toml, requirements.txt, go.mod.',
						)}
					</span>
				</div>
				<div>
					<strong>{t('ui.static.create.from.zero.5cd2f082', 'Create from zero')}</strong>
					<span className="muted">
						{t(
							'app.settings.workspaces.validateHelp',
							'Validate the workspace name before creating a directory and project record.',
						)}
					</span>
				</div>
			</div>
			<Badge tone="ok">
				{t('app.settings.workspaces.duplicateGuard', 'duplicate-name guard active')}
			</Badge>
			<Disclosure title={advLabel}>
				<DataTable
					rows={workspaceRoots}
					caption={advLabel}
					empty={
						<EmptyState
							title={t('ui.static.no.workspace.roots.e38dc3e1', 'No workspace roots')}
							body={t(
								'ui.static.import.an.existing.workspace.or.create.one.from.the.project.aa5fed6b',
								'Import an existing workspace or create one from the project wizard.',
							)}
						/>
					}
					columns={[
						{
							key: 'root',
							label: t('ui.static.workspace.root.12c2483d', 'Workspace root'),
							render: (row) => <span className="mono">{row.path}</span>,
						},
						{
							key: 'project',
							label: t('ui.static.detected.project.0effc850', 'Detected project'),
							render: (row) =>
								projectById.get(String(row.projectId))?.name ?? String(row.projectId ?? ''),
						},
						{
							key: 'mode',
							label: t('ui.static.mode.a7b93d21', 'Mode'),
							render: (row) => (
								<span className="mono">{String(row.isolationType ?? 'directory')}</span>
							),
						},
						{
							key: 'status',
							label: t('ui.static.status.bae7d5be', 'Status'),
							render: (row) => (
								<Badge tone={toneForStatus(String(row.status ?? ''))}>
									{String(row.status ?? '')}
								</Badge>
							),
						},
					]}
				/>
			</Disclosure>
		</>
	);
}

export function AdvancedBody({
	overview,
	selectedProject,
	mutate,
	language,
	onNavigate,
}: {
	overview: Overview;
	selectedProject: Project | null;
	mutate: Mutate;
	language: Language;
	onNavigate?: () => void;
}) {
	const { t } = useI18n();
	const teams = useMemo(
		() =>
			selectedProject ? overview.teams.filter((team) => team.projectId === selectedProject.id) : [],
		[overview.teams, selectedProject],
	);

	return (
		<>
			<section className="settings-group">
				<h4 className="settings-group-title">
					{t('app.settings.advanced.consolesTitle', 'Developer consoles')}
				</h4>
				<p className="settings-section-intro">
					{t(
						'app.settings.advanced.consolesNote',
						'Read-only technical consoles demoted from primary navigation.',
					)}
				</p>
				<div className="settings-dev-groups">
					{DEVELOPER_PAGE_GROUPS.map((group) => (
						<div key={group.label.en} className="settings-dev-group">
							<span className="settings-dev-group-label">{pickLabel(group.label, language)}</span>
							<div className="settings-deeplinks">
								{group.links.map((link) => (
									<ConsoleLink
										key={link.page}
										page={link.page}
										label={pickLabel(link.label, language)}
										onNavigate={onNavigate}
									/>
								))}
							</div>
						</div>
					))}
				</div>
			</section>

			<Disclosure title={t('app.copy.features.settings.SettingsPage.5', 'Parameters')}>
				<DataTable
					rows={overview.modelPolicies}
					caption={t('app.copy.features.settings.SettingsPage.5', 'Parameters')}
					empty={
						<EmptyState
							title={t('ui.static.no.model.policies.993f8301', 'No model policies')}
							body={t(
								'ui.static.model.parameters.appear.here.when.routing.policies.are.confi.71a70605',
								'Model parameters appear here when routing policies are configured.',
							)}
						/>
					}
					columns={[
						{ key: 'name', label: t('ui.static.name.709a2322', 'Name'), render: (row) => row.name },
						{
							key: 'tokens',
							label: t('ui.static.max.tokens.4bd246e3', 'Max tokens'),
							render: (row) => <span className="mono">{row.maxTokens}</span>,
						},
						{
							key: 'cost',
							label: t('ui.static.max.cost.cd7a5d86', 'Max cost'),
							render: (row) => <span className="mono">{row.maxCostUsd}</span>,
						},
						{
							key: 'temperature',
							label: t('ui.static.temperature.0a9062a9', 'Temperature'),
							render: (row) => <span className="mono">{row.temperature}</span>,
						},
					]}
				/>
			</Disclosure>

			<Disclosure title={t('app.copy.features.settings.SettingsPage.6', 'Catalogs')}>
				<div className="stack">
					<TranslationMaintainer mutate={mutate} />
					<DataTable
						rows={overview.projectTemplates}
						caption={t('ui.static.project.templates.0bcf1705', 'Project templates')}
						empty={
							<EmptyState
								title={t('ui.static.no.templates.00cbea20', 'No templates')}
								body={t(
									'ui.static.project.templates.feed.the.new.project.wizard.4394b0ac',
									'Project templates feed the new project wizard.',
								)}
							/>
						}
						columns={[
							{ key: 'id', label: 'ID', render: (row) => <span className="mono">{row.id}</span> },
							{
								key: 'name',
								label: t('ui.static.name.709a2322', 'Name'),
								render: (row) => row.name,
							},
							{
								key: 'kind',
								label: t('ui.static.kind.e00ac23f', 'Kind'),
								render: (row) => row.kind,
							},
						]}
					/>
					<DataTable
						rows={overview.providers}
						caption={t('ui.static.providers.87b7c08b', 'Providers')}
						empty={
							<EmptyState
								title={t('ui.static.no.providers.d239f867', 'No providers')}
								body={t(
									'ui.static.provider.catalogs.seed.agent.and.runtime.choices.87203dc0',
									'Provider catalogs seed agent and runtime choices.',
								)}
							/>
						}
						columns={[
							{ key: 'id', label: 'ID', render: (row) => <span className="mono">{row.id}</span> },
							{
								key: 'kind',
								label: t('ui.static.kind.e00ac23f', 'Kind'),
								render: (row) => row.kind,
							},
							{
								key: 'status',
								label: t('ui.static.status.bae7d5be', 'Status'),
								render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge>,
							},
						]}
					/>
					<DataTable
						rows={teams}
						caption={t('ui.static.teams.cbfd44d9', 'Teams')}
						empty={
							<EmptyState
								title={t(
									'ui.static.no.teams.for.selected.project.eb3c5572',
									'No teams for selected project',
								)}
								body={t(
									'settings.teams.emptyHint',
									'Team catalogs appear here when they are seeded for the selected project.',
								)}
							/>
						}
						columns={[
							{
								key: 'name',
								label: t('ui.static.name.709a2322', 'Name'),
								render: (row) => row.name,
							},
							{
								key: 'version',
								label: t('ui.static.version.2da600bf', 'Version'),
								render: (row) => <span className="mono">{row.version}</span>,
							},
							{
								key: 'capabilities',
								label: t('ui.static.capabilities.ca09c54b', 'Capabilities'),
								render: (row) => row.capabilities.length,
							},
						]}
					/>
				</div>
			</Disclosure>
		</>
	);
}
