/**
 * Per-story execution board of a thread (To do · In progress · QA · Done), fed by
 * `GET /threads/{id}/board`. The product loop moves the cards — there is no drag and drop — so the
 * board is a projection of the backlog: a header with the stage, the done-of-total count and the
 * overall progress meter, and four lanes with fixed headers and their own scroll. Each user story
 * (HU) card shows who is working it (loop role, runtime and model, live while the loop runs it), its
 * progress, criteria and QA badges and its agent tasks; the whole card opens the story drawer.
 * @author Rodrigo Mason
 */
import { AlertTriangle } from 'lucide-react';
import { m } from 'motion/react';

import type { ThreadBoardCard, ThreadBoardResponse } from '../../api/client';
import { EmptyState, StatusChip, StatusDot } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { crossfade } from '../../motion/variants';
import {
	ASSIGNEE_ROLE_COPY,
	BOARD_COLUMN_META,
	BOARD_STAGE_META,
	type BoardStage,
} from './threadBoardModel';

export type ThreadBoardProps = {
	board: ThreadBoardResponse;
	/** Last refetch error; the previous board stays on screen while it is shown. */
	error: string;
	/** Opens the story drawer for a card. */
	onOpenStory: (storyId: string) => void;
};

/** The thread's story board: header with stage and progress, plus the four status lanes. */
export function ThreadBoard({ board, error, onOpenStory }: ThreadBoardProps) {
	const { t } = useI18n();
	const stage = BOARD_STAGE_META[board.stage];
	const percent = board.progress.percent ?? 0;
	return (
		<m.section
			className="thread-board"
			aria-label={t('app.threads.board.region', 'Story board')}
			layout
			variants={crossfade}
			initial="initial"
			animate="animate"
		>
			<header className="thread-board-stage">
				<h2 className="thread-board-heading">{t('app.threads.board.heading', 'User stories')}</h2>
				<StatusChip tone={stage.tone}>{t(stage.labelKey, stage.fallback)}</StatusChip>
				<span className="thread-board-progress">
					{t('app.threads.board.progress', '{done} of {total} stories done')
						.replace('{done}', String(board.progress.done))
						.replace('{total}', String(board.progress.total))}
				</span>
				<ProgressMeter
					className="thread-board-overall"
					percent={percent}
					label={t('app.threads.board.overallProgress', 'Backlog progress')}
				/>
				{error ? (
					<span className="thread-board-error" role="status">
						{error}
					</span>
				) : null}
			</header>
			<div className="thread-board-columns">
				{board.columns.map((column) => {
					const meta = BOARD_COLUMN_META[column.id];
					const headerId = `thread-board-col-${column.id}`;
					return (
						<section
							className="thread-board-column"
							data-column={column.id}
							aria-labelledby={headerId}
							key={column.id}
						>
							<header className="thread-board-column-header">
								<StatusDot tone={meta.tone} />
								<h3 id={headerId} className="thread-board-column-title">
									{t(meta.labelKey, meta.fallback)}
								</h3>
								<span className="thread-board-column-count">
									<StatusChip tone={column.cards.length ? meta.tone : undefined}>
										{column.cards.length}
									</StatusChip>
								</span>
							</header>
							<div className="thread-board-column-scroll">
								{column.cards.length === 0 ? (
									<EmptyState title={t(meta.emptyKey, meta.emptyFallback)} body="" />
								) : (
									column.cards.map((card) => (
										<ThreadBoardStoryCard
											key={card.storyId}
											card={card}
											stage={board.stage}
											onOpen={() => onOpenStory(card.storyId)}
										/>
									))
								)}
							</div>
						</section>
					);
				})}
			</div>
		</m.section>
	);
}

/** Horizontal progress bar with an accessible `progressbar` role and a visible percentage. */
export function ProgressMeter({
	percent,
	label,
	className,
}: {
	percent: number;
	label: string;
	className?: string;
}) {
	const { t } = useI18n();
	const clamped = Math.min(100, Math.max(0, Math.round(percent)));
	return (
		<span className={className ? `thread-progress ${className}` : 'thread-progress'}>
			<span
				className="thread-progress-track"
				role="progressbar"
				aria-label={label}
				aria-valuemin={0}
				aria-valuemax={100}
				aria-valuenow={clamped}
				aria-valuetext={t('app.threads.board.percentComplete', '{percent}% complete').replace(
					'{percent}',
					String(clamped),
				)}
			>
				<span className="thread-progress-fill" style={{ inlineSize: `${clamped}%` }} />
			</span>
			<span className="thread-progress-value tnum">{clamped}%</span>
		</span>
	);
}

