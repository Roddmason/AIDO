/**
 * Loads the runtime-team candidates for a project plus the backend's automatic split for the
 * current selection; a changed selection aborts the stale request.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useState } from 'react';

import { getRuntimeTeamCandidates, type RuntimeTeamCandidatesResponse } from '../../api/client';

export function useRuntimeTeamCandidates(
	projectId: string,
	selected: readonly string[] | null,
	enabled: boolean,
) {
	const [data, setData] = useState<RuntimeTeamCandidatesResponse | null>(null);
	const [failed, setFailed] = useState(false);
	const selectedKey = selected === null ? null : selected.join(',');

	const load = useCallback(
		async (signal?: AbortSignal) => {
			const selection = selectedKey === null ? null : selectedKey.split(',').filter(Boolean);
			try {
				const response = await getRuntimeTeamCandidates(projectId, selection, signal);
				if (!signal?.aborted) {
					setData(response);
					setFailed(false);
				}
			} catch {
				if (!signal?.aborted) setFailed(true);
			}
		},
		[projectId, selectedKey],
	);

	useEffect(() => {
		if (!enabled) return undefined;
		const controller = new AbortController();
		void load(controller.signal);
		return () => controller.abort();
	}, [enabled, load]);

	const reload = useCallback(() => {
		void load();
	}, [load]);

	return { data, failed, reload };
}
