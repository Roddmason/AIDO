/**
 * Project Team section body: the effective agent-profile roster for the selected
 * project, fetched with the project id so per-project overrides (status, providers,
 * quality gates) are applied by the repository before rendering. The overview roster
 * cannot be reused here: it resolves profiles without a project, so overrides and
 * their `projectOverride` marker would always be absent.
 * @author Rodrigo Mason
 */

import { useEffect, useMemo, useState } from 'react';

import { getAgentProfiles } from '../../api/client';
import type { AgentProfile } from '../../api/types';
import { EmptyState, ErrorState, Skeleton } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { TeamRosterCards } from './DefaultTeamSettingsPanel';
import { ConsoleLink } from './SettingsPage';

export function ProjectTeamPanel({
	projectId,
	onNavigate,
}: {
	projectId: string | null;
	onNavigate?: () => void;
}) {
	const { t } = useI18n();
	const [profiles, setProfiles] = useState<AgentProfile[] | null>(null);
	const [error, setError] = useState('');

	useEffect(() => {
		if (!projectId) {
			setProfiles(null);
			setError('');
			return;
		}
		const controller = new AbortController();
		setProfiles(null);
		setError('');
		getAgentProfiles(projectId, controller.signal)
			.then((payload) => setProfiles(payload.agentProfiles))
			.catch((err: unknown) => {
				if (controller.signal.aborted) return;
				setError(err instanceof Error ? err.message : String(err));
			});
		return () => controller.abort();
	}, [projectId]);

	const sorted = useMemo(
		() => (profiles ? [...profiles].sort((a, b) => a.role.localeCompare(b.role)) : []),
		[profiles],
	);

	if (!projectId) {
		return (
			<EmptyState
				title={t('app.settings.team.selectProjectTitle', 'Select a project')}
				body={t(
					'app.settings.team.selectProjectBody',
					'Choose an operational project in the bar above to see its team.',
				)}
			/>
		);
	}

	if (error) {
		return (
			<ErrorState
				title={t('app.settings.team.loadFailed', 'Failed to load the project team')}
				body={error}
			/>
		);
	}

	if (profiles === null) {
		return (
			<>
				<Skeleton
					className="settings-skeleton"
					label={t('app.settings.team.loading', 'Loading project team...')}
				/>
				<Skeleton className="settings-skeleton" />
			</>
		);
	}

	const overrideCount = sorted.filter((profile) => profile.projectOverride).length;

	return (
		<>
			<p className="settings-section-intro">
				{t(
					'app.settings.team.projectIntro',
					'Effective team for this project. Overrides replace the general defaults.',
				)}{' '}
				{overrideCount > 0
					? `${overrideCount} ${t('app.settings.team.overridesActive', 'profiles carry a project override.')}`
					: t('app.settings.team.noOverrides', 'No project overrides are active.')}
			</p>
			<TeamRosterCards profiles={sorted} showOverride />
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
