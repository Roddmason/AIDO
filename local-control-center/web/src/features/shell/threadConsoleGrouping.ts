/**
 * Groups consecutive console entries that describe the SAME underlying failure.
 *
 * A single product-loop block leaves 3-4 separate records milliseconds apart: the generic
 * `state_changed` event, one or more `blocked` events with progressively more detail (stage,
 * evidence, jobId), and the worker's `kind=error` message — all sharing the same `loopId` and the
 * same `reason` text. Rendered as-is, the console shows one real failure as three or four rows.
 * This groups them into a single failure row (motive once, chips merged, an unobtrusive count of
 * how many records it folds) while keeping every original record reachable from the existing
 * per-row technical-details disclosure. A different reason or a different loop always stays a
 * separate row: this never hides a distinct failure.
 *
 * Shared by ThreadExecutionPanel (events + messages) and BottomPanel (events only), so both render
 * the same grouping for the same underlying data.
 * @author Rodrigo Mason
 */

import type { ConsoleEntry } from './ThreadExecutionPanel';
import { safeRecord, textValue } from './threadPresentation';

/** Records further apart than this are treated as unrelated even if the reason text matches. */
const FAILURE_GROUP_WINDOW_MS = 2000;

export type FailureGroupEntry = {
	kind: 'failure-group';
	key: string;
	createdAt: string;
	sequence: number;
	reason: string;
	loopId: string | null;
	chips: string[];
	entries: ConsoleEntry[];
};

export type GroupedConsoleEntry = ConsoleEntry | FailureGroupEntry;

export function isFailureGroup(entry: GroupedConsoleEntry): entry is FailureGroupEntry {
	return entry.kind === 'failure-group';
}

/** The payload/metadata to read chips and the reason from, or null when the entry is not a failure. */
function failurePayload(entry: ConsoleEntry): Record<string, unknown> | null {
	if (entry.kind === 'message') {
		return entry.message.kind === 'error' ? safeRecord(entry.message.metadata) : null;
	}
	const { type, payload } = entry.event;
	const record = safeRecord(payload);
	const isBlockedTransition = type === 'state_changed' && textValue(record.toState) === 'blocked';
	return type === 'blocked' || isBlockedTransition ? record : null;
}

function failureReason(entry: ConsoleEntry, payload: Record<string, unknown>): string {
	const reason = entry.kind === 'message' ? entry.message.content : textValue(payload.reason);
	return (reason ?? '').trim();
}

function chipsFromPayload(payload: Record<string, unknown>): string[] {
	return [
		textValue(payload.status),
		textValue(payload.jobId),
		textValue(payload.evidencePackageId),
	].filter((value): value is string => Boolean(value));
}

function withinFailureWindow(startIso: string, currentIso: string): boolean {
	const start = Date.parse(startIso);
	const current = Date.parse(currentIso);
	if (Number.isNaN(start) || Number.isNaN(current)) return false;
	return Math.abs(current - start) <= FAILURE_GROUP_WINDOW_MS;
}

function mergeChips(into: string[], from: string[]): string[] {
	const merged = [...into];
	for (const chip of from) {
		if (!merged.includes(chip)) merged.push(chip);
	}
	return merged;
}

/**
 * Folds consecutive failure records (same loop, same reason, within {@link FAILURE_GROUP_WINDOW_MS})
 * into one `failure-group` entry. `entries` must already be in chronological order (as produced by
 * `mergeConsoleEntries`). Never merges across a non-failure entry or a differing reason/loop: two
 * genuinely different failures always stay two rows.
 */
export function groupConsoleFailures(entries: ConsoleEntry[]): GroupedConsoleEntry[] {
	const grouped: GroupedConsoleEntry[] = [];
	let current: FailureGroupEntry | null = null;

	for (const entry of entries) {
		const payload = failurePayload(entry);
		const reason = payload ? failureReason(entry, payload) : '';
		if (payload && reason) {
			const loopId = textValue(payload.loopId) ?? null;
			if (
				current &&
				current.reason === reason &&
				current.loopId === loopId &&
				withinFailureWindow(current.createdAt, entry.createdAt)
			) {
				current.entries.push(entry);
				current.chips = mergeChips(current.chips, chipsFromPayload(payload));
				continue;
			}
			current = {
				kind: 'failure-group',
				key: `failure-${entry.key}`,
				createdAt: entry.createdAt,
				sequence: entry.sequence,
				reason,
				loopId,
				chips: chipsFromPayload(payload),
				entries: [entry],
			};
			grouped.push(current);
			continue;
		}
		current = null;
		grouped.push(entry);
	}

	return grouped;
}
