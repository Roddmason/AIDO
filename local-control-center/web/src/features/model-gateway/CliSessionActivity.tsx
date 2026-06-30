/**
 * Live activity view for a CLI session: renders its streamed event timeline (started → stdout/stderr
 * chunks → tool actions → file changes → completed/failed/cancelled) as it arrives, plus a running
 * indicator and a cancel control. Replaces the indefinite spinner with real, incremental activity; the
 * events come from useCliSessionStream (incremental poll) and large chunks are downloadable artifacts.
 * @author Rodrigo Mason
 */

import { useEffect, useRef, useState } from 'react';

import { type CliSessionEvent, cancelCliSession } from '../../api/client';
import { Badge, EmptyState, Surface } from '../../components/primitives';
import { Button, ErrorState, Skeleton, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { shortId } from '../../lib/format';
import { useCliSessionStream } from './useCliSessionStream';

function eventTone(type: string): 'ok' | 'warn' | 'danger' | 'info' {
	if (type === 'completed') return 'ok';
	if (type === 'failed') return 'danger';
	if (type === 'cancelled' || type === 'stderr_chunk') return 'warn';
	return 'info';
}

function eventSummary(event: CliSessionEvent): string {
	const payload = (event.payload ?? {}) as Record<string, unknown>;
	for (const key of ['text', 'preview', 'path', 'reason', 'action'] as const) {
		if (typeof payload[key] === 'string') return payload[key] as string;
	}
	if (payload.returnCode !== undefined) return `exit ${String(payload.returnCode)}`;
	return '';
}

/** Renders the live event timeline for `sessionId`; `token` enables cancelling a running session. */
export function CliSessionActivity({
	sessionId,
	token,
	embedded = false,
}: {
	sessionId: string;
	token?: string;
	embedded?: boolean;
}) {
	const { t } = useI18n();
	const { notify } = useToast();
	const { events, running, loading, error } = useCliSessionStream(sessionId);
	const [cancelling, setCancelling] = useState(false);
	const [cancelRequested, setCancelRequested] = useState(false);
	const logRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		if (!running) setCancelRequested(false);
	}, [running]);

	// biome-ignore lint/correctness/useExhaustiveDependencies: events.length is the trigger that re-runs the auto-scroll as new events stream in; the body reads logRef instead of events, so Biome can't infer it.
	useEffect(() => {
		if (!embedded) return;
		const log = logRef.current;
		if (!log) return;
		log.scrollTop = log.scrollHeight;
	}, [embedded, events.length]);

	const cancel = async () => {
		if (!token || cancelling || cancelRequested) return;
		setCancelRequested(true);
		setCancelling(true);
		try {
			await cancelCliSession(token, sessionId);
			notify({ title: t('app.cliSession.cancelled', 'Cancellation requested'), tone: 'ok' });
		} catch (cancelError) {
			notify({
				title: t('app.cliSession.cancelFailed', 'Could not cancel the session'),
				body: cancelError instanceof Error ? cancelError.message : undefined,
				tone: 'danger',
			});
			setCancelRequested(false);
		} finally {
			setCancelling(false);
		}
	};

	const content = (
		<>
			<div className="inline">
				<Badge tone={running ? 'info' : 'ok'}>
					{running ? t('app.cliSession.running', 'Running') : t('app.cliSession.idle', 'Idle')}
				</Badge>
				<span className="mono">{shortId(sessionId)}</span>
				{token && running ? (
					<Button
						variant="danger"
						onClick={cancel}
						loading={cancelling}
						disabled={cancelRequested && !cancelling}
					>
						{t('app.cliSession.cancel', 'Cancel')}
					</Button>
				) : null}
			</div>
			{error ? (
				<ErrorState
					title={t('app.cliSession.errorTitle', 'Could not load session activity')}
					body={error}
				/>
			) : events.length ? (
				<div
					ref={logRef}
					className={embedded ? 'cli-session-log' : 'chat-transcript'}
					role="log"
					aria-live="polite"
					aria-relevant="additions"
				>
					{events.map((event) => (
						<div className="inline" key={event.id}>
							<Badge tone={eventTone(event.type)}>{event.type}</Badge>
							{eventSummary(event) ? <span className="mono">{eventSummary(event)}</span> : null}
						</div>
					))}
				</div>
			) : loading || running ? (
				<Skeleton label={t('app.cliSession.waiting', 'Waiting for activity')} />
			) : (
				<EmptyState
					title={t('app.cliSession.emptyTitle', 'No activity recorded')}
					body={t('app.cliSession.emptyBody', 'Select a running session to watch its live output.')}
				/>
			)}
		</>
	);

	if (embedded) {
		return (
			<section
				className="cli-session-activity"
				aria-label={t('app.cliSession.activityTitle', 'Session activity')}
			>
				{content}
			</section>
		);
	}

	return (
		<Surface title={t('app.cliSession.activityTitle', 'Session activity')} flat>
			{content}
		</Surface>
	);
}
