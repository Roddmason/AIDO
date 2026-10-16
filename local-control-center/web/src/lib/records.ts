/**
 * Newest-wins merging for id-keyed records shown in editable tables. Pages hold local copies of overview
 * rows and fold in both the periodic overview refresh and the result of their own writes; a stale
 * overview snapshot must never overwrite a row that was just saved.
 * @author Rodrigo Mason
 */

type TimestampedRecord = { id: string; updatedAt?: string; createdAt?: string };

/** Millisecond timestamp of a record (`updatedAt`, else `createdAt`); 0 when absent or unparsable. */
export function recordTimestamp(record: { updatedAt?: string; createdAt?: string }) {
	const parsed = Date.parse(String(record.updatedAt ?? record.createdAt ?? ''));
	return Number.isNaN(parsed) ? 0 : parsed;
}

/** Inserts `incoming` first, or replaces the row with the same id unless the existing one is newer. */
export function upsertNewestById<T extends TimestampedRecord>(records: T[], incoming: T) {
	const existing = records.find((record) => record.id === incoming.id);
	if (existing && recordTimestamp(existing) > recordTimestamp(incoming)) return records;
	return existing
		? records.map((record) => (record.id === incoming.id ? incoming : record))
		: [incoming, ...records];
}

/** Folds every `incoming` record into `current` with {@link upsertNewestById}. */
export function mergeNewestById<T extends TimestampedRecord>(current: T[], incoming: T[]) {
	return incoming.reduce((merged, record) => upsertNewestById(merged, record), current);
}
