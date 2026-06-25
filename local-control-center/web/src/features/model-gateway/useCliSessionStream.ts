/**
 * Streams a CLI session's bounded event log to the UI by polling the incremental events endpoint while
 * the session is running. Accumulates only the new events (seq > the last seen), so the channel shows
 * real activity instead of an indefinite spinner; stops polling once the backend reports the session is
 * no longer running. The accumulated list is capped so a long session never grows the UI without bound.
 */

import { useEffect, useRef, useState } from 'react';

import { type CliSessionEvent, getCliSessionEvents } from '../../api/client';

const POLL_INTERVAL_MS = 1000;
const MAX_RENDERED_EVENTS = 300;

export type CliSessionStreamState = {
	events: CliSessionEvent[];
	running: boolean;
	loading: boolean;
	error: string;
};

/** Polls the CLI-session event stream for `sessionId`; a null id clears the state. */
export function useCliSessionStream(sessionId: string | null): CliSessionStreamState {
	const [events, setEvents] = useState<CliSessionEvent[]>([]);
	const [running, setRunning] = useState(false);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState('');
	const lastSeqRef = useRef(0);

	useEffect(() => {
		if (!sessionId) {
			setEvents([]);
			setRunning(false);
			setLoading(false);
			setError('');
			lastSeqRef.current = 0;
			return;
		}
		let active = true;
		let timer: number | undefined;
		setEvents([]);
		setRunning(true);
		setLoading(true);
		setError('');
		lastSeqRef.current = 0;

		const poll = async () => {
			try {
				const page = await getCliSessionEvents(sessionId, lastSeqRef.current);
				if (!active) return;
				if (page.events.length) {
					lastSeqRef.current = page.events[page.events.length - 1].seq;
					setEvents((previous) => [...previous, ...page.events].slice(-MAX_RENDERED_EVENTS));
				}
				setLoading(false);
				setRunning(page.running);
				if (page.running) {
					timer = window.setTimeout(poll, POLL_INTERVAL_MS);
				}
			} catch (pollError) {
				if (!active) return;
				setError(pollError instanceof Error ? pollError.message : 'cli_session_unavailable');
				setRunning(false);
				setLoading(false);
			}
		};
		void poll();
		return () => {
			active = false;
			if (timer) window.clearTimeout(timer);
		};
	}, [sessionId]);

	return { events, running, loading, error };
}
