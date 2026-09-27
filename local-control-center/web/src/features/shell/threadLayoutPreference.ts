/**
 * Per-thread memory of the operator's layout choice in a live thread: the Chat | Board mode they
 * picked by hand and whether they folded the chat column while the board leads. Stored as one
 * versioned JSON map in `localStorage` keyed by thread id; every read and write is guarded, so a
 * private window or blocked storage just falls back to the automatic layout.
 * @author Rodrigo Mason
 */
import type { BoardMode } from './threadBoardModel';

export type ThreadLayoutPreference = {
	/** Mode the operator chose with the toggle; absent means "follow the loop stage". */
	mode?: BoardMode;
	/** Chat column folded into its dock while the board takes the main area. */
	chatCollapsed?: boolean;
};

const STORAGE_KEY = 'aido:threads:layout:v1';
/** Oldest entries are dropped past this size so the map cannot grow without bound. */
const MAX_THREADS = 200;

function readAll(): Record<string, ThreadLayoutPreference> {
	try {
		const raw = window.localStorage.getItem(STORAGE_KEY);
		const parsed = raw ? JSON.parse(raw) : null;
		return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
			? (parsed as Record<string, ThreadLayoutPreference>)
			: {};
	} catch {
		return {};
	}
}

/** The saved preference for one thread (empty when none, or storage is unavailable). */
export function readThreadLayout(threadId: string): ThreadLayoutPreference {
	if (!threadId) return {};
	const entry = readAll()[threadId];
	if (!entry || typeof entry !== 'object') return {};
	return {
		mode: entry.mode === 'chat' || entry.mode === 'board' ? entry.mode : undefined,
		chatCollapsed: entry.chatCollapsed === true,
	};
}

/** Merges `patch` into the thread's saved preference; the newest thread moves to the end. */
export function writeThreadLayout(threadId: string, patch: ThreadLayoutPreference): void {
	if (!threadId) return;
	try {
		const all = readAll();
		const next = { ...(all[threadId] ?? {}), ...patch };
		delete all[threadId];
		all[threadId] = next;
		const ids = Object.keys(all);
		for (const staleId of ids.slice(0, Math.max(0, ids.length - MAX_THREADS))) delete all[staleId];
		window.localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
	} catch {}
}
