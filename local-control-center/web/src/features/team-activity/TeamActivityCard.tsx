/**
 * One Team Activity entry rendered as a card: who is working and the state of their work.
 *
 * Surfaces the headline fields the operator needs at a glance — agent and role, provider/runtime,
 * current assignment, blocked reason, completed artifact, reviewer, duration and (when known) cost —
 * with the noisy low-level events folded into {@link TeamActivityEvents}. Every field renders an
 * honest fallback when its data is absent rather than implying activity that did not happen.
 * @author Rodrigo Mason
 */
import { Bot } from 'lucide-react';
import type { TeamActivityEntry } from '../../api/types';
import { Disclosure } from '../../components/Disclosure';
import { Badge } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import {
	formatCostUsd,
	formatDurationMs,
	redactVisibleSecret,
	shortId,
	toneForStatus,
} from '../../lib/format';
import { CliSessionActivity } from '../model-gateway/CliSessionActivity';
import { TeamActivityEvents } from './TeamActivityEvents';

/** Maps the coarse entry state to a visual tone for the state badge. */
function toneForState(state: TeamActivityEntry['state']): 'ok' | 'warn' | 'danger' | 'info' {
	if (state === 'blocked') return 'danger';
	if (state === 'active') return 'warn';
	return 'ok';
}

function structuredOutput(entry: TeamActivityEntry): Record<string, unknown> {
	const output = entry.developerDetails?.output;
	if (!output || typeof output !== 'object' || Array.isArray(output)) return {};
	return output as Record<string, unknown>;
}

function stringField(output: Record<string, unknown>, key: string): string | null {
	const value = output[key];
	return typeof value === 'string' && value.trim() ? value : null;
}

function stringListField(output: Record<string, unknown>, key: string): string[] {
	const value = output[key];
	if (!Array.isArray(value)) return [];
	return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
}

function artifactSummaries(
	output: Record<string, unknown>,
	labels: { runtimeLog: string; stdout: string; stderr: string; diff: string },
) {
	return [
		{ label: labels.runtimeLog, id: stringField(output, 'logsArtifactId') },
		{ label: labels.stdout, id: stringField(output, 'stdoutArtifactId') },
		{ label: labels.stderr, id: stringField(output, 'stderrArtifactId') },
		{ label: labels.diff, id: stringField(output, 'diffArtifactId') },
	].filter((artifact): artifact is { label: string; id: string } => Boolean(artifact.id));
}

/** Renders a single agent's current activity as a card. */
export function TeamActivityCard({ entry, token }: { entry: TeamActivityEntry; token?: string }) {
	const { t } = useI18n();
	const assignment = entry.currentAssignment;
	const artifact = entry.completedArtifact;
	const reviewer = entry.reviewer;
	const nextStep = entry.nextStep;
	const output = structuredOutput(entry);
	const cliSessionId = stringField(output, 'cliSessionId');
	const changedFiles = stringListField(output, 'changedFiles');
	const runtimeArtifacts = artifactSummaries(output, {
		runtimeLog: t('app.teamActivity.runtimeLogArtifact', 'Runtime log'),
		stdout: t('app.teamActivity.stdoutArtifact', 'Standard output'),
		stderr: t('app.teamActivity.stderrArtifact', 'Standard error'),
		diff: t('app.teamActivity.diffArtifact', 'Diff patch'),
	});
	const evidencePackageId = stringField(output, 'evidencePackageId');

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
				{nextStep ? (
					<div className="activity-field">
						<dt>{t('app.teamActivity.nextStep', 'Next step')}</dt>
						<dd>
							{nextStep.source === 'handoff'
								? t('app.teamActivity.nextStepHandoff', 'Hand off to {agent}').replace(
										'{agent}',
										nextStep.handoffTo ?? '',
									)
								: redactVisibleSecret(nextStep.text)}
						</dd>
					</div>
				) : null}
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

			{cliSessionId ? (
				<div className="activity-detail">
					<Disclosure
						title={t('app.teamActivity.executionLogs', 'Execution logs')}
						summary={shortId(cliSessionId)}
						defaultOpen={entry.state === 'active'}
					>
						<CliSessionActivity sessionId={cliSessionId} token={token} embedded />
						{runtimeArtifacts.length ? (
							<ul
								className="activity-artifacts"
								aria-label={t('app.teamActivity.logArtifacts', 'Log artifacts')}
							>
								{runtimeArtifacts.map((item) => (
									<li className="activity-artifact" key={item.label}>
										<span>{item.label}</span>
										<code>{shortId(item.id)}</code>
									</li>
								))}
							</ul>
						) : null}
						{evidencePackageId ? (
							<p className="activity-muted mono">
								{t('app.teamActivity.evidencePackage', 'Evidence package')} ·{' '}
								{shortId(evidencePackageId)}
							</p>
						) : null}
					</Disclosure>
				</div>
			) : null}

			{cliSessionId ? (
				<div className="activity-detail">
					<Disclosure
						title={t('app.teamActivity.modifiedFiles', 'Modified files')}
						summary={t('app.teamActivity.fileCount', '{n} files').replace(
							'{n}',
							String(changedFiles.length),
						)}
					>
						{changedFiles.length ? (
							<ul className="activity-files">
								{changedFiles.map((path) => (
									<li className="activity-file mono" key={path}>
										{redactVisibleSecret(path)}
									</li>
								))}
							</ul>
						) : (
							<p className="activity-empty">
								{t('app.teamActivity.noModifiedFiles', 'No modified files recorded yet.')}
							</p>
						)}
					</Disclosure>
				</div>
			) : null}

			<TeamActivityEvents entry={entry} />
		</article>
	);
}
