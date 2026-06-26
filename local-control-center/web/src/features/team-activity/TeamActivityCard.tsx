/**
 * One Team Activity entry rendered as a card: who is working and the state of their work.
 *
 * Surfaces the headline fields the operator needs at a glance — agent and role, provider/runtime,
 * current assignment, blocked reason, completed artifact, reviewer, duration and (when known) cost —
 * with the noisy low-level events folded into {@link TeamActivityEvents}. Every field renders an
 * honest fallback when its data is absent rather than implying activity that did not happen.
 */
import { Bot } from 'lucide-react';
import type { TeamActivityEntry } from '../../api/types';
import { Badge } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import {
	formatCostUsd,
	formatDurationMs,
	redactVisibleSecret,
	toneForStatus,
} from '../../lib/format';
import { TeamActivityEvents } from './TeamActivityEvents';

/** Maps the coarse entry state to a visual tone for the state badge. */
function toneForState(state: TeamActivityEntry['state']): 'ok' | 'warn' | 'danger' | 'info' {
	if (state === 'blocked') return 'danger';
	if (state === 'active') return 'warn';
	return 'ok';
}

/** Renders a single agent's current activity as a card. */
export function TeamActivityCard({ entry }: { entry: TeamActivityEntry }) {
	const { t } = useI18n();
	const assignment = entry.currentAssignment;
	const artifact = entry.completedArtifact;
	const reviewer = entry.reviewer;

	return (
		<article className="activity-card" data-state={entry.state}>
			<header className="activity-card-head">
				<span className="activity-icon">
					<Bot aria-hidden="true" size={15} />
				</span>
				<div className="activity-identity">
					<strong>{entry.agentName}</strong>
					<span className="activity-role">{entry.role}</span>
				</div>
				<Badge tone={toneForState(entry.state)}>{entry.status}</Badge>
			</header>

			<dl className="activity-fields">
				<div className="activity-field">
					<dt>{t('app.teamActivity.runtime', 'Runtime')}</dt>
					<dd className="mono">{entry.runtime}</dd>
				</div>
				<div className="activity-field">
					<dt>{t('app.teamActivity.assignment', 'Assignment')}</dt>
					<dd>
						{assignment ? (
							<span className="activity-assignment">
								<span>{assignment.taskTitle || assignment.taskId}</span>
								<Badge tone={toneForStatus(assignment.assignmentStatus)}>
									{assignment.assignmentStatus}
								</Badge>
							</span>
						) : (
							<span className="activity-muted">
								{t('app.teamActivity.noAssignment', 'Unassigned')}
							</span>
						)}
					</dd>
				</div>
				<div className="activity-field">
					<dt>{t('app.teamActivity.duration', 'Duration')}</dt>
					<dd className="mono">{formatDurationMs(entry.durationMs)}</dd>
				</div>
				<div className="activity-field">
					<dt>{t('app.teamActivity.cost', 'Cost')}</dt>
					<dd className="mono">
						{entry.costUsd == null
							? t('app.teamActivity.costUnknown', 'not recorded')
							: formatCostUsd(entry.costUsd)}
					</dd>
				</div>
				{reviewer ? (
					<div className="activity-field">
						<dt>{t('app.teamActivity.reviewer', 'Reviewer')}</dt>
						<dd className="mono">
							{reviewer.reviewerAgentId}
							{reviewer.decision ? ` · ${reviewer.decision}` : ` · ${reviewer.status}`}
						</dd>
					</div>
				) : null}
				{artifact ? (
					<div className="activity-field">
						<dt>{t('app.teamActivity.artifact', 'Completed artifact')}</dt>
						{/* title mirrors the visible text so the full value survives truncation. */}
						<dd
							className="mono activity-truncate"
							title={`${artifact.kind} · ${artifact.name || artifact.path || artifact.artifactId}`}
						>
							{artifact.kind} · {artifact.name || artifact.path || artifact.artifactId}
						</dd>
					</div>
				) : null}
			</dl>

			{entry.blockedReason ? (
				<p className="activity-blocked">
					<Badge tone="danger">{t('app.teamActivity.blocked', 'Blocked')}</Badge>
					<span>{redactVisibleSecret(entry.blockedReason)}</span>
				</p>
			) : null}

			<TeamActivityEvents entry={entry} />
		</article>
	);
}
