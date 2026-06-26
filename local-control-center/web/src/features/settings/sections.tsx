/**
 * Section registry for the Settings modal: defines all navigation sections for
 * the General and Project scopes, their kind (wired/display/placeholder), and the
 * render context type shared across all section render functions.
 *
 * I-4/B-3: each section's `render(ctx)` is fully implemented here, so SettingsModal
 * only calls `activeSection.render(ctx)` — no switch needed. Adding a new section
 * requires only this registry.
 */

import type { ReactNode } from 'react';

import type { JsonValue } from '../../api/generated/openapi';
import type {
	Overview,
	Project,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../../api/types';
import { CredentialManagerPanel } from './CredentialManagerPanel';
import { SectionPlaceholder } from './SectionPlaceholder';
import { SettingRow } from './SettingRow';
import {
	AdvancedBody,
	AgentsBody,
	ConsoleLink,
	IntegrationsBody,
	ProjectBody,
	RuntimeBody,
	WorkspacesBody,
} from './SettingsPage';
import type { ResolvedSetting, SettingScope } from './useSettings';

/** Context available to every section render function. */
export type SectionContext = {
	/** Resolved settings for the current scope (general or project). */
	resolved: ResolvedSetting[];
	/** Full overview data for display sections. */
	overview: Overview;
	/** Currently selected project (may be null). */
	selectedProject: Project | null;
	/** Runtime providers data for the Runtime section. */
	runtimeProviders: RuntimeProviders | null;
	/** Runtime provider configuration for the Runtime section. */
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	/** Local control plane write token for display sections needing mutations. */
	token: string;
	/** Set an override value for a resolved setting. */
	setValue: (
		key: string,
		scope: SettingScope,
		scopeId: string | null,
		value: JsonValue,
	) => Promise<void>;
	/** Clear an override value to revert to inherited. */
	clearValue: (key: string, scope: SettingScope, scopeId: string | null) => Promise<void>;
	/** i18n translator function. */
	t: (key: string, fallback: string) => string;
	/** Callback for the overview refresh (used by runtime setup). */
	onRefresh: () => Promise<unknown> | undefined;
	/** Callback to open the new workspace dialog. */
	onCreateProject: () => void;
	/** Callback to select an operational project. */
	onSelectProject: (projectId: string) => void;
	/** Callback to open a mutate operation (for AdvancedBody catalogs). */
	mutate: <T>(operation: (token: string) => Promise<T>) => Promise<T>;
	/** Current interface language for AdvancedBody. */
	language: 'en' | 'es';
	/** Returns enum options for a given setting key, or undefined if not applicable. */
	enumOptionsFor: (key: string) => Array<{ value: string; label: string }> | undefined;
	/** Scope string for the current section ('general' | 'project'). */
	scope: 'general' | 'project';
	/** Scope id (project id for project sections, null for general). */
	scopeId: string | null;
};

/** A single section definition in the settings navigator. */
export type SectionDefinition = {
	id: string;
	/** i18n key for the section title. */
	titleKey: string;
	/** English fallback for the section title. */
	titleFallback: string;
	/** Whether this section has wired settings, reuses a display body, or is a placeholder. */
	kind: 'wired' | 'display' | 'placeholder';
	/** Renders the section's content given the shared context. */
	render: (ctx: SectionContext) => ReactNode;
};

// ---------------------------------------------------------------------------
// Shared render helpers
// ---------------------------------------------------------------------------

function renderWired(ctx: SectionContext): ReactNode {
	if (ctx.resolved.length === 0) {
		return (
			<p className="muted">
				{ctx.t('app.settings.section.noSettings', 'No settings found for this section.')}
			</p>
		);
	}
	return (
		<div className="stack">
			{ctx.resolved.map((setting) => (
				<SettingRow
					key={setting.key}
					setting={setting}
					enumOptions={ctx.enumOptionsFor(setting.key)}
					onSet={(value) => ctx.setValue(setting.key, ctx.scope, ctx.scopeId, value)}
					onRevert={() => ctx.clearValue(setting.key, ctx.scope, ctx.scopeId)}
				/>
			))}
		</div>
	);
}

function renderAgentsWithLink(ctx: SectionContext): ReactNode {
	return (
		<>
			<AgentsBody overview={ctx.overview} selectedProject={ctx.selectedProject} />
			<ConsoleLink page="agents" label={ctx.t('app.settings.openAgents', 'Open Agents')} />
		</>
	);
}

/**
 * General scope sections in display order.
 * General, Appearance, Providers & CLI, Credentials, Default Team,
 * Autonomy (wired), Security (wired), Research, Costs (wired), Integrations, Advanced.
 */
export const GENERAL_SECTIONS: SectionDefinition[] = [
	{
		id: 'general',
		titleKey: 'app.settings.section.general',
		titleFallback: 'General',
		kind: 'placeholder',
		render: () => (
			<SectionPlaceholder titleKey="app.settings.section.general" titleFallback="General" />
		),
	},
	{
		id: 'appearance',
		titleKey: 'app.settings.section.appearance',
		titleFallback: 'Appearance',
		kind: 'display',
		// B-2: Appearance reuses theme/density/language controls (AdvancedBody).
		render: (ctx) => (
			<AdvancedBody
				overview={ctx.overview}
				selectedProject={ctx.selectedProject}
				mutate={ctx.mutate}
				language={ctx.language}
			/>
		),
	},
	{
		id: 'providers-cli',
		titleKey: 'app.settings.section.providersCli',
		titleFallback: 'Providers & CLI',
		kind: 'display',
		render: (ctx) => (
			<RuntimeBody
				overview={ctx.overview}
				runtimeProviders={ctx.runtimeProviders}
				runtimeProviderConfiguration={ctx.runtimeProviderConfiguration}
				token={ctx.token}
				onRefresh={ctx.onRefresh}
			/>
		),
	},
	{
		id: 'credentials',
		titleKey: 'app.settings.section.credentials',
		titleFallback: 'Credentials',
		kind: 'display',
		render: (ctx) => <CredentialManagerPanel token={ctx.token} />,
	},
	{
		id: 'default-team',
		titleKey: 'app.settings.section.defaultTeam',
		titleFallback: 'Default Team',
		kind: 'placeholder',
		render: () => (
			<SectionPlaceholder
				titleKey="app.settings.section.defaultTeam"
				titleFallback="Default Team"
			/>
		),
	},
	{
		id: 'autonomy',
		titleKey: 'app.settings.section.autonomy',
		titleFallback: 'Autonomy',
		kind: 'wired',
		render: renderWired,
	},
	{
		id: 'security',
		titleKey: 'app.settings.section.security',
		titleFallback: 'Security',
		kind: 'wired',
		render: renderWired,
	},
	{
		id: 'research',
		titleKey: 'app.settings.section.research',
		titleFallback: 'Research',
		kind: 'placeholder',
		render: () => (
			<SectionPlaceholder titleKey="app.settings.section.research" titleFallback="Research" />
		),
	},
	{
		id: 'costs',
		titleKey: 'app.settings.section.costs',
		titleFallback: 'Costs',
		kind: 'wired',
		render: renderWired,
	},
	{
		id: 'integrations',
		titleKey: 'app.settings.section.integrations',
		titleFallback: 'Integrations',
		kind: 'display',
		render: (ctx) => <IntegrationsBody overview={ctx.overview} />,
	},
	{
		id: 'advanced',
		titleKey: 'app.settings.section.advanced',
		titleFallback: 'Advanced',
		kind: 'display',
		render: (ctx) => (
			<AdvancedBody
				overview={ctx.overview}
				selectedProject={ctx.selectedProject}
				mutate={ctx.mutate}
				language={ctx.language}
			/>
		),
	},
];

/**
 * Project scope sections in display order.
 * Project, Goal, Team, Routing, Quality, Security (wired override), Workspaces,
 * Internet, Budget (wired override), Credentials, Integrations, Advanced.
 */
export const PROJECT_SECTIONS: SectionDefinition[] = [
	{
		id: 'project',
		titleKey: 'app.settings.section.project',
		titleFallback: 'Project',
		kind: 'display',
		render: (ctx) => (
			<ProjectBody
				overview={ctx.overview}
				activeProjects={ctx.overview.projects.filter((p) => p.status === 'active')}
				selectedProject={ctx.selectedProject}
				onSelectProject={ctx.onSelectProject}
				onNewProject={ctx.onCreateProject}
			/>
		),
	},
	{
		id: 'goal',
		titleKey: 'app.settings.section.goal',
		titleFallback: 'Goal',
		kind: 'placeholder',
		render: () => <SectionPlaceholder titleKey="app.settings.section.goal" titleFallback="Goal" />,
	},
	{
		id: 'team',
		titleKey: 'app.settings.section.team',
		titleFallback: 'Team',
		kind: 'display',
		render: renderAgentsWithLink,
	},
	{
		id: 'routing',
		titleKey: 'app.settings.section.routing',
		titleFallback: 'Routing',
		kind: 'display',
		render: renderAgentsWithLink,
	},
	{
		id: 'quality',
		titleKey: 'app.settings.section.quality',
		titleFallback: 'Quality',
		kind: 'display',
		render: renderAgentsWithLink,
	},
	{
		id: 'project-security',
		titleKey: 'app.settings.section.security',
		titleFallback: 'Security',
		kind: 'wired',
		render: renderWired,
	},
	{
		id: 'workspaces',
		titleKey: 'app.settings.section.workspaces',
		titleFallback: 'Workspaces',
		kind: 'display',
		render: (ctx) => <WorkspacesBody overview={ctx.overview} />,
	},
	{
		id: 'internet',
		titleKey: 'app.settings.section.internet',
		titleFallback: 'Internet',
		kind: 'placeholder',
		render: () => (
			<SectionPlaceholder titleKey="app.settings.section.internet" titleFallback="Internet" />
		),
	},
	{
		id: 'budget',
		titleKey: 'app.settings.section.budget',
		titleFallback: 'Budget',
		kind: 'wired',
		render: renderWired,
	},
	{
		id: 'project-credentials',
		titleKey: 'app.settings.section.credentials',
		titleFallback: 'Credentials',
		kind: 'display',
		render: (ctx) => <CredentialManagerPanel token={ctx.token} />,
	},
	{
		id: 'project-integrations',
		titleKey: 'app.settings.section.integrations',
		titleFallback: 'Integrations',
		kind: 'display',
		render: (ctx) => <IntegrationsBody overview={ctx.overview} />,
	},
	{
		id: 'project-advanced',
		titleKey: 'app.settings.section.advanced',
		titleFallback: 'Advanced',
		kind: 'display',
		render: (ctx) => (
			<AdvancedBody
				overview={ctx.overview}
				selectedProject={ctx.selectedProject}
				mutate={ctx.mutate}
				language={ctx.language}
			/>
		),
	},
];

/**
 * Maps a section id to the key used in `ResolvedSetting.section` or
 * `ResolvedSetting.projectSection` to filter which settings belong to a section.
 */
export const SECTION_TO_SETTING_SECTION: Record<string, string> = {
	autonomy: 'autonomy',
	security: 'security',
	costs: 'costs',
	'project-security': 'security',
	budget: 'budget',
};
