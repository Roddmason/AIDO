/**
 * Loads the effective global AI team of a project (or the general one with `projectId === null`).
 * Only the latest request writes state; `reload` re-queries after a settings write.
 *
 * A short module cache keeps the last answer per scope: reopening the inspector Team tab or the
 * composer chip shows it at once instead of an empty card, and a fresh entry skips the request.
 * `reload` and `invalidateRuntimeTeamCache` (after a provider switch) always go to the server.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { getRuntimeTeam } from '../../api/client';
import type { RuntimeTeam } from '../../api/types';

const FRESH_MS = 15_000;
const GENERAL_SCOPE = '__general__';
const cache = new Map<string, { data: RuntimeTeam; at: number }>();

/** Drops every cached team: a provider switch or a team write changes all scopes at once. */
export function invalidateRuntimeTeamCache() {
	cache.clear();
}

export function useRuntimeTeam(projectId: string | null, enabled: boolean) {
	const scope = projectId ?? GENERAL_SCOPE;
	const [data, setData] = useState<RuntimeTeam | null>(() => cache.get(scope)?.data ?? null);
	const [failed, setFailed] = useState(false);
	const latestRequest = useRef<AbortController | null>(null);

	const load = useCallback(
		async (force: boolean) => {
			const cached = cache.get(scope);
			if (cached) setData(cached.data);
			if (!force && cached && Date.now() - cached.at < FRESH_MS) return;
			latestRequest.current?.abort();
			const controller = new AbortController();
			latestRequest.current = controller;
			try {
				const response = await getRuntimeTeam(projectId, controller.signal);
				if (!controller.signal.aborted) {
					cache.set(scope, { data: response, at: Date.now() });
					setData(response);
					setFailed(false);
				}
			} catch {
				if (!controller.signal.aborted) setFailed(true);
			}
		},
		[projectId, scope],
	);

	useEffect(() => {
		if (!enabled) return undefined;
		void load(false);
		return () => latestRequest.current?.abort();
	}, [enabled, load]);

	const reload = useCallback(() => {
		invalidateRuntimeTeamCache();
		void load(true);
	}, [load]);

	return { data, failed, reload };
}
