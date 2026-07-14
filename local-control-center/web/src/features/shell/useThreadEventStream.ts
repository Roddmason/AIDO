/**
 * Polls a thread's append-only event log incrementally so the Threads view can render a live console
 * without blocking on the background Product Loop job.
 * @author Rodrigo Mason
 */
import { useEffect, useRef, useState } from 'react';

import { getThreadEvents } from '../../api/client';
import type { ThreadAgentEvent, ThreadEvents } from '../../api/types';

const POLL_INTERVAL_MS = 1000;
const MAX_RENDERED_EVENTS = 300;
const EMPTY_BOOTSTRAP_POLLS = 5;

export type ThreadEventStreamState = {
	events: ThreadAgentEvent[];
	running: boolean;
	threadStatus: ThreadEvents['threadStatus'] | null;
	loading: boolean;
	error: string;
};

/** Polls the bounded thread console event stream for `threadId`; null clears state. */
export function useThreadEventStream(
	threadId: string | null,
	refreshKey = 0,
): ThreadEventStreamState {
	const [events, setEvents] = useState<ThreadAgentEvent[]>([]);
	const [running, setRunning] = useState(false);
	const [threadStatus, setThreadStatus] = useState<ThreadEvents['threadStatus'] | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState('');
	const lastSeqRef = useRef(0);

	// biome-ignore lint/correctness/useExhaustiveDependencies: refreshKey is an intentional re-fetch trigger (bumped by the parent to force a fresh poll), a dependency by design not read inside the effect body.
	useEffect(() => {
		if (!threadId) {
			setEvents([]);
			setRunning(false);
			setThreadStatus(null);
			setLoading(false);
			setError('');
			lastSeqRef.current = 0;
			return;
		}
		let active = true;
		let timer: number | undefined;
		setEvents([]);
		setRunning(true);
		setThreadStatus(null);
		setLoading(true);
		setError('');
		lastSeqRef.current = 0;
		let emptyPolls = 0;

		const poll = async () => {
			try {
				const page = await getThreadEvents(threadId, lastSeqRef.current, MAX_RENDERED_EVENTS);
				if (!active) return;
				if (page.events.length) {
					lastSeqRef.current = page.lastSeq;
					setEvents((previous) => [...previous, ...page.events].slice(-MAX_RENDERED_EVENTS));
					emptyPolls = 0;
				} else {
					emptyPolls += 1;
				}
				setThreadStatus(page.threadStatus);
				setRunning(page.running);
				setLoading(false);
				if (page.running || emptyPolls < EMPTY_BOOTSTRAP_POLLS) {
					timer = window.setTimeout(poll, POLL_INTERVAL_MS);
				}
			} catch (pollError) {
				if (!active) return;
				setError(pollError instanceof Error ? pollError.message : 'thread_events_unavailable');
				setRunning(false);
				setLoading(false);
			}
		};
		void poll();
		return () => {
			active = false;
			if (timer) window.clearTimeout(timer);
		};
	}, [threadId, refreshKey]);

	return { events, running, threadStatus, loading, error };
}
