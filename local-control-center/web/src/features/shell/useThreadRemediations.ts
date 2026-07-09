/**
 * Data source for the actionable blocker cards.
 *
 * Fetches the persisted `/threads/{id}/remediations`, groups them into {@link BlockerCardModel}s and
 * exposes `execute`/`dismiss` mutations that run through the shell's write `mutate`. Refetches when the
 * thread changes or the caller bumps `refreshSignal` (so a `blocked` event on the stream re-pulls the
 * fresh repair actions), and tracks a single `busyId` so only the clicked button shows its spinner.
 * Reads keep the previous cards visible during a background refetch — no full-panel skeleton flash.
 * @author Rodrigo Mason
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
	dismissRemediation,
	executeRemediation,
	getThreadRemediations,
	type RemediationActionRecord,
	type RemediationExecuteResponse,
} from '../../api/client';
import type { JsonObject } from '../../api/generated/openapi';
import type { Mutate } from '../../app/routes';
import {
	type BlockerActionModel,
	type BlockerCardModel,
	buildBlockerCards,
} from './remediationPresentation';

export type ThreadRemediationsHandle = {
	cards: BlockerCardModel[];
	loading: boolean;
	error: boolean;
	/** Id of the action/card currently mutating, so only that control shows a spinner. */
	busyId: string | null;
	execute: (
		action: BlockerActionModel,
		payload?: JsonObject,
	) => Promise<RemediationExecuteResponse | null>;
	dismiss: (card: BlockerCardModel) => Promise<void>;
	reload: () => void;
};

/**
 * Loads the thread's remediations and returns a uniform handle for the blocker cards. `threadId` is
 * null on the new-thread intake, which yields an empty, non-loading handle. `refreshSignal` lets the
 * host force a refetch after a stream event without changing the thread.
 */
export function useThreadRemediations(
	threadId: string | null,
	mutate: Mutate,
	refreshSignal = 0,
): ThreadRemediationsHandle {
	const [remediations, setRemediations] = useState<RemediationActionRecord[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState(false);
	const [busyId, setBusyId] = useState<string | null>(null);
	const [attempt, setAttempt] = useState(0);
	const loadedKeyRef = useRef<string | null>(null);
	const fetchedRef = useRef<string | null>(null);

	useEffect(() => {
		if (!threadId) {
			setRemediations([]);
			setError(false);
			setLoading(false);
			loadedKeyRef.current = null;
			fetchedRef.current = null;
			return undefined;
		}
		// One fetch per (thread, refresh signal, manual reload); the marker also dedups React's
		// StrictMode double-invoke and resets synchronously on cleanup so a quick change refetches.
		const requestKey = `${threadId}#${refreshSignal}#${attempt}`;
		if (fetchedRef.current === requestKey) return undefined;
		fetchedRef.current = requestKey;
		const controller = new AbortController();
		// Drop the previous thread's cards immediately, but keep the same thread's cards visible
		// across a refresh/refetch so an incoming event never flashes the panel back to a skeleton.
		if (loadedKeyRef.current !== threadId) {
			loadedKeyRef.current = threadId;
			setRemediations([]);
		}
		setLoading(true);
		setError(false);
		getThreadRemediations(threadId, controller.signal)
			.then((response) => {
				if (controller.signal.aborted) return;
				setRemediations(response.remediations);
				setLoading(false);
			})
			.catch(() => {
				if (controller.signal.aborted) return;
				setError(true);
				setLoading(false);
			});
		return () => {
			controller.abort();
			if (fetchedRef.current === requestKey) fetchedRef.current = null;
		};
	}, [threadId, refreshSignal, attempt]);

	const refetch = useCallback(async () => {
		if (!threadId) return;
		try {
			const response = await getThreadRemediations(threadId);
			setRemediations(response.remediations);
			setError(false);
		} catch {
			// A failed background refetch keeps the last known cards rather than blanking the panel.
		}
	}, [threadId]);

	const execute = useCallback(
		async (action: BlockerActionModel, payload?: JsonObject) => {
			if (!action.remediation || busyId) return null;
			setBusyId(action.id);
			try {
				const result = await mutate(
					(token) => executeRemediation(token, action.remediation?.id ?? '', payload),
					{ awaitRefresh: false },
				);
				await refetch();
				return result;
			} finally {
				setBusyId(null);
			}
		},
		[busyId, mutate, refetch],
	);

	const dismiss = useCallback(
		async (card: BlockerCardModel) => {
			if (busyId) return;
			setBusyId(`${card.key}:dismiss`);
			try {
				for (const record of card.remediations) {
					if (record.status !== 'pending') continue;
					await mutate((token) => dismissRemediation(token, record.id), { awaitRefresh: false });
				}
				await refetch();
			} finally {
				setBusyId(null);
			}
		},
		[busyId, mutate, refetch],
	);

	const reload = useCallback(() => setAttempt((value) => value + 1), []);

	const cards = useMemo(() => buildBlockerCards(remediations), [remediations]);

	return { cards, loading, error, busyId, execute, dismiss, reload };
}
