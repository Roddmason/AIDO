/**
 * Loads the runtime-team candidates for a project plus the backend's automatic split for the
 * current selection. Only the latest request writes state: a changed selection or a reload
 * aborts the stale request, and a reload always queries the current selection.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { getRuntimeTeamCandidates, type RuntimeTeamCandidatesResponse } from '../../api/client';

export function useRuntimeTeamCandidates(
	projectId: string,
	selected: readonly string[] | null,
	enabled: boolean,
) {
	const [data, setData] = useState<RuntimeTeamCandidatesResponse | null>(null);
	const [failed, setFailed] = useState(false);
	const selectedKey = selected === null ? null : selected.join(',');
	const query = useRef({ projectId, selectedKey });
	const latestRequest = useRef<AbortController | null>(null);

	const loadLatest = useCallback(async () => {
		latestRequest.current?.abort();
		const controller = new AbortController();
		latestRequest.current = controller;
		const { projectId: currentProjectId, selectedKey: currentKey } = query.current;
		const selection = currentKey === null ? null : currentKey.split(',').filter(Boolean);
		try {
			const response = await getRuntimeTeamCandidates(
				currentProjectId,
				selection,
				controller.signal,
			);
			if (!controller.signal.aborted) {
				setData(response);
				setFailed(false);
			}
		} catch {
			if (!controller.signal.aborted) setFailed(true);
		}
	}, []);

	useEffect(() => {
		query.current = { projectId, selectedKey };
		if (!enabled) return undefined;
		void loadLatest();
		return () => latestRequest.current?.abort();
	}, [enabled, projectId, selectedKey, loadLatest]);

	const reload = useCallback(() => {
		void loadLatest();
	}, [loadLatest]);

	return { data, failed, reload };
}
