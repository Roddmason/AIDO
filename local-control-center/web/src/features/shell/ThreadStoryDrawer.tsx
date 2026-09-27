/**
 * Detail drawer of one user story (HU) of the thread board: value statement and description, the
 * acceptance criteria with their met state, who is working it (loop role, runtime, model), progress,
 * the QA verdict, commit and runs recorded by the story loop, the agent tasks and the story's own
 * activity timeline (thread events whose payload names the story). The card is re-read from every
 * board refetch, so the drawer follows the loop live.
 *
 * Review is only offered where the backend has a real command for it: `request_changes` feedback on
 * one of the story's agent tasks (`POST /projects/{id}/product-loop/{loopId}/feedback`), accepted by
 * the loop FSM only while it waits for review or approval ({@link REVIEWABLE_LOOP_STATES}). There is
 * no per-story approval — the loop is approved as a whole in Approvals — so outside those states the
 * drawer is read-only and says why.
 * @author Rodrigo Mason
 */
import { CheckCircle2, Circle, GitCommit } from 'lucide-react';
import { type FormEvent, useState } from 'react';

import type { ThreadBoardCard } from '../../api/client';
import type { ThreadAgentEvent } from '../../api/types';
import { Button, Drawer, SelectField, StatusChip, StatusDot, TextArea } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { formatTime, toneForStatus } from '../../lib/format';
import { ProgressMeter, useAssigneeLine } from './ThreadBoard';
import { BOARD_COLUMN_META, REVIEWABLE_LOOP_STATES, reworkTargetTask } from './threadBoardModel';

type StatusCopy = { key: string; fallback: string };

const STORY_STATUS_COPY: Record<string, StatusCopy> = {
	todo: { key: 'app.threads.board.status.todo', fallback: 'Moved to To do' },
	in_progress: { key: 'app.threads.board.status.inProgress', fallback: 'Development started' },
	qa: { key: 'app.threads.board.status.qa', fallback: 'Moved to QA' },
	done: { key: 'app.threads.board.status.done', fallback: 'Marked done' },
	blocked: { key: 'app.threads.board.status.blocked', fallback: 'Blocked' },
	reopened: { key: 'app.threads.board.status.reopened', fallback: 'Reopened' },
};

export type ThreadStoryDrawerProps = {
	/** The live card, or null when the drawer is closed (or the story left the board). */
	card: ThreadBoardCard | null;
	loopState: string | null;
	/** Thread events already filtered to this story, newest first. */
	activity: ThreadAgentEvent[];
	onClose: () => void;
	/** Sends `request_changes` feedback on one of the story's tasks. */
	onRequestChanges: (taskId: string, feedback: string) => Promise<void>;
	onOpenApprovals: () => void;
};

export function ThreadStoryDrawer({
	card,
	loopState,
	activity,
	onClose,
	onRequestChanges,
	onOpenApprovals,
}: ThreadStoryDrawerProps) {
	const { t } = useI18n();
	const title = card
		? t('app.threads.story.drawerTitle', 'User story #{index}').replace(
				'{index}',
				String(card.index),
			)
		: '';
	return (
		<Drawer label={title} open={card !== null} onClose={onClose} className="thread-story-drawer">
			{card ? (
				<ThreadStoryDetail
					key={card.storyId}
					card={card}
					loopState={loopState}
					activity={activity}
					onRequestChanges={onRequestChanges}
					onOpenApprovals={onOpenApprovals}
				/>
			) : null}
		</Drawer>
	);
}

