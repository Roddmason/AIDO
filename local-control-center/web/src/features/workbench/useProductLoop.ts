/**
 * Fetches the project-scoped product-loop aggregate (loops, questions, brief, assumptions, decisions,
 * backlog) for the Workbench loop sections. Re-fetches when the selected project changes or
 * when `refresh()` is called (after a loop mutation), and aborts the in-flight request on change/unmount.
 * Decoupled from the 5s overview poll: the loop is detail-shaped and changes infrequently, so it is
 * loaded on demand rather than folded into /overview.
 * @author Rodrigo Mason
 */

import { useCallback, useEffect, useState } from 'react';

import { getProjectProductLoop, type ProjectProductLoopResponse } from '../../api/client';

export type ProductLoopState = {
	data: ProjectProductLoopResponse | null;
	loading: boolean;
	error: string;
	/** Re-fetches the loop aggregate (call after a start/transition mutation). */
	refresh: () => void;
};

/** Loads the product-loop aggregate for `projectId`; null id (no project) clears the state. */
export function useProductLoop(projectId: string | undefined): ProductLoopState {
	const [data, setData] = useState<ProjectProductLoopResponse | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState('');
	const [reloadToken, setReloadToken] = useState(0);

	const refresh = useCallback(() => setReloadToken((token) => token + 1), []);

	// biome-ignore lint/correctness/useExhaustiveDependencies: reloadToken is an intentional re-fetch trigger (bumped by refresh()), a dependency by design not read inside the effect body.
	useEffect(() => {
		if (!projectId) {
			setData(null);
			setError('');
			setLoading(false);
			return;
		}
		const controller = new AbortController();
		setLoading(true);
		setError('');
		getProjectProductLoop(projectId, controller.signal)
			.then((result) => {
				if (!controller.signal.aborted) setData(result);
			})
			.catch((requestError: unknown) => {
				if (controller.signal.aborted) return;
				setData(null);
				setError(requestError instanceof Error ? requestError.message : 'product_loop_unavailable');
			})
			.finally(() => {
				if (!controller.signal.aborted) setLoading(false);
			});
		return () => controller.abort();
	}, [projectId, reloadToken]);

	return { data, loading, error, refresh };
}
