/**
 * Per-story execution board of a thread (To do · In progress · QA · Done), fed by
 * `GET /threads/{id}/board`. The product loop moves the cards — there is no drag and drop — so the
 * board is a read-only projection: a stage strip (planning / executing / security / approval /
 * delivered / blocked) with the done-of-total progress, and four lanes with fixed headers and their
 * own scroll, one card per user story with its value statement, criteria and agent tasks.
 * @author Rodrigo Mason
 */
import { AlertTriangle } from 'lucide-react';
import { m } from 'motion/react';

import type { ThreadBoardCard, ThreadBoardResponse } from '../../api/client';
import { EmptyState, StatusChip, StatusDot } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { crossfade } from '../../motion/variants';
import { BOARD_COLUMN_META, BOARD_STAGE_META } from './threadBoardModel';

export type ThreadBoardProps = {
	board: ThreadBoardResponse;
	/** Last refetch error; the previous board stays on screen while it is shown. */
	error: string;
};

/** The thread's story board: stage strip plus the four status lanes. */
export function ThreadBoard({ board, error }: ThreadBoardProps) {
	const { t } = useI18n();
	const stage = BOARD_STAGE_META[board.stage];
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
				<StatusChip tone={stage.tone}>{t(stage.labelKey, stage.fallback)}</StatusChip>
				<span className="thread-board-progress">
					{t('app.threads.board.progress', '{done} of {total} stories done')
						.replace('{done}', String(board.progress.done))
						.replace('{total}', String(board.progress.total))}
				</span>
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
										<ThreadBoardStoryCard key={card.storyId} card={card} />
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

/** One story card: title, value statement, blocker, outcome chip, criteria and agent tasks. */
function ThreadBoardStoryCard({ card }: { card: ThreadBoardCard }) {
	const { t } = useI18n();
	const title = card.synthetic ? t('app.threads.board.generalTasks', 'General tasks') : card.title;
	return (
		<m.article
			className="thread-board-card"
			data-story-id={card.storyId}
			data-blocked={card.blocked ? 'true' : undefined}
			layout="position"
			layoutId={`thread-board-${card.storyId}`}
		>
			<div className="thread-board-card-head">
				<span className="thread-board-card-index mono">#{card.index}</span>
				<h4 className="thread-board-card-title">{title}</h4>
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
			{card.acceptanceCriteria.length ? (
				<details className="thread-board-criteria">
					<summary>
						{t('app.threads.board.criteria', 'Acceptance criteria ({count})').replace(
							'{count}',
							String(card.acceptanceCriteria.length),
						)}
					</summary>
					<ul>
						{card.acceptanceCriteria.map((criterion) => (
							<li key={criterion}>{criterion}</li>
						))}
					</ul>
				</details>
			) : null}
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
			{card.runtime ? <span className="thread-board-card-runtime mono">{card.runtime}</span> : null}
		</m.article>
	);
}
