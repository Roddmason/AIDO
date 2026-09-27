/**
 * Loads the effective global AI team of a project (or the general one with `projectId === null`).
 * Only the latest request writes state; `reload` re-queries after a settings write.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { getRuntimeTeam } from '../../api/client';
import type { RuntimeTeam } from '../../api/types';

export function useRuntimeTeam(projectId: string | null, enabled: boolean) {
	const [data, setData] = useState<RuntimeTeam | null>(null);
	const [failed, setFailed] = useState(false);
	const latestRequest = useRef<AbortController | null>(null);

	const load = useCallback(async () => {
		latestRequest.current?.abort();
		const controller = new AbortController();
		latestRequest.current = controller;
		try {
			const response = await getRuntimeTeam(projectId, controller.signal);
			if (!controller.signal.aborted) {
				setData(response);
				setFailed(false);
			}
		} catch {
			if (!controller.signal.aborted) setFailed(true);
		}
	}, [projectId]);

	useEffect(() => {
		if (!enabled) return undefined;
		void load();
		return () => latestRequest.current?.abort();
	}, [enabled, load]);

	const reload = useCallback(() => {
		void load();
	}, [load]);

	return { data, failed, reload };
}
