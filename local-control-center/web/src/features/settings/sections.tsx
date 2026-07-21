/**
 * Section registry for the Settings modal: defines all navigation sections for
 * the General and Project scopes, their kind (wired/display), and the render
 * context type shared across all section render functions.
 *
 * I-4/B-3: each section's `render(ctx)` is fully implemented here, so SettingsModal
 * only calls `activeSection.render(ctx)` — no switch needed. Adding a new section
 * requires only this registry. Every section renders real control-plane data:
 * there are no placeholder sections.
 * @author Rodrigo Mason
 */

import type { LucideIcon } from 'lucide-react';
import {
	CheckCircle2,
	CircleDollarSign,
	Cpu,
	FlaskConical,
	FolderTree,
	GitBranch,
	Globe,
	KeyRound,
	Lock,
	Palette,
	Puzzle,
	Route,
	ShieldCheck,
	SlidersHorizontal,
	Target,
	Users,
	Wrench,
} from 'lucide-react';
import type { ReactNode } from 'react';

import type { JsonValue } from '../../api/generated/openapi';
import type {
	Overview,
	Project,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../../api/types';
import { AppearanceSettingsPanel } from './AppearanceSettingsPanel';
import { AutonomyBody } from './AutonomyBody';
import { CredentialManagerPanel } from './CredentialManagerPanel';
import { DefaultTeamSettingsPanel } from './DefaultTeamSettingsPanel';
import { GeneralSettingsPanel } from './GeneralSettingsPanel';
import { InternetSettingsPanel } from './InternetSettingsPanel';
import { PluginsPanel } from './PluginsPanel';
import { ProjectGoalSettingsPanel } from './ProjectGoalSettingsPanel';
import { ProjectPluginsBody } from './ProjectPluginsBody';
import { ProjectQualitySettingsPanel } from './ProjectQualitySettingsPanel';
import { ProjectRoutingSettingsPanel } from './ProjectRoutingSettingsPanel';
import { ProjectTeamPanel } from './ProjectTeamPanel';
import { ResearchSettingsPanel } from './ResearchSettingsPanel';
import { RuntimeAccessPanel } from './RuntimeAccessPanel';
import { SettingRow } from './SettingRow';
import { AdvancedBody, RuntimeBody, WorkspacesBody } from './SettingsPage';
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
	/** Provider id the runtime setup section should preselect when opened from a remediation. */
	initialProviderId: string | null;
	/** Closes the Settings modal; sections whose links navigate to another page
	 *  must call it so the overlay never survives the navigation it triggered. */
	closeSettings: () => void;
};

/** A single section definition in the settings navigator. */
export type SectionDefinition = {
	id: string;
	/** i18n key for the section title. */
	titleKey: string;
	/** English fallback for the section title. */
	titleFallback: string;
	/** Navigator icon: anchors scanning; rendered aria-hidden next to the title. */
	icon: LucideIcon;
	/** Whether this section edits wired settings or renders a display body. */
	kind: 'wired' | 'display';
	/** Renders the section's content given the shared context. */
	render: (ctx: SectionContext) => ReactNode;
};

/**
 * Human labels for wired enum settings: raw member values (`feature_branch`,
 * `policy_gated`, …) read as machine identifiers, so the modal translates them
 * through these keys before they reach a `<select>`.
 */
const SETTING_ENUM_LABELS: Record<string, Record<string, { key: string; fallback: string }>> = {
	'autonomy.level': {
		guided: { key: 'app.composer.permissions.guided', fallback: 'Ask for approval' },
		recommended: { key: 'app.composer.permissions.recommended', fallback: 'Approve for me' },
		autonomous: { key: 'app.composer.permissions.autonomous', fallback: 'Full access' },
	},
	'project.git.integrationMode': {
		direct_push: {
			key: 'app.settings.enum.integrationMode.directPush',
			fallback: 'Direct push to dev',
		},
		auto_pr: {
			key: 'app.settings.enum.integrationMode.autoPr',
			fallback: 'Auto PR (TL approves & merges)',
		},
		manual_pr: {
			key: 'app.settings.enum.integrationMode.manualPr',
			fallback: 'Manual PR (human approves)',
		},
	},
	'security.shell.profile': {
		plan: { key: 'app.settings.enum.shell.plan', fallback: 'Plan (read-only)' },
		dev_safe: { key: 'app.settings.enum.shell.devSafe', fallback: 'Dev safe' },
		qa: { key: 'app.settings.enum.shell.qa', fallback: 'QA checks' },
		release: { key: 'app.settings.enum.shell.release', fallback: 'Release' },
	},
	'research.internetPolicy': {
		blocked: { key: 'app.settings.enum.internet.blocked', fallback: 'Blocked' },
		official_allowlist: {
			key: 'app.settings.enum.internet.officialAllowlist',
			fallback: 'Official allowlist',
		},
		policy_gated: { key: 'app.settings.enum.internet.policyGated', fallback: 'Policy gated' },
	},
	'project.loop.teamMode': {
		economy: { key: 'app.settings.enum.teamMode.economy', fallback: 'Economy' },
		balanced: { key: 'app.settings.enum.teamMode.balanced', fallback: 'Balanced' },
		critical: { key: 'app.settings.enum.teamMode.critical', fallback: 'Critical' },
		maximum: { key: 'app.settings.enum.teamMode.maximum', fallback: 'Maximum' },
	},
	'project.loop.risk': {
		low: { key: 'app.settings.enum.risk.low', fallback: 'Low' },
		medium: { key: 'app.settings.enum.risk.medium', fallback: 'Medium' },
		high: { key: 'app.settings.enum.risk.high', fallback: 'High' },
		critical: { key: 'app.settings.enum.risk.critical', fallback: 'Critical' },
	},
	'project.runtime.defaultMode': {
		api: { key: 'app.settings.enum.mode.api', fallback: 'API (direct)' },
		cli: { key: 'app.settings.enum.mode.cli', fallback: 'CLI runtime' },
		ollama: { key: 'app.settings.enum.mode.ollama', fallback: 'Ollama (local)' },
		hybrid: { key: 'app.settings.enum.mode.hybrid', fallback: 'Hybrid' },
		manual: { key: 'app.settings.enum.mode.manual', fallback: 'Manual' },
	},
};

