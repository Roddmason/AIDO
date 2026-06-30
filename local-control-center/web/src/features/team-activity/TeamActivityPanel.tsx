/**
 * Team Activity board: the live roster of what the AI delivery team is doing right now.
 *
 * Renders the per-project activity entries from {@link useTeamActivity} — active and blocked work
 * first, then recent deliveries — each as a {@link TeamActivityCard}. Honest by construction: an
 * absent project, an empty board, a load error and a still-loading fetch each render their own state
 * rather than a fabricated roster. Mounted inside the workbench "AI team" drawer and fetches only
 * while `open`, so it adds no cost when the drawer is closed.
 * @author Rodrigo Mason
 */
import { Badge, EmptyState } from '../../components/primitives';
import { ErrorState, Skeleton } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { TeamActivityCard } from './TeamActivityCard';
import { useTeamActivity } from './useTeamActivity';

/** The Team Activity drawer body for `projectId`, fetching only while `open`. */
export function TeamActivityPanel({
	projectId,
	open,
	token,
}: {
	projectId: string | undefined;
	open: boolean;
	token?: string;
}) {
	const { t } = useI18n();
	const activity = useTeamActivity(projectId, open);

	if (!projectId) {
		return (
			<EmptyState
				title={t('app.teamActivity.noProjectTitle', 'No workspace selected')}
				body={t('app.teamActivity.noProjectBody', 'Pick a workspace to see its team activity.')}
			/>
		);
	}
	if (activity.loading && !activity.data) {
		return (
			<div className="stack compact" aria-busy="true">
				<Skeleton
					className="h-24"
					label={t('app.teamActivity.loading', 'Loading team activity…')}
				/>
				<Skeleton className="h-24" />
			</div>
		);
	}
	if (activity.error && !activity.data) {
		return (
			<ErrorState
				title={t('app.teamActivity.errorTitle', 'Could not load team activity')}
				body={activity.error}
			/>
		);
	}

	const data = activity.data;
	const entries = data?.entries ?? [];

	if (!entries.length) {
		return (
			<EmptyState
				title={t('app.teamActivity.emptyTitle', 'No team activity yet')}
				body={t(
					'app.teamActivity.emptyBody',
					'Agent runs show up here with their assignment, runtime, duration and cost as work starts.',
				)}
			/>
		);
	}

	return (
		<div className="team-activity">
			<header className="team-activity-summary" aria-live="polite">
				<Badge tone="warn">
					{t('app.teamActivity.activeCount', '{n} active').replace(
						'{n}',
						String(data?.activeCount ?? 0),
					)}
				</Badge>
				<Badge tone="danger">
					{t('app.teamActivity.blockedCount', '{n} blocked').replace(
						'{n}',
						String(data?.blockedCount ?? 0),
					)}
				</Badge>
				<span className="team-activity-total">
					{t('app.teamActivity.totalCount', '{n} total').replace(
						'{n}',
						String(data?.totalCount ?? 0),
					)}
				</span>
			</header>
			<div className="team-activity-list">
				{entries.map((entry) => (
					<TeamActivityCard entry={entry} key={entry.id} token={token} />
				))}
			</div>
			{data?.truncated ? (
				<p className="team-activity-truncated">
					{t('app.teamActivity.truncated', 'Older completed work is not shown.')}
				</p>
			) : null}
		</div>
	);
}
