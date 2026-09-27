/**
 * Status-bar item for the local worker, the process that claims every queued operation (git
 * refresh, runtime validation, thread runs). A fresh install starts it paused on purpose ("requires
 * an explicit resume"), but nothing in the web said so or offered the resume: queued work sat in
 * `queued` forever. This item shows the worker state and, when paused, a one-click Resume.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useState } from 'react';

import { getWorkerStatus, resumeWorker, type WorkerStatusResponse } from '../api/client';
import { Button, StatusDot, useToast } from '../components/ui';
import { redactVisibleSecret } from '../lib/format';

const POLL_MS = 15_000;

type Tone = 'ok' | 'warn' | 'danger';

export function WorkerStatusItem({
	token,
	t,
}: {
	token: string;
	t: (key: string, fallback?: string) => string;
}) {
	const { notify } = useToast();
	const [status, setStatus] = useState<WorkerStatusResponse | null>(null);
	const [busy, setBusy] = useState(false);

	const load = useCallback(async (signal?: AbortSignal) => {
		try {
			setStatus(await getWorkerStatus(signal));
		} catch {
			// The API link item already reports a lost connection; keep the last known state.
		}
	}, []);

	useEffect(() => {
		const controller = new AbortController();
		void load(controller.signal);
		const timer = window.setInterval(() => void load(), POLL_MS);
		// A queued operation is the moment the operator needs to know the worker will not take it.
		const onExecution = () => void load();
		window.addEventListener('aido:execution', onExecution);
		return () => {
			controller.abort();
			window.clearInterval(timer);
			window.removeEventListener('aido:execution', onExecution);
		};
	}, [load]);

	if (!status) return null;

	const resume = async () => {
		if (!token) {
			notify({
				title: t(
					'app.worker.tokenRequired',
					'A local write token is required to resume the worker.',
				),
				tone: 'warn',
			});
			return;
		}
		setBusy(true);
		try {
			setStatus(await resumeWorker(token));
			notify({
				title: t('app.worker.resumed', 'Worker resumed: queued work will start'),
				tone: 'ok',
			});
		} catch (error) {
			notify({
				title: t('app.worker.resumeFailed', 'Could not resume the worker'),
				body: redactVisibleSecret(
					error instanceof Error ? error.message : String(error),
					'worker resume failed',
				),
				tone: 'danger',
			});
		} finally {
			setBusy(false);
		}
	};

	const paused = status.paused || status.status === 'paused';
	const offline = !paused && (status.status === 'stopped' || status.role === 'offline');
	const tone: Tone = paused ? 'warn' : offline ? 'danger' : 'ok';
	const label = paused
		? t('app.worker.paused', 'Worker paused')
		: offline
			? t('app.worker.offline', 'Worker offline')
			: t('app.worker.running', 'Worker running');
	const hint = paused
		? t(
				'app.worker.pausedHint',
				'Queued work (git refresh, runtime checks, thread runs) waits until you resume the worker.',
			)
		: offline
			? t(
					'app.worker.offlineHint',
					'No worker process is connected. Start AIDO with its launcher so the worker runs next to the API.',
				)
			: status.reason || undefined;

	return (
		<span
			className="status-bar-item"
			data-worker-state={paused ? 'paused' : offline ? 'offline' : 'running'}
			title={hint}
		>
			<StatusDot tone={tone} />
			{label}
			{paused ? (
				<Button
					className="settings-console-link"
					disabled={busy}
					aria-busy={busy}
					onClick={() => void resume()}
				>
					{t('app.worker.resume', 'Resume')}
				</Button>
			) : null}
		</span>
	);
}
