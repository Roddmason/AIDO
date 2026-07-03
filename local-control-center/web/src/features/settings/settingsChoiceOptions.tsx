/**
 * Enum-member card definitions for the settings radio-card selectors. Each option
 * pairs a Lucide icon with the existing enum label key (reused as the card title)
 * and a section-local description key that states what choosing that member does.
 * The raw member values match the settings registry enums so a card commit writes
 * a value the backend validator accepts.
 * @author Rodrigo Mason
 */

import {
	Ban,
	Cloud,
	Cpu,
	Feather,
	Flame,
	Hand,
	Layers,
	Scale,
	Shield,
	ShieldAlert,
	ShieldCheck,
	Shuffle,
	Terminal,
} from 'lucide-react';

import type { ChoiceCardOption } from './SettingsChoiceCards';

/** research.internetPolicy — how agents may reach the public internet. */
export const INTERNET_POLICY_OPTIONS: ReadonlyArray<ChoiceCardOption> = [
	{
		value: 'blocked',
		icon: Ban,
		titleKey: 'app.settings.enum.internet.blocked',
		titleFallback: 'Blocked',
		descKey: 'app.settings.enum.internet.blockedDesc',
		descFallback: 'Agents never reach the public internet.',
	},
	{
		value: 'official_allowlist',
		icon: ShieldCheck,
		titleKey: 'app.settings.enum.internet.officialAllowlist',
		titleFallback: 'Official allowlist',
		descKey: 'app.settings.enum.internet.officialAllowlistDesc',
		descFallback: 'Only vetted official documentation domains are reachable.',
	},
	{
		value: 'policy_gated',
		icon: Shield,
		titleKey: 'app.settings.enum.internet.policyGated',
		titleFallback: 'Policy gated',
		descKey: 'app.settings.enum.internet.policyGatedDesc',
		descFallback: 'Each fetch is checked against the security policy before it runs.',
	},
];

/** project.loop.teamMode — how many roles the loop engages per pass. */
export const TEAM_MODE_OPTIONS: ReadonlyArray<ChoiceCardOption> = [
	{
		value: 'economy',
		icon: Feather,
		titleKey: 'app.settings.enum.teamMode.economy',
		titleFallback: 'Economy',
		descKey: 'app.settings.enum.teamMode.economyDesc',
		descFallback: 'Fewest roles for the fastest, cheapest passes.',
	},
	{
		value: 'balanced',
		icon: Scale,
		titleKey: 'app.settings.enum.teamMode.balanced',
		titleFallback: 'Balanced',
		descKey: 'app.settings.enum.teamMode.balancedDesc',
		descFallback: 'A balanced roster for everyday delivery.',
	},
	{
		value: 'critical',
		icon: ShieldAlert,
		titleKey: 'app.settings.enum.teamMode.critical',
		titleFallback: 'Critical',
		descKey: 'app.settings.enum.teamMode.criticalDesc',
		descFallback: 'Adds review and security roles for risky work.',
	},
	{
		value: 'maximum',
		icon: Layers,
		titleKey: 'app.settings.enum.teamMode.maximum',
		titleFallback: 'Maximum',
		descKey: 'app.settings.enum.teamMode.maximumDesc',
		descFallback: 'The full roster with every guardrail engaged.',
	},
];

/** project.loop.risk — how much scrutiny the loop applies before accepting work. */
export const RISK_OPTIONS: ReadonlyArray<ChoiceCardOption> = [
	{
		value: 'low',
		icon: ShieldCheck,
		titleKey: 'app.settings.enum.risk.low',
		titleFallback: 'Low',
		descKey: 'app.settings.enum.risk.lowDesc',
		descFallback: 'Routine changes with minimal oversight.',
	},
	{
		value: 'medium',
		icon: Shield,
		titleKey: 'app.settings.enum.risk.medium',
		titleFallback: 'Medium',
		descKey: 'app.settings.enum.risk.mediumDesc',
		descFallback: 'Standard oversight for typical delivery.',
	},
	{
		value: 'high',
		icon: ShieldAlert,
		titleKey: 'app.settings.enum.risk.high',
		titleFallback: 'High',
		descKey: 'app.settings.enum.risk.highDesc',
		descFallback: 'Extra checks before anything is accepted.',
	},
	{
		value: 'critical',
		icon: Flame,
		titleKey: 'app.settings.enum.risk.critical',
		titleFallback: 'Critical',
		descKey: 'app.settings.enum.risk.criticalDesc',
		descFallback: 'Maximum scrutiny; nothing ships unreviewed.',
	},
];

/** project.runtime.defaultMode — the preferred runtime for each step. */
export const RUNTIME_MODE_OPTIONS: ReadonlyArray<ChoiceCardOption> = [
	{
		value: 'api',
		icon: Cloud,
		titleKey: 'app.settings.enum.mode.api',
		titleFallback: 'API (direct)',
		descKey: 'app.settings.enum.mode.apiDesc',
		descFallback: 'Call hosted provider APIs directly.',
	},
	{
		value: 'cli',
		icon: Terminal,
		titleKey: 'app.settings.enum.mode.cli',
		titleFallback: 'CLI runtime',
		descKey: 'app.settings.enum.mode.cliDesc',
		descFallback: 'Drive installed CLI runtimes on this machine.',
	},
	{
		value: 'ollama',
		icon: Cpu,
		titleKey: 'app.settings.enum.mode.ollama',
		titleFallback: 'Ollama (local)',
		descKey: 'app.settings.enum.mode.ollamaDesc',
		descFallback: 'Run local models through Ollama.',
	},
	{
		value: 'hybrid',
		icon: Shuffle,
		titleKey: 'app.settings.enum.mode.hybrid',
		titleFallback: 'Hybrid',
		descKey: 'app.settings.enum.mode.hybridDesc',
		descFallback: 'Mix API, CLI and local as each step needs.',
	},
	{
		value: 'manual',
		icon: Hand,
		titleKey: 'app.settings.enum.mode.manual',
		titleFallback: 'Manual',
		descKey: 'app.settings.enum.mode.manualDesc',
		descFallback: 'Pick the runtime for every step by hand.',
	},
];
