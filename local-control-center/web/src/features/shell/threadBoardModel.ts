/**
 * Presentation model of the thread story board: the lane and stage vocabulary (i18n keys, English
 * fallbacks and tones), the thread event types that make the board stale, the view mode of the
 * thread layout (chat-first or board-first), the per-story activity filter and the loop states in
 * which the operator can send a story back for changes.
 * @author Rodrigo Mason
 */
import type { ThreadBoardCard, ThreadBoardResponse } from '../../api/client';
import type { ThreadAgentEvent } from '../../api/types';
import type { StatusTone } from '../../components/ui';

export type BoardMode = 'chat' | 'board';
export type BoardColumnId = ThreadBoardResponse['columns'][number]['id'];
export type BoardStage = ThreadBoardResponse['stage'];

type CopyMeta = { labelKey: string; fallback: string; tone: StatusTone };
type ColumnMeta = CopyMeta & { emptyKey: string; emptyFallback: string };

export const BOARD_COLUMN_META: Record<BoardColumnId, ColumnMeta> = {
	todo: {
		labelKey: 'app.threads.board.column.todo',
		fallback: 'To do',
		tone: 'pending',
		emptyKey: 'app.threads.board.empty.todo',
		emptyFallback: 'Nothing waiting',
	},
	in_progress: {
		labelKey: 'app.threads.board.column.inProgress',
		fallback: 'In progress',
		tone: 'info',
		emptyKey: 'app.threads.board.empty.inProgress',
		emptyFallback: 'No story in progress',
	},
	qa: {
		labelKey: 'app.threads.board.column.qa',
		fallback: 'QA',
		tone: 'warn',
		emptyKey: 'app.threads.board.empty.qa',
		emptyFallback: 'Nothing under QA',
	},
	done: {
		labelKey: 'app.threads.board.column.done',
		fallback: 'Done',
		tone: 'ok',
		emptyKey: 'app.threads.board.empty.done',
		emptyFallback: 'No story done yet',
	},
};

export const BOARD_STAGE_META: Record<BoardStage, CopyMeta> = {
	planning: { labelKey: 'app.threads.board.stage.planning', fallback: 'Planning', tone: 'pending' },
	executing: {
		labelKey: 'app.threads.board.stage.executing',
		fallback: 'Executing stories',
		tone: 'info',
	},
	security: {
		labelKey: 'app.threads.board.stage.security',
		fallback: 'Security review',
		tone: 'warn',
	},
	approval: {
		labelKey: 'app.threads.board.stage.approval',
		fallback: 'Awaiting approval',
		tone: 'warn',
	},
	delivered: { labelKey: 'app.threads.board.stage.delivered', fallback: 'Delivered', tone: 'ok' },
	blocked: { labelKey: 'app.threads.board.stage.blocked', fallback: 'Blocked', tone: 'danger' },
};

/** Thread events after which the board can have moved (story transitions, delivery stages and the
 *  stops — block, cancel, rework — that do not always emit a `story_progress`). */
export const BOARD_REFRESH_EVENT_TYPES: ReadonlySet<string> = new Set([
	'story_progress',
	'executing',
	'reworking',
	'qa_running',
	'security_running',
	'awaiting_approval',
	'delivered',
	'blocked',
	'cancelled',
]);

/** Sequence of the newest board-relevant event; a new value means the board must be refetched. */
export function latestBoardRefreshSequence(events: ThreadAgentEvent[]): number {
	return events.findLast((event) => BOARD_REFRESH_EVENT_TYPES.has(event.type))?.sequence ?? 0;
}

/** Newest-first thread events that belong to one user story (their payload names its `storyId`). */
export function storyActivity(events: ThreadAgentEvent[], storyId: string): ThreadAgentEvent[] {
	return events
		.filter((event) => (event.payload as { storyId?: unknown } | null)?.storyId === storyId)
		.sort((left, right) => right.sequence - left.sequence);
}

/** Loop states whose FSM accepts `request_changes` on a task (the edge to `reworking`); outside them
 *  the story drawer is read-only. `qa_running` is left out: there the QA gate decides the rework. */
export const REVIEWABLE_LOOP_STATES: ReadonlySet<string> = new Set([
	'review_ready',
	'quality_review',
	'awaiting_approval',
	'awaiting_feedback',
]);

/** Task that a story-level "request changes" targets: the first non-QA task, else the first one. */
export function reworkTargetTask(card: ThreadBoardCard): ThreadBoardCard['tasks'][number] | null {
	return (
		card.tasks.find((task) => task.id && !/qa|review/i.test(task.role)) ??
		card.tasks.find((task) => task.id) ??
		null
	);
}

type RoleCopy = { key: string; fallback: string };

/** Human label of the loop role working a story; unknown roles fall back to their id. */
export const ASSIGNEE_ROLE_COPY: Record<string, RoleCopy> = {
	developer: { key: 'app.threads.board.role.developer', fallback: 'DeveloperAgent' },
	qa: { key: 'app.threads.board.role.qa', fallback: 'QA gate' },
};
