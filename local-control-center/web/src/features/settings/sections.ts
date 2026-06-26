/**
 * Section registry for the Settings modal: defines all navigation sections for
 * the General and Project scopes, their kind (wired/display/placeholder), and the
 * render context type shared across all section render functions.
 */

import type { ReactNode } from 'react';

import type { JsonValue } from '../../api/generated/openapi';
import type { Overview, Project, RuntimeProviderConfiguration, RuntimeProviders } from '../../api/types';
import type { ResolvedSetting } from './useSettings';

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
	setValue: (key: string, scope: string, scopeId: string | null, value: JsonValue) => Promise<void>;
	/** Clear an override value to revert to inherited. */
	clearValue: (key: string, scope: string, scopeId: string | null) => Promise<void>;
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
		render: () => null,
	},
	{
		id: 'appearance',
		titleKey: 'app.settings.section.appearance',
		titleFallback: 'Appearance',
		kind: 'display',
		render: () => null,
	},
	{
		id: 'providers-cli',
		titleKey: 'app.settings.section.providersCli',
		titleFallback: 'Providers & CLI',
		kind: 'display',
		render: () => null,
	},
	{
		id: 'credentials',
		titleKey: 'app.settings.section.credentials',
		titleFallback: 'Credentials',
		kind: 'display',
		render: () => null,
	},
	{
		id: 'default-team',
		titleKey: 'app.settings.section.defaultTeam',
		titleFallback: 'Default Team',
		kind: 'placeholder',
		render: () => null,
	},
	{
		id: 'autonomy',
		titleKey: 'app.settings.section.autonomy',
		titleFallback: 'Autonomy',
		kind: 'wired',
		render: () => null,
	},
	{
		id: 'security',
		titleKey: 'app.settings.section.security',
		titleFallback: 'Security',
		kind: 'wired',
		render: () => null,
	},
	{
		id: 'research',
		titleKey: 'app.settings.section.research',
		titleFallback: 'Research',
		kind: 'placeholder',
		render: () => null,
	},
	{
		id: 'costs',
		titleKey: 'app.settings.section.costs',
		titleFallback: 'Costs',
		kind: 'wired',
		render: () => null,
	},
	{
		id: 'integrations',
		titleKey: 'app.settings.section.integrations',
		titleFallback: 'Integrations',
		kind: 'display',
		render: () => null,
	},
	{
		id: 'advanced',
		titleKey: 'app.settings.section.advanced',
		titleFallback: 'Advanced',
		kind: 'display',
		render: () => null,
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
		render: () => null,
	},
	{
		id: 'goal',
		titleKey: 'app.settings.section.goal',
		titleFallback: 'Goal',
		kind: 'placeholder',
		render: () => null,
	},
	{
		id: 'team',
		titleKey: 'app.settings.section.team',
		titleFallback: 'Team',
		kind: 'display',
		render: () => null,
	},
	{
		id: 'routing',
		titleKey: 'app.settings.section.routing',
		titleFallback: 'Routing',
		kind: 'display',
		render: () => null,
	},
	{
		id: 'quality',
		titleKey: 'app.settings.section.quality',
		titleFallback: 'Quality',
		kind: 'display',
		render: () => null,
	},
	{
		id: 'project-security',
		titleKey: 'app.settings.section.security',
		titleFallback: 'Security',
		kind: 'wired',
		render: () => null,
	},
	{
		id: 'workspaces',
		titleKey: 'app.settings.section.workspaces',
		titleFallback: 'Workspaces',
		kind: 'display',
		render: () => null,
	},
	{
		id: 'internet',
		titleKey: 'app.settings.section.internet',
		titleFallback: 'Internet',
		kind: 'placeholder',
		render: () => null,
	},
	{
		id: 'budget',
		titleKey: 'app.settings.section.budget',
		titleFallback: 'Budget',
		kind: 'wired',
		render: () => null,
	},
	{
		id: 'project-credentials',
		titleKey: 'app.settings.section.credentials',
		titleFallback: 'Credentials',
		kind: 'display',
		render: () => null,
	},
	{
		id: 'project-integrations',
		titleKey: 'app.settings.section.integrations',
		titleFallback: 'Integrations',
		kind: 'display',
		render: () => null,
	},
	{
		id: 'project-advanced',
		titleKey: 'app.settings.section.advanced',
		titleFallback: 'Advanced',
		kind: 'display',
		render: () => null,
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
