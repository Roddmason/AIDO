/**
 * Data source for the Thread Inspector.
 *
 * Thread detail and product loop load eagerly (the pinned loop-vitals strip needs both on
 * every tab); the agent roster, memory recall and resolved settings stay lazy per tab. Results cache per key
 * and every resource exposes a uniform `{ data, loading, error, reload }` handle so panels
 * render honest loading/error/empty/success states. Reads only — mutations stay with the
 * composer and the product-loop pages. The in-flight dedup marker is cleared synchronously on
 * cleanup so a quick tab-away/tab-back refetches instead of hanging on a stale marker, and a
 * `reload` keeps the previous data visible while the refetch runs (no full-panel skeleton
 * flash); data resets only when the backing key itself changes.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import {
	type AgentProfilesResponse,
	getAgentProfiles,
	getProjectProductLoop,
	getSettings,
	getThread,
	getThreadMemory,
	type ProjectProductLoopResponse,
	type SettingsResponse,
	type ThreadMemoryRecallResponse,
} from '../../api/client';
import type { ThreadDetail } from '../../api/types';

/** The eight inspector views, in display order. */
export type ThreadInspectorTab =
	| 'goal'
	| 'team'
	| 'plan'
	| 'backlog'
	| 'memory'
	| 'research'
	| 'artifacts'
	| 'settings';

/** Uniform read handle every panel consumes; `reload` refetches after an error. */
export type InspectorResource<T> = {
	data: T | null;
	loading: boolean;
	error: boolean;
	reload: () => void;
};

/**
 * Fetches `load` once per `key`/`attempt` while `active`, aborting on unmount/key change.
 * The dedup marker resets synchronously in the cleanup (the aborted promise settles on a
 * later microtask, too late for a quick tab-away/tab-back). Data is dropped only when `key`
 * changes so a `reload` keeps the previous rows visible during the background refetch.
 */
function useLazyResource<T>(
	key: string | null,
	active: boolean,
	load: (signal: AbortSignal) => Promise<T>,
): InspectorResource<T> {
	const [data, setData] = useState<T | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState(false);
	const [attempt, setAttempt] = useState(0);
	const fetchedRef = useRef<string | null>(null);
	const dataKeyRef = useRef<string | null>(null);

	useEffect(() => {
		if (!active || !key) return;
		const fetchId = `${key}#${attempt}`;
		if (fetchedRef.current === fetchId) return;
		fetchedRef.current = fetchId;
		const controller = new AbortController();
		if (dataKeyRef.current !== key) {
			dataKeyRef.current = key;
			setData(null);
		}
		setLoading(true);
		setError(false);
		load(controller.signal)
			.then((result) => {
				if (controller.signal.aborted) return;
				setData(result);
				setLoading(false);
			})
			.catch(() => {
				if (controller.signal.aborted) return;
				setError(true);
				setLoading(false);
			});
		return () => {
			controller.abort();
			if (fetchedRef.current === fetchId) fetchedRef.current = null;
		};
	}, [active, key, attempt, load]);

	const reload = useCallback(() => {
		fetchedRef.current = null;
		setAttempt((value) => value + 1);
	}, []);

	return { data, loading, error, reload };
}

/**
 * Resolves the five inspector resources. Thread detail and product loop are eager (the
 * pinned vitals strip consumes both on every tab); the roster, memory recall and settings load on
 * their tab's first visit. `threadId` is null on the new-thread intake, which disables the
 * thread-scoped resources (their panels render a guidance empty state instead).
 */
export function useThreadInspectorData(
	projectId: string | null,
	threadId: string | null,
	tab: ThreadInspectorTab,
) {
	const loadDetail = useCallback(
		(signal: AbortSignal) => getThread(threadId ?? '', signal),
		[threadId],
	);
	const detail = useLazyResource<ThreadDetail>(threadId, true, loadDetail);

	const loadLoop = useCallback(
		(signal: AbortSignal) => getProjectProductLoop(projectId ?? '', signal),
		[projectId],
	);
	const loop = useLazyResource<ProjectProductLoopResponse>(projectId, true, loadLoop);

	const loadRoster = useCallback(
		(signal: AbortSignal) => getAgentProfiles(projectId ?? undefined, signal),
		[projectId],
	);
	// The roster comes from the agent-profiles endpoint, not from the overview snapshot: only this
	// endpoint resolves each profile against the runtimes actually detected on this machine and joins
	// the project override. Overview serves the profile rows straight from the repository, so every
	// agent would arrive with the `unknown` availability default and the whole team would read as
	// unconfigured.
	const roster = useLazyResource<AgentProfilesResponse>(projectId, tab === 'team', loadRoster);

	const memoryTitle = detail.data?.thread.title ?? '';
	const loadMemory = useCallback(
		(signal: AbortSignal) => getThreadMemory(threadId ?? '', signal),
		[threadId],
	);
	// The title is part of the key: renaming the thread refetches instead of serving the
	// recall computed for the old title (the backend re-indexes the thread per request).
	const memory = useLazyResource<ThreadMemoryRecallResponse>(
		threadId && memoryTitle ? `${threadId}:${memoryTitle}` : null,
		tab === 'memory',
		loadMemory,
	);

	const loadSettings = useCallback(
		(signal: AbortSignal) => getSettings(projectId ?? '', signal),
		[projectId],
	);
	const settings = useLazyResource<SettingsResponse>(projectId, tab === 'settings', loadSettings);

	return { detail, loop, roster, memory, settings };
}