function ThreadStoryDetail({
	card,
	loopState,
	activity,
	onRequestChanges,
	onOpenApprovals,
}: Omit<ThreadStoryDrawerProps, 'card' | 'onClose'> & { card: ThreadBoardCard }) {
	const { t } = useI18n();
	const column = BOARD_COLUMN_META[card.column];
	const assignee = useAssigneeLine(card);
	const criteria =
		card.criteria && card.criteria.length > 0
			? card.criteria
			: card.acceptanceCriteria.map((text) => ({ id: null, text, status: 'pending', met: false }));
	const metCount = criteria.filter((criterion) => criterion.met).length;
	const title = card.synthetic ? t('app.threads.board.generalTasks', 'General tasks') : card.title;
	return (
		<div className="thread-story-body">
			<header className="thread-story-head">
				<h3 className="thread-story-title">{title}</h3>
				<div className="thread-story-chips">
					<StatusChip tone={card.blocked ? 'danger' : column.tone}>
						{card.blocked
							? t('app.threads.board.stage.blocked', 'Blocked')
							: t(column.labelKey, column.fallback)}
					</StatusChip>
					{card.qaVerdict ? (
						<StatusChip tone={toneForStatus(card.qaVerdict)}>
							{t('app.threads.board.qaVerdict', 'QA {verdict}').replace(
								'{verdict}',
								card.qaVerdict.replace(/_/g, ' '),
							)}
						</StatusChip>
					) : null}
					<span className="thread-story-priority mono">{card.priority}</span>
				</div>
				<ProgressMeter
					percent={card.progressPercent ?? 0}
					label={t('app.threads.board.storyProgress', 'Story progress')}
				/>
			</header>

			<section className="thread-story-section" aria-labelledby="thread-story-assignee">
				<h4 id="thread-story-assignee" className="thread-story-section-title">
					{t('app.threads.story.assignee', 'Who is working it')}
				</h4>
				<p className="thread-story-assignee">
					<StatusDot tone={assignee.active ? 'info' : 'pending'} />
					<span>
						{assignee.active ? (
							<strong>{t('app.threads.board.workingNow', 'Working now')} · </strong>
						) : null}
						{assignee.text}
					</span>
				</p>
			</section>

			{card.asA || card.iWant || card.soThat || card.description ? (
				<section className="thread-story-section" aria-labelledby="thread-story-description">
					<h4 id="thread-story-description" className="thread-story-section-title">
						{t('app.threads.story.description', 'Description')}
					</h4>
					{card.asA || card.iWant || card.soThat ? (
						<p className="thread-story-text">
							{t('app.threads.board.storyLine', 'As {asA}, I want {iWant} so that {soThat}')
								.replace('{asA}', card.asA)
								.replace('{iWant}', card.iWant)
								.replace('{soThat}', card.soThat)}
						</p>
					) : null}
					{card.description ? <p className="thread-story-text">{card.description}</p> : null}
				</section>
			) : null}

			{card.blocked ? (
				<p className="thread-story-blocked" role="status">
					{card.blockedReason ||
						t(
							'app.threads.board.blockedFallback',
							'Blocked: QA or the runtime could not finish this story.',
						)}
				</p>
			) : null}

			<section className="thread-story-section" aria-labelledby="thread-story-criteria">
				<h4 id="thread-story-criteria" className="thread-story-section-title">
					{t('app.threads.board.criteria', 'Acceptance criteria ({count})').replace(
						'{count}',
						`${metCount}/${criteria.length}`,
					)}
				</h4>
				{criteria.length ? (
					<ul className="thread-story-criteria">
						{criteria.map((criterion, index) => (
							<li
								key={criterion.id ?? `${index}-${criterion.text}`}
								className="thread-story-criterion"
								data-met={criterion.met ? 'true' : undefined}
							>
								{criterion.met ? (
									<CheckCircle2 aria-hidden="true" size={15} />
								) : (
									<Circle aria-hidden="true" size={15} />
								)}
								<span className="thread-story-criterion-text">{criterion.text}</span>
								<span className="sr-only">
									{criterion.met
										? t('app.threads.story.criterionMet', 'Met')
										: t('app.threads.story.criterionPending', 'Pending')}
								</span>
							</li>
						))}
					</ul>
				) : (
					<p className="thread-story-muted">
						{t('app.threads.story.noCriteria', 'This story has no acceptance criteria.')}
					</p>
				)}
				<p className="thread-story-muted">
					{t(
						'app.threads.story.criteriaHelp',
						'A criterion counts as met once the story passes its QA gate.',
					)}
				</p>
			</section>

			<section className="thread-story-section" aria-labelledby="thread-story-evidence">
				<h4 id="thread-story-evidence" className="thread-story-section-title">
					{t('app.threads.story.evidence', 'Evidence')}
				</h4>
				<dl className="thread-story-facts">
					<dt className="thread-story-fact-label">{t('app.threads.story.commit', 'Commit')}</dt>
					<dd className="thread-story-fact-value">
						{card.commit ? (
							<span className="thread-story-commit mono">
								<GitCommit aria-hidden="true" size={14} />
								{card.commit.slice(0, 12)}
							</span>
						) : (
							t('app.threads.story.noCommit', 'No commit yet')
						)}
					</dd>
					<dt className="thread-story-fact-label">
						{t('app.threads.story.runs', 'Developer runs')}
					</dt>
					<dd className="thread-story-fact-value tnum">{card.runs ?? 0}</dd>
				</dl>
			</section>

			{card.tasks.length ? (
				<section className="thread-story-section" aria-labelledby="thread-story-tasks">
					<h4 id="thread-story-tasks" className="thread-story-section-title">
						{t('app.threads.board.tasks', 'Tasks')}
					</h4>
					<ul className="thread-board-tasks">
						{card.tasks.map((task) => (
							<li className="thread-board-task" key={task.id}>
								<span className="thread-board-task-title">{task.title}</span>
								<span className="thread-board-task-role mono">{task.role}</span>
								<StatusChip tone={toneForStatus(task.status)}>
									{task.status.replace(/_/g, ' ')}
								</StatusChip>
							</li>
						))}
					</ul>
				</section>
			) : null}

			<section className="thread-story-section" aria-labelledby="thread-story-activity">
				<h4 id="thread-story-activity" className="thread-story-section-title">
					{t('app.threads.story.activity', 'Activity')}
				</h4>
				{activity.length ? (
					<ol className="thread-story-timeline">
						{activity.map((event) => (
							<StoryActivityItem key={event.id} event={event} />
						))}
					</ol>
				) : (
					<p className="thread-story-muted">
						{t('app.threads.story.noActivity', 'No agent activity recorded for this story yet.')}
					</p>
				)}
			</section>

			<StoryReview
				card={card}
				loopState={loopState}
				onRequestChanges={onRequestChanges}
				onOpenApprovals={onOpenApprovals}
			/>
		</div>
	);
}

