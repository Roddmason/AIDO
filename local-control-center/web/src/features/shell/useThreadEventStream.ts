/**
 * Polls a thread's append-only event log incrementally so the Threads view can render a live console
 * without blocking on the background Product Loop job. An explicit operator action can wake an idle
 * stream through `wakeThreadEventStream`.
 * @author Rodrigo Mason
 */
import { useEffect, useRef, useState } from 'react';

import { getThreadEvents } from '../../api/client';
import type { ThreadAgentEvent, ThreadEta, ThreadEvents } from '../../api/types';

const POLL_INTERVAL_MS = 1000;
const IDLE_POLL_INTERVAL_MS = 15000;
const MAX_RENDERED_EVENTS = 300;
const EMPTY_BOOTSTRAP_POLLS = 5;

/** Wakers of the mounted streams, by thread: an explicit action (a retry) asks for an immediate poll. */
const streamWakers = new Map<string, Set<() => void>>();

/**
 * Polls `threadId`'s mounted event streams right away and restores their fast cadence. Used after an
 * operator action that restarts work (a remediation such as `retry_loop`), so an idle stream on its
 * 15 s cadence reflects the first story transition within one fast poll instead of up to 15 s later.
 */
export function wakeThreadEventStream(threadId: string): void {
	for (const wake of streamWakers.get(threadId) ?? []) wake();
}

export type ThreadEventStreamState = {
	events: ThreadAgentEvent[];
	running: boolean;
	threadStatus: ThreadEvents['threadStatus'] | null;
	eta: ThreadEta | null;
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
	const [eta, setEta] = useState<ThreadEta | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState('');
	const lastSeqRef = useRef(0);

	// biome-ignore lint/correctness/useExhaustiveDependencies: refreshKey is an intentional re-fetch trigger (bumped by the parent to force a fresh poll), a dependency by design not read inside the effect body.
	useEffect(() => {
		if (!threadId) {
			setEvents([]);
			setRunning(false);
			setThreadStatus(null);
			setEta(null);
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
		setEta(null);
		setLoading(true);
		setError('');
		lastSeqRef.current = 0;
		let emptyPolls = 0;
		let inFlight = false;
		let wakeRequested = false;
		const schedule = (delay: number) => {
			timer = window.setTimeout(poll, wakeRequested ? 0 : delay);
			wakeRequested = false;
		};

		const poll = async () => {
			inFlight = true;
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
				setEta(page.eta ?? null);
				setRunning(page.running);
				setLoading(false);
				setError('');
				schedule(
					page.running || emptyPolls < EMPTY_BOOTSTRAP_POLLS
						? POLL_INTERVAL_MS
						: IDLE_POLL_INTERVAL_MS,
				);
			} catch (pollError) {
				if (!active) return;
				setError(pollError instanceof Error ? pollError.message : 'thread_events_unavailable');
				setRunning(false);
				setLoading(false);
				schedule(IDLE_POLL_INTERVAL_MS);
			} finally {
				inFlight = false;
			}
		};
		const wake = () => {
			if (!active) return;
			emptyPolls = 0;
			if (inFlight) {
				wakeRequested = true;
				return;
			}
			if (timer) window.clearTimeout(timer);
			void poll();
		};
		const wakers = streamWakers.get(threadId) ?? new Set<() => void>();
		wakers.add(wake);
		streamWakers.set(threadId, wakers);
		void poll();
		return () => {
			wakers.delete(wake);
			if (!wakers.size) streamWakers.delete(threadId);
			active = false;
			if (timer) window.clearTimeout(timer);
		};
	}, [threadId, refreshKey]);

	return { events, running, threadStatus, eta, loading, error };
}
