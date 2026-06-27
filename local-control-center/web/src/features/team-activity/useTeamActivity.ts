/**
 * Fetches the project-scoped Team Activity board and polls it while the panel is open.
 *
 * Decoupled from the 5s overview poll: the board is a cross-slice aggregation (runs joined with
 * assignments, reviews, model/tool calls and artifacts) computed on demand by the backend, so it is
 * loaded only when `enabled` (the Team drawer is open) and re-polled on an interval while it stays
 * open. Re-fetches on project change and aborts the in-flight request on change/close/unmount.
 * @author Rodrigo Mason
 */

import { useCallback, useEffect, useState } from 'react';

import { getProjectTeamActivity } from '../../api/client';
import type { TeamActivity } from '../../api/types';

const POLL_INTERVAL_MS = 5000;

export type TeamActivityState = {
	data: TeamActivity | null;
	loading: boolean;
	error: string;
	/** Forces an immediate re-fetch (independent of the poll cadence). */
	refresh: () => void;
};

/** Loads Team Activity for `projectId` while `enabled`; disabled or missing id clears the state. */
export function useTeamActivity(
	projectId: string | undefined,
	enabled: boolean,
): TeamActivityState {
	const [data, setData] = useState<TeamActivity | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState('');
	const [reloadToken, setReloadToken] = useState(0);

	const refresh = useCallback(() => setReloadToken((token) => token + 1), []);

	// biome-ignore lint/correctness/useExhaustiveDependencies: reloadToken is an intentional re-fetch trigger (bumped by refresh()), a dependency by design not read inside the effect body.
	useEffect(() => {
		if (!enabled || !projectId) {
			setData(null);
			setError('');
			setLoading(false);
			return;
		}
		let aborted = false;
		let controller: AbortController | null = null;

		const load = (showSpinner: boolean) => {
			controller?.abort();
			controller = new AbortController();
			if (showSpinner) setLoading(true);
			getProjectTeamActivity(projectId, controller.signal)
				.then((result) => {
					if (!aborted) {
						setData(result);
						setError('');
					}
				})
				.catch((requestError: unknown) => {
					if (aborted || controller?.signal.aborted) return;
					setError(
						requestError instanceof Error ? requestError.message : 'team_activity_unavailable',
					);
				})
				.finally(() => {
					if (!aborted) setLoading(false);
				});
		};

		load(true);
		const timer = window.setInterval(() => load(false), POLL_INTERVAL_MS);
		return () => {
			aborted = true;
			window.clearInterval(timer);
			controller?.abort();
		};
	}, [projectId, enabled, reloadToken]);

	return { data, loading, error, refresh };
}