function StoryActivityItem({ event }: { event: ThreadAgentEvent }) {
	const { t } = useI18n();
	const payload = (event.payload ?? {}) as { status?: unknown; outcome?: unknown };
	const status = typeof payload.status === 'string' ? payload.status : '';
	const copy = event.type === 'story_progress' ? STORY_STATUS_COPY[status] : undefined;
	const label = copy
		? t(copy.key, copy.fallback)
		: event.type === 'story_progress'
			? status.replace(/_/g, ' ')
			: event.type.replace(/_/g, ' ');
	return (
		<li className="thread-story-event" data-status={status || undefined}>
			<StatusDot tone={status ? toneForStory(status) : 'info'} />
			<span className="thread-story-event-label">{label}</span>
			{event.agentRole ? (
				<span className="thread-story-event-role mono">{event.agentRole}</span>
			) : null}
			<time className="thread-story-event-time" dateTime={event.createdAt}>
				{formatTime(event.createdAt)}
			</time>
		</li>
	);
}

function toneForStory(status: string) {
	if (status === 'done') return 'ok' as const;
	if (status === 'blocked') return 'danger' as const;
	if (status === 'qa') return 'warn' as const;
	if (status === 'in_progress') return 'info' as const;
	return 'pending' as const;
}

/** Request-changes form when the loop accepts it; otherwise a read-only note with the reason. */
function StoryReview({
	card,
	loopState,
	onRequestChanges,
	onOpenApprovals,
}: {
	card: ThreadBoardCard;
	loopState: string | null;
	onRequestChanges: (taskId: string, feedback: string) => Promise<void>;
	onOpenApprovals: () => void;
}) {
	const { t } = useI18n();
	const defaultTask = reworkTargetTask(card);
	const [taskId, setTaskId] = useState(defaultTask?.id ?? '');
	const [feedback, setFeedback] = useState('');
	const [busy, setBusy] = useState(false);
	const [result, setResult] = useState<'sent' | 'failed' | null>(null);
	const reviewable = Boolean(loopState && REVIEWABLE_LOOP_STATES.has(loopState) && defaultTask);

	const submit = async (event: FormEvent) => {
		event.preventDefault();
		if (!feedback.trim() || !taskId) return;
		setBusy(true);
		setResult(null);
		try {
			await onRequestChanges(taskId, feedback.trim());
			setFeedback('');
			setResult('sent');
		} catch {
			setResult('failed');
		} finally {
			setBusy(false);
		}
	};

	return (
		<section className="thread-story-section" aria-labelledby="thread-story-review">
			<h4 id="thread-story-review" className="thread-story-section-title">
				{t('app.threads.story.review', 'Review')}
			</h4>
			{reviewable ? (
				<form className="thread-story-review" onSubmit={submit}>
					{card.tasks.length > 1 ? (
						<SelectField
							label={t('app.threads.story.reviewTask', 'Task to rework')}
							value={taskId}
							onChange={(event) => setTaskId(event.target.value)}
						>
							{card.tasks.map((task) => (
								<option key={task.id} value={task.id}>
									{task.title}
								</option>
							))}
						</SelectField>
					) : null}
					<TextArea
						label={t('app.threads.story.reviewFeedback', 'What should change')}
						value={feedback}
						rows={3}
						required
						onChange={(event) => setFeedback(event.target.value)}
					/>
					<div className="thread-story-review-actions">
						<Button type="submit" variant="primary" loading={busy} disabled={!feedback.trim()}>
							{t('app.threads.story.requestChanges', 'Request changes')}
						</Button>
						<Button type="button" onClick={onOpenApprovals}>
							{t('app.threads.story.openApprovals', 'Approve delivery in Approvals')}
						</Button>
					</div>
					{result === 'sent' ? (
						<p className="thread-story-muted" role="status">
							{t(
								'app.threads.story.changesSent',
								'Changes requested: the loop reworks this story next.',
							)}
						</p>
					) : null}
					{result === 'failed' ? (
						<p className="thread-story-error" role="alert">
							{t(
								'app.threads.story.changesFailed',
								'Could not request changes. The loop may have moved on; try again.',
							)}
						</p>
					) : null}
				</form>
			) : (
				<p className="thread-story-muted">
					{t(
						'app.threads.story.reviewReadOnly',
						'Read-only while the loop works. You can request changes on a story once the loop awaits review or approval; the delivery is approved as a whole in Approvals.',
					)}
				</p>
			)}
		</section>
	);
}