/** Who works the story, as one line: role label, runtime and model (or the planned roles). */
export function useAssigneeLine(card: ThreadBoardCard): { text: string; active: boolean } {
	const { t } = useI18n();
	const assignee = card.assignee;
	if (!assignee?.role) {
		const planned = assignee?.plannedRoles?.length
			? assignee.plannedRoles
			: card.tasks.map((task) => task.role).filter(Boolean);
		return {
			text: planned.length
				? t('app.threads.board.assigneePlanned', 'Unassigned · planned: {roles}').replace(
						'{roles}',
						Array.from(new Set(planned)).join(', '),
					)
				: t('app.threads.board.assigneeNone', 'Unassigned'),
			active: false,
		};
	}
	const copy = ASSIGNEE_ROLE_COPY[assignee.role];
	const role = copy ? t(copy.key, copy.fallback) : assignee.role;
	const parts = [role, assignee.runtime ?? card.runtime, assignee.model].filter(Boolean);
	return { text: parts.join(' · '), active: Boolean(assignee.active) };
}

/** One story card: the title button opens the drawer and stretches over the whole card. */
function ThreadBoardStoryCard({
	card,
	stage,
	onOpen,
}: {
	card: ThreadBoardCard;
	stage: BoardStage;
	onOpen: () => void;
}) {
	const { t } = useI18n();
	const title = card.synthetic ? t('app.threads.board.generalTasks', 'General tasks') : card.title;
	const assignee = useAssigneeLine(card);
	const criteria = card.criteria ?? [];
	const criteriaCount = criteria.length || card.acceptanceCriteria.length;
	const metCount = criteria.filter((criterion) => criterion.met).length;
	return (
		<m.article
			className="thread-board-card"
			data-story-id={card.storyId}
			data-blocked={card.blocked ? 'true' : undefined}
			data-active={assignee.active ? 'true' : undefined}
			layout="position"
			layoutId={`thread-board-${card.storyId}`}
		>
			<div className="thread-board-card-head">
				<span className="thread-board-card-index mono">#{card.index}</span>
				<h4 className="thread-board-card-title">
					<button
						type="button"
						className="thread-board-card-open"
						aria-haspopup="dialog"
						onClick={onOpen}
					>
						{title}
					</button>
				</h4>
			</div>
			{card.asA || card.iWant || card.soThat ? (
				<p className="thread-board-card-story">
					{t('app.threads.board.storyLine', 'As {asA}, I want {iWant} so that {soThat}')
						.replace('{asA}', card.asA)
						.replace('{iWant}', card.iWant)
						.replace('{soThat}', card.soThat)}
				</p>
			) : null}
			{card.blocked ? (
				<p className="thread-board-card-blocked" role="status">
					<AlertTriangle aria-hidden="true" size={13} />
					{card.blockedReason ||
						t(
							'app.threads.board.blockedFallback',
							'Blocked: QA or the runtime could not finish this story.',
						)}
				</p>
			) : null}
			<p className="thread-board-card-assignee">
				<StatusDot tone={assignee.active ? 'info' : 'pending'} />
				<span className="thread-board-card-assignee-text">
					{assignee.active ? (
						<span className="thread-board-card-live">
							{t('app.threads.board.workingNow', 'Working now')}
						</span>
					) : null}
					{assignee.text}
				</span>
			</p>
			<ProgressMeter
				percent={card.progressPercent ?? 0}
				label={t('app.threads.board.storyProgress', 'Story progress')}
			/>
			<div className="thread-board-card-badges">
				{criteriaCount ? (
					<StatusChip tone={criteria.length && metCount === criteria.length ? 'ok' : undefined}>
						{t('app.threads.board.criteriaMet', '{met}/{count} criteria')
							.replace('{met}', String(metCount))
							.replace('{count}', String(criteriaCount))}
					</StatusChip>
				) : null}
				{card.qaVerdict ? (
					<StatusChip tone={toneForStatus(card.qaVerdict)}>
						{t('app.threads.board.qaVerdict', 'QA {verdict}').replace(
							'{verdict}',
							card.qaVerdict.replace(/_/g, ' '),
						)}
					</StatusChip>
				) : null}
				{card.column === 'done' && stage === 'approval' ? (
					<StatusChip tone="warn">
						{t('app.threads.board.awaitingReview', 'Awaiting your review')}
					</StatusChip>
				) : null}
				{card.outcome === 'noop' ? (
					<StatusChip tone="info">
						{t('app.threads.board.outcomeNoop', 'No changes needed')}
					</StatusChip>
				) : null}
				{card.outcome === 'carried_over' ? (
					<StatusChip tone="ok">
						{t('app.threads.board.outcomeCarried', 'Done in a previous run')}
					</StatusChip>
				) : null}
			</div>
			<ul className="thread-board-tasks" aria-label={t('app.threads.board.tasks', 'Tasks')}>
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
		</m.article>
	);
}
