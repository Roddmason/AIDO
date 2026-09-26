/**
 * Data hook for a single real thread: loads its timeline and exposes the two write paths
 * (post a user message → runs the coordinator; resolve a pending decision).
 *
 * Reads go straight to the API; writes flow through the control-plane `mutate` runner so the
 * write token is injected and the global overview refreshes. After every write we reload the
 * thread detail so the transcript reflects the coordinator's response or block immediately.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useState } from 'react';
import { getThread, postThreadMessage, resolveThreadDecision } from '../../api/client';
import type { ThreadDetail } from '../../api/types';
import type { Mutate } from '../../app/routes';
import type { DecisionAnswer } from './ThreadDecisionAnswers';
import { useThreadRefresh } from './useThreadRefresh';

type UseThreadConversation = {
	detail: ThreadDetail | null;
	loading: boolean;
	error: boolean;
	busy: boolean;
	reload: () => void;
	send: (content: string) => Promise<void>;
	/** Resolves every answered decision in sequence (one write each, same as the API contract),
	 * then reloads once so the chat and the execution panel settle together. */
	resolveDecisions: (answers: DecisionAnswer[]) => Promise<void>;
};

/** Loads and drives one thread by id; a null/sentinel id yields an idle, empty state. */
export function useThreadConversation(
	threadId: string | null,
	mutate: Mutate,
): UseThreadConversation {
	const { invalidate } = useThreadRefresh();
	const [detail, setDetail] = useState<ThreadDetail | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState(false);
	const [busy, setBusy] = useState(false);

	const load = useCallback(
		(signal?: AbortSignal) => {
			if (!threadId) {
				setDetail(null);
				setError(false);
				return;
			}
			setLoading(true);
			setError(false);
			getThread(threadId, signal)
				.then((data) => {
					if (!signal?.aborted) setDetail(data);
				})
				.catch(() => {
					if (!signal?.aborted) setError(true);
				})
				.finally(() => {
					if (!signal?.aborted) setLoading(false);
				});
		},
		[threadId],
	);

	const reload = useCallback(() => {
		load();
		if (threadId) invalidate(threadId);
	}, [load, threadId, invalidate]);

	useEffect(() => {
		const controller = new AbortController();
		load(controller.signal);
		return () => controller.abort();
	}, [load]);

	const send = useCallback(
		async (content: string) => {
			if (!threadId) return;
			setBusy(true);
			try {
				await mutate((token) => postThreadMessage(token, threadId, { content }), {
					awaitRefresh: false,
				});
				reload();
			} finally {
				setBusy(false);
			}
		},
		[threadId, mutate, reload],
	);

	const resolveDecisions = useCallback(
		async (answers: DecisionAnswer[]) => {
			if (!threadId || answers.length === 0) return;
			setBusy(true);
			try {
				// Sequential: each resolve can mutate shared loop state (e.g. defers the rest of a
				// Product Owner batch), so two in flight at once would race on that state.
				for (const answer of answers) {
					await mutate(
						(token) =>
							resolveThreadDecision(token, threadId, answer.decisionId, {
								selectedOptions: answer.selectedOptions,
								freeText: answer.freeText || undefined,
							}),
						{ awaitRefresh: false },
					);
				}
				reload();
			} finally {
				setBusy(false);
			}
		},
		[threadId, mutate, reload],
	);

	return { detail, loading, error, busy, reload, send, resolveDecisions };
}
