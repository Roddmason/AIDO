/**
 * Read-only operational snapshots and explicit, authenticated worker controls.
 * Lifecycle and cancellation availability come from backend DTOs, never UI inference.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useId, useState } from 'react';
import {
	type ExecutionsResponse,
	type ProcessesResponse,
	type ResourceStatusResponse,
	type RuntimeProvidersResponse,
	requestGeneratedOperation as request,
	type WorkerStatusResponse,
} from '../../api/generated/openapi';
import { Disclosure } from '../../components/Disclosure';
import { Button, Dialog } from '../../components/ui';
import type { SectionContext } from './sections';

type Snapshot = {
	worker: WorkerStatusResponse;
	resources: ResourceStatusResponse;
	processes: ProcessesResponse;
	executions: ExecutionsResponse;
	runtimes: RuntimeProvidersResponse;
};

export function OperationsPanel({ ctx }: { ctx: SectionContext }) {
	const { t, token } = ctx;
	const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
	const [error, setError] = useState('');
	const [busy, setBusy] = useState(false);
	const [stopTarget, setStopTarget] = useState<string | null>(null);
	const [reason, setReason] = useState('');
	const reasonId = useId();
	const projectId = ctx.selectedProject?.id;
	const refresh = useCallback(
		async (signal?: AbortSignal) => {
			const [worker, resources, processes, executions, runtimes] = await Promise.all([
				request('worker_status_api_v1_workers_status_get', { signal }),
				request('resource_status_api_v1_operations_resources_get', { signal }),
				request('processes_api_v1_operations_processes_get', { signal, query: { limit: 30 } }),
				request('list_executions_api_v1_executions_get', { signal, query: { limit: 30 } }),
				request('list_runtime_providers_api_v1_runtime_providers_get', {
					signal,
					query: { projectId },
				}),
			]);
			if (!signal?.aborted) {
				setSnapshot({ worker, resources, processes, executions, runtimes });
				setError('');
			}
		},
		[projectId],
	);
	useEffect(() => {
		const controller = new AbortController();
		let timer: ReturnType<typeof setTimeout>;
		const poll = async () => {
			try {
				await refresh(controller.signal);
			} catch (failure) {
				if (!controller.signal.aborted) setError(String(failure));
			}
			if (!controller.signal.aborted) timer = setTimeout(poll, 3000);
		};
		void poll();
		return () => {
			controller.abort();
			clearTimeout(timer);
		};
	}, [refresh]);
	const act = async (operation: () => Promise<unknown>) => {
		setBusy(true);
		try {
			await operation();
			await refresh();
		} catch (failure) {
			setError(String(failure));
		} finally {
			setBusy(false);
		}
	};
	const bytes = (value: number | null | undefined) =>
		value == null ? '—' : `${(value / 1024 ** 3).toFixed(2)} GiB`;
	const sample = snapshot?.resources.latestSample?.snapshot;
	return (
		<Disclosure
			title={t('app.operations.title', 'Operations')}
			summary={t('app.operations.summary', 'Worker, host capacity and managed executions')}
			headingLevel={4}
		>
			<div className="stack">
				<p role="status">
					{error
						? t(
								'app.operations.disconnected',
								'Operational snapshot unavailable; the last reading may be stale.',
							)
						: snapshot
							? t('app.operations.connected', 'API connected')
							: t('app.operations.loading', 'Reading operational state…')}
				</p>
				{error ? (
					<p role="alert" className="muted">
						{error}
					</p>
				) : null}
				{snapshot ? (
					<>
						<p>
							{t('app.operations.worker', 'Worker')} · {String(snapshot.worker.connected)} ·{' '}
							{snapshot.worker.role} · {snapshot.worker.status} · {snapshot.worker.reason}
						</p>
						<p className="mono">
							{snapshot.worker.ownerId ?? '—'} · {snapshot.worker.heartbeatAt ?? '—'}
						</p>
						<div className="actions">
							<Button
								disabled={!token || busy}
								onClick={() =>
									void act(() => request('worker_pause_api_v1_workers_pause_post', { token }))
								}
							>
								{t('app.operations.pause', 'Pause')}
							</Button>
							<Button
								disabled={!token || busy}
								onClick={() =>
									void act(() => request('worker_resume_api_v1_workers_resume_post', { token }))
								}
							>
								{t('app.operations.resume', 'Resume')}
							</Button>
							<Button
								disabled={!token || busy}
								onClick={() =>
									void act(() => request('worker_drain_api_v1_workers_drain_post', { token }))
								}
							>
								{t('app.operations.drain', 'Drain')}
							</Button>
							<Button
								variant="danger"
								disabled={!token || busy}
								onClick={() => {
									setReason('');
									setStopTarget('emergency');
								}}
							>
								{t('app.operations.emergency', 'Emergency stop')}
							</Button>
						</div>
						<p>
							{t('app.operations.waiting', 'Waiting for resources')} ·{' '}
							{snapshot.resources.resourceWaitCount}
						</p>
						<Button
							disabled={!token || busy}
							onClick={() =>
								void act(() =>
									request('sample_resources_api_v1_operations_resources_sample_post', { token }),
								)
							}
						>
							{t('app.operations.sample', 'Sample host resources')}
						</Button>
						{sample ? (
							<dl className="settings-readouts">
								<div>
									<dt>{t('app.operations.sampleAt', 'Sample timestamp')}</dt>
									<dd>{sample.sampledAt}</dd>
								</div>
								<div>
									<dt>CPU</dt>
									<dd>
										{sample.cpuPercent1s}% / {sample.cpuPercent30s}% (30s)
									</dd>
								</div>
								<div>
									<dt>{t('app.operations.memory', 'Available RAM')}</dt>
									<dd>{bytes(sample.availableMemoryBytes)}</dd>
								</div>
								<div>
									<dt>{t('app.operations.commit', 'Committed / limit / pagefile')}</dt>
									<dd>
										{bytes(sample.committedMemoryBytes)} / {bytes(sample.commitLimitBytes)} /{' '}
										{bytes(sample.swapOrPagefileUsedBytes)}
									</dd>
								</div>
								<div>
									<dt>{t('app.operations.disk', 'Free disk')}</dt>
									<dd>
										{Object.entries(sample.diskFreeBytes)
											.map(([path, free]) => `${path}: ${bytes(free)}`)
											.join(' · ')}
									</dd>
								</div>
								<div>
									<dt>{t('app.operations.gpu', 'GPU / VRAM')}</dt>
									<dd>
										{sample.gpuUtilizationPercent == null
											? '—'
											: `${sample.gpuUtilizationPercent}%`}{' '}
										· {bytes(sample.gpuMemoryUsedBytes)} / {bytes(sample.gpuMemoryFreeBytes)}
									</dd>
								</div>
								<div>
									<dt>{t('app.operations.unreal', 'UnrealEditor')}</dt>
									<dd>{String(sample.unrealEditorRunning)}</dd>
								</div>
							</dl>
						) : (
							<p>{t('app.operations.noSample', 'No host sample available.')}</p>
						)}
						<h5>{t('app.operations.workloads', 'Active workloads')}</h5>
						<ul>
							{(snapshot.resources.activeLeases ?? []).map((lease) => (
								<li key={lease.id}>
									{lease.workloadClass} · {lease.executionId} · {lease.expiresAt}
								</li>
							))}
						</ul>
						<h5>{t('app.operations.executions', 'Recent executions')}</h5>
						<ul className="stack">
							{snapshot.executions.executions.map((execution) => (
								<li key={execution.executionId}>
									<p>
										{execution.operation} · {execution.status} · {execution.reason}
									</p>
									<code>{execution.executionId}</code>
									{execution.canCancel ? (
										<Button
											disabled={!token || busy}
											onClick={() => {
												setReason('');
												setStopTarget(execution.executionId);
											}}
										>
											{t('app.operations.cancel', 'Cancel execution')}
										</Button>
									) : null}
								</li>
							))}
						</ul>
						<h5>{t('app.operations.processes', 'Managed processes · peak RAM')}</h5>
						<ul>
							{snapshot.processes.processes.map((process) => (
								<li key={process.managedProcessId}>
									{process.managedProcessId} · PID {process.rootPid} ·{' '}
									{bytes(process.peakMemoryBytes)} · CPU {process.cpuTimeSeconds}s ·{' '}
									{process.terminationReason || '—'} · {process.finishedAt ?? '—'}
								</li>
							))}
						</ul>
						<h5>{t('app.operations.readiness', 'Effective runtime readiness')}</h5>
						<ul>
							{snapshot.runtimes.providers.map((runtime) => (
								<li key={runtime.id}>
									{runtime.displayName} · {runtime.effectiveStatus ?? '—'} ·{' '}
									{runtime.blockingReasons?.join(' · ') || runtime.reason}
								</li>
							))}
						</ul>
					</>
				) : null}
			</div>
			<Dialog
				open={stopTarget !== null}
				onClose={() => {
					if (!busy) setStopTarget(null);
				}}
				label={t('app.operations.stopTitle', 'Confirm stop request')}
			>
				<form
					className="stack"
					onSubmit={(event) => {
						event.preventDefault();
						if (!stopTarget || !reason.trim()) return;
						void act(async () => {
							if (stopTarget === 'emergency')
								await request('emergency_stop_api_v1_workers_emergency_stop_post', {
									token,
									body: { reason },
								});
							else
								await request('cancel_api_v1_executions__execution_id__cancel_post', {
									token,
									pathParams: { execution_id: stopTarget },
									body: { reason },
								});
							setStopTarget(null);
						});
					}}
				>
					<p>
						{t(
							'app.operations.stopHelp',
							'The request is durable. Termination is confirmed only after backend cleanup; Pause does not cancel active work.',
						)}
					</p>
					<label htmlFor={reasonId}>{t('app.operations.reason', 'Human reason')}</label>
					<textarea
						id={reasonId}
						required
						maxLength={1000}
						value={reason}
						onChange={(event) => setReason(event.target.value)}
					/>
					<Button type="submit" variant="danger" disabled={!token || busy || !reason.trim()}>
						{t('app.operations.confirm', 'Request termination')}
					</Button>
				</form>
			</Dialog>
		</Disclosure>
	);
}