/** Translated option labels for a wired enum setting, or undefined when unmapped. */
export function labeledEnumOptions(
	setting: ResolvedSetting,
	t: (key: string, fallback: string) => string,
): Array<{ value: string; label: string }> | undefined {
	const labels = SETTING_ENUM_LABELS[setting.key];
	if (!labels || !setting.enum) return undefined;
	return setting.enum.map((member) => ({
		value: member,
		label: labels[member] ? t(labels[member].key, labels[member].fallback) : member,
	}));
}

function renderWired(ctx: SectionContext): ReactNode {
	if (ctx.resolved.length === 0) {
		return (
			<p className="muted">
				{ctx.t('app.settings.section.noSettings', 'No settings found for this section.')}
			</p>
		);
	}
	return (
		<div className="settings-list">
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

/** Wired section with a one-line intro so each surface states what it governs. */
function wiredSection(introKey: string, introFallback: string) {
	return (ctx: SectionContext): ReactNode => (
		<>
			<p className="settings-section-intro">{ctx.t(introKey, introFallback)}</p>
			{renderWired(ctx)}
		</>
	);
}

/**
 * General scope sections in display order.
 * General, Appearance, Providers & CLI, Credentials, Default Team, Autonomy,
 * Security, Research, Costs, Plugins, Advanced — all wired to real data.
 */
export const GENERAL_SECTIONS: SectionDefinition[] = [
	{
		id: 'general',
		titleKey: 'app.settings.section.general',
		titleFallback: 'General',
		icon: SlidersHorizontal,
		kind: 'wired',
		render: (ctx) => <GeneralSettingsPanel ctx={ctx} />,
	},
	{
		id: 'appearance',
		titleKey: 'app.settings.section.appearance',
		titleFallback: 'Appearance',
		icon: Palette,
		kind: 'display',
		render: () => <AppearanceSettingsPanel />,
	},
	{
		id: 'providers-cli',
		titleKey: 'app.settings.section.providersCli',
		titleFallback: 'Providers & CLI',
		icon: Cpu,
		kind: 'display',
		render: (ctx) => (
			<>
				<RuntimeAccessPanel ctx={ctx} />
				<RuntimeBody
					overview={ctx.overview}
					runtimeProviders={ctx.runtimeProviders}
					runtimeProviderConfiguration={ctx.runtimeProviderConfiguration}
					token={ctx.token}
					onRefresh={ctx.onRefresh}
					initialProviderId={ctx.initialProviderId}
					onNavigate={ctx.closeSettings}
				/>
			</>
		),
	},
	{
		id: 'credentials',
		titleKey: 'app.settings.section.credentials',
		titleFallback: 'Credentials',
		icon: KeyRound,
		kind: 'display',
		render: (ctx) => <CredentialManagerPanel token={ctx.token} />,
	},
	{
		id: 'default-team',
		titleKey: 'app.settings.section.defaultTeam',
		titleFallback: 'Default Team',
		icon: Users,
		kind: 'display',
		render: (ctx) => (
			<DefaultTeamSettingsPanel overview={ctx.overview} onNavigate={ctx.closeSettings} />
		),
	},
	{
		id: 'autonomy',
		titleKey: 'app.settings.section.autonomy',
		titleFallback: 'Autonomy',
		icon: ShieldCheck,
		kind: 'wired',
		render: (ctx) => {
			const setting = ctx.resolved.find((entry) => entry.key === 'autonomy.level');
			return (
				<AutonomyBody
					setting={setting}
					onSelect={(level) => ctx.setValue('autonomy.level', ctx.scope, ctx.scopeId, level)}
					onRevert={() => ctx.clearValue('autonomy.level', ctx.scope, ctx.scopeId)}
				/>
			);
		},
	},
	{
		id: 'security',
		titleKey: 'app.settings.section.security',
		titleFallback: 'Security',
		icon: Lock,
		kind: 'wired',
		render: wiredSection(
			'app.settings.intro.security',
			'Guardrails applied to every write the platform performs.',
		),
	},
	{
		id: 'research',
		titleKey: 'app.settings.section.research',
		titleFallback: 'Research',
		icon: FlaskConical,
		kind: 'wired',
		render: (ctx) => <ResearchSettingsPanel ctx={ctx} />,
	},
	{
		id: 'costs',
		titleKey: 'app.settings.section.costs',
		titleFallback: 'Costs',
		icon: CircleDollarSign,
		kind: 'wired',
		render: wiredSection('app.settings.intro.costs', 'Spending ceilings for runs and jobs.'),
	},
	{
		id: 'plugins',
		titleKey: 'app.settings.section.plugins',
		titleFallback: 'Plugins',
		icon: Puzzle,
		kind: 'display',
		render: (ctx) => <PluginsPanel token={ctx.token} />,
	},
	{
		id: 'advanced',
		titleKey: 'app.settings.section.advanced',
		titleFallback: 'Advanced',
		icon: Wrench,
		kind: 'display',
		render: (ctx) => (
			<AdvancedBody
				overview={ctx.overview}
				selectedProject={ctx.selectedProject}
				mutate={ctx.mutate}
				language={ctx.language}
				onNavigate={ctx.closeSettings}
			/>
		),
	},
];

/**
 * Project scope sections in display order.
 * Goal, Team, Routing, Quality, Security, Workspaces, Internet, Budget,
 * Credentials, Plugins — all project-scoped over real data. The operational
 * project is chosen from the context bar the modal renders above these sections.
 */
export const PROJECT_SECTIONS: SectionDefinition[] = [
	{
		id: 'goal',
		titleKey: 'app.settings.section.goal',
		titleFallback: 'Goal',
		icon: Target,
		kind: 'wired',
		render: (ctx) => <ProjectGoalSettingsPanel ctx={ctx} />,
	},
	{
		id: 'team',
		titleKey: 'app.settings.section.team',
		titleFallback: 'Team',
		icon: Users,
		kind: 'display',
		render: (ctx) => <ProjectTeamPanel projectId={ctx.scopeId} onNavigate={ctx.closeSettings} />,
	},
	{
		id: 'routing',
		titleKey: 'app.settings.section.routing',
		titleFallback: 'Routing',
		icon: Route,
		kind: 'wired',
		render: (ctx) => <ProjectRoutingSettingsPanel ctx={ctx} />,
	},
	{
		id: 'quality',
		titleKey: 'app.settings.section.quality',
		titleFallback: 'Quality',
		icon: CheckCircle2,
		kind: 'wired',
		render: (ctx) => <ProjectQualitySettingsPanel ctx={ctx} />,
	},
	{
		id: 'project-security',
		titleKey: 'app.settings.section.security',
		titleFallback: 'Security',
		icon: Lock,
		kind: 'wired',
		render: wiredSection(
			'app.settings.intro.projectSecurity',
			'Project-level overrides of the security posture.',
		),
	},
	{
		id: 'git',
		titleKey: 'app.settings.section.git',
		titleFallback: 'Git',
		icon: GitBranch,
		kind: 'wired',
		render: wiredSection(
			'app.settings.intro.git',
			'Base branch and how finished work lands: direct push, auto PR, or manual PR.',
		),
	},
	{
		id: 'workspaces',
		titleKey: 'app.settings.section.workspaces',
		titleFallback: 'Workspaces',
		icon: FolderTree,
		kind: 'display',
		render: (ctx) => <WorkspacesBody overview={ctx.overview} />,
	},
	{
		id: 'internet',
		titleKey: 'app.settings.section.internet',
		titleFallback: 'Internet',
		icon: Globe,
		kind: 'wired',
		render: (ctx) => <InternetSettingsPanel ctx={ctx} />,
	},
	{
		id: 'budget',
		titleKey: 'app.settings.section.budget',
		titleFallback: 'Budget',
		icon: CircleDollarSign,
		kind: 'wired',
		render: wiredSection('app.settings.intro.budget', 'Spending limits for this project.'),
	},
	{
		id: 'project-credentials',
		titleKey: 'app.settings.section.credentials',
		titleFallback: 'Credentials',
		icon: KeyRound,
		kind: 'display',
		render: (ctx) => <CredentialManagerPanel token={ctx.token} />,
	},
	{
		id: 'project-plugins',
		titleKey: 'app.settings.section.plugins',
		titleFallback: 'Plugins',
		icon: Puzzle,
		kind: 'display',
		render: () => <ProjectPluginsBody />,
	},
];

/**
 * Maps a section id to the key used in `ResolvedSetting.section` or
 * `ResolvedSetting.projectSection` to filter which settings belong to a section.
 */
export const SECTION_TO_SETTING_SECTION: Record<string, string> = {
	general: 'worker',
	'providers-cli': 'runtime',
	autonomy: 'autonomy',
	security: 'security',
	research: 'research',
	costs: 'costs',
	goal: 'goal',
	routing: 'runtime',
	quality: 'quality',
	'project-security': 'security',
	git: 'git',
	internet: 'internet',
	budget: 'budget',
};
