/**
 * Loads the per-story board of a thread and refetches it whenever a board-relevant event (story
 * progress or a delivery-stage milestone) lands in the thread event stream, so a card move shows up
 * within one event poll plus one read.
 * @author Rodrigo Mason
 */
import { useEffect, useState } from 'react';

import { getThreadBoard, type ThreadBoardResponse } from '../../api/client';

export type ThreadBoardState = {
	data: ThreadBoardResponse | null;
	loading: boolean;
	error: string;
};

type Snapshot = { threadId: string | null; board: ThreadBoardResponse | null };

/** Fetches `GET /threads/{id}/board` for `threadId` and again on every `refreshSequence` change. */
export function useThreadBoard(threadId: string | null, refreshSequence: number): ThreadBoardState {
	const [snapshot, setSnapshot] = useState<Snapshot>({ threadId: null, board: null });
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState('');

	// biome-ignore lint/correctness/useExhaustiveDependencies: refreshSequence is an explicit refetch trigger, not read inside the effect body.
	useEffect(() => {
		if (!threadId) {
			setSnapshot({ threadId: null, board: null });
			setLoading(false);
			setError('');
			return;
		}
		const controller = new AbortController();
		setLoading(true);
		getThreadBoard(threadId, controller.signal)
			.then((board) => {
				if (controller.signal.aborted) return;
				setSnapshot({ threadId, board });
				setError('');
			})
			.catch((reason: unknown) => {
				if (controller.signal.aborted) return;
				setError(reason instanceof Error ? reason.message : String(reason));
			})
			.finally(() => {
				if (!controller.signal.aborted) setLoading(false);
			});
		return () => controller.abort();
	}, [threadId, refreshSequence]);

	return { data: snapshot.threadId === threadId ? snapshot.board : null, loading, error };
}
