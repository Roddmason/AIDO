/**
 * Reorderable workbench columns: persisted order with safe fallbacks.
 * @author AIDO contributors
 */
import { useCallback, useState } from 'react';

export type WorkbenchColumnId = 'explorer' | 'conversation' | 'status';

export const DEFAULT_COLUMN_ORDER: readonly WorkbenchColumnId[] = [
	'explorer',
	'conversation',
	'status',
];
export const COLUMN_ORDER_STORAGE_KEY = 'aido:workbench-columns:v1';

/** Accepts only a permutation of the known columns; anything else falls back to the default. */
function parseOrder(raw: string | null): WorkbenchColumnId[] {
	try {
		const parsed: unknown = raw ? JSON.parse(raw) : null;
		if (
			Array.isArray(parsed) &&
			parsed.length === DEFAULT_COLUMN_ORDER.length &&
			DEFAULT_COLUMN_ORDER.every((id) => parsed.includes(id))
		) {
			return parsed as WorkbenchColumnId[];
		}
	} catch {
		// Corrupt preference: use the default order.
	}
	return [...DEFAULT_COLUMN_ORDER];
}

function readOrder(): WorkbenchColumnId[] {
	try {
		return parseOrder(window.localStorage.getItem(COLUMN_ORDER_STORAGE_KEY));
	} catch {
		return [...DEFAULT_COLUMN_ORDER];
	}
}

export function useColumnOrder() {
	const [order, setOrder] = useState<WorkbenchColumnId[]>(readOrder);

	/** Moves a column one step; returns its new 1-based position, or null at the edge. */
	const move = useCallback(
		(id: WorkbenchColumnId, direction: -1 | 1): number | null => {
			const from = order.indexOf(id);
			const to = from + direction;
			if (from < 0 || to < 0 || to >= order.length) return null;
			const next = [...order];
			next.splice(from, 1);
			next.splice(to, 0, id);
			setOrder(next);
			try {
				window.localStorage.setItem(COLUMN_ORDER_STORAGE_KEY, JSON.stringify(next));
			} catch {
				// Preference is best effort; the order still applies for this session.
			}
			return to + 1;
		},
		[order],
	);

	return { order, move };
}
