/**
 * Default Team panel (general scope) and the roster card grid it shares with the project
 * Team panel: one card per agent profile with runtime availability, providers, reviewer,
 * per-run budget and quality-gate count.
 *
 * Cards over a table: the roster is scanned by role to answer "can this agent run, and
 * what may it use", not compared column-by-column across every profile.
 * @author Rodrigo Mason
 */

import { useMemo } from 'react';

import type { AgentProfile, Overview } from '../../api/types';
import { Badge, EmptyState } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { ConsoleLink } from './SettingsPage';

/** Formats a per-run USD budget, or the translated 'no cap' label when unbounded (<= 0). */
function usdLabel(amount: number, noCap: string): string {
	return amount > 0 ? `$${amount.toFixed(2)}` : noCap;
}

/** Reads the reviewer role from an agent profile's opaque reviewer-policy object. */
function reviewerRoleOf(reviewerPolicy: Record<string, unknown> | null | undefined): string {
	return reviewerPolicy && typeof reviewerPolicy.reviewerRole === 'string'
		? reviewerPolicy.reviewerRole
		: '';
}

/**
 * Shared roster card grid for the Default Team (general) and project Team sections.
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
				const reviewerRole = reviewerRoleOf(
					profile.reviewerPolicy as Record<string, unknown> | null,
				);
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
								<dt>{t('app.settings.team.reviewer', 'Reviewer')}</dt>
								<dd className="mono">{reviewerRole || '—'}</dd>
							</div>
							<div>
								<dt>{t('app.settings.team.budgetPerRun', 'Budget / run')}</dt>
								<dd className="mono">
									{usdLabel(profile.maxCostPerRun, t('app.settings.team.noBudget', 'No cap'))}
								</dd>
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
export function DefaultTeamSettingsPanel({
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
