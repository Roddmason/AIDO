/**
 * Pure value helpers shared by the Thread Inspector tabs: untyped-metadata readers, date and label formatting, status tone mapping and selection of the loop that belongs to a thread.
 * @author Rodrigo Mason
 */

import type { ProjectProductLoopResponse } from '../../../api/client';
import type { ThreadAgentEvent } from '../../../api/types';
import type { StatusTone } from '../../../components/ui';

/** Product-loop snapshot the inspector reads loops, stories and tasks from. */
export type LoopData = ProjectProductLoopResponse;
/** One loop of the project-wide loop snapshot. */
export type LoopRecord = LoopData['loops'][number];

export function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: {};
}

export function textValue(value: unknown): string | undefined {
	return typeof value === 'string' && value.trim() ? value : undefined;
}

export function arrayValue(value: unknown): unknown[] {
	return Array.isArray(value) ? value : [];
}

const dateFormatters = new Map<string, Intl.DateTimeFormat>();

export function formatWhen(value: string | null | undefined, language: string): string {
	if (!value) return '—';
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return value;
	const locale = language || 'en';
	let formatter = dateFormatters.get(locale);
	if (!formatter) {
		formatter = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' });
		dateFormatters.set(locale, formatter);
	}
	return formatter.format(date);
}

export function humanize(value: string): string {
	return value.replace(/_/g, ' ');
}

export function loopStateTone(state: string): StatusTone {
	if (state === 'delivered') return 'ok';
	if (state === 'blocked' || state === 'cancelled') return 'danger';
	if (
		state.startsWith('awaiting') ||
		state === 'goal_received' ||
		state === 'discovering' ||
		state === 'discovery' ||
		state === 'planning'
	) {
		return 'pending';
	}
	return 'warn';
}

export function trustTone(level: string): StatusTone {
	if (
		level === 'official_documentation' ||
		level === 'official_repository' ||
		level === 'standard_rfc'
	) {
		return 'ok';
	}
	if (level === 'untrusted') return 'danger';
	return 'info';
}

export function activeLoopOf(data: LoopData): LoopRecord | null {
	const sorted = [...data.loops].sort((left, right) => (left.updatedAt < right.updatedAt ? 1 : -1));
	return sorted.find((loop) => loop.status === 'active') ?? sorted[0] ?? null;
}

/**
 * The thread's own loop: the newest one its events reference. The loop resource is project-wide, so
 * the most recently updated loop could belong to another thread, or be an old loop of this thread
 * closed after its delivery (seen live: "cancelled" on a delivered thread). Falls back to the
 * project-wide choice while the thread has no loop events yet.
 */
export function threadLoopOf(
	data: LoopData,
	events: ReadonlyArray<ThreadAgentEvent> | undefined,
): LoopRecord | null {
	const loops = new Map(data.loops.map((loop) => [loop.id, loop]));
	const newestFirst = [...(events ?? [])].sort((left, right) => right.sequence - left.sequence);
	for (const event of newestFirst) {
		const loopId = event.payload?.loopId;
		if (typeof loopId === 'string' && loops.has(loopId)) return loops.get(loopId) ?? null;
	}
	return activeLoopOf(data);
}
