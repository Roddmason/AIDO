/**
 * Collapsible bottom dock for logs / run output.
 *
 * Logs sigue el hilo activo con el mismo stream incremental y el mismo lenguaje visual que la
 * consola del ThreadExecutionPanel; sin hilo seleccionado cae a los eventos del control plane del
 * proyecto. Output lista los comandos de QA realmente ejecutados (test results del proyecto).
 * El stream solo se suscribe con el dock abierto: el pane desktop mantiene montado a su hijo aun
 * colapsado, así que un poll incondicional correría invisible.
 * @author Rodrigo Mason
 */
import { Terminal, X } from 'lucide-react';
import { useMemo, useState } from 'react';

import type { TestResultRecord } from '../api/generated/openapi';
import type { EventRecord, Overview } from '../api/types';
import { DataTable, EmptyState, IconButton, Tabs } from '../components/ui';
import { ThreadConsoleRow } from '../features/shell/ThreadExecutionPanel';
import { useThreadEventStream } from '../features/shell/useThreadEventStream';
import { useI18n } from '../i18n/I18nProvider';

const PROJECT_EVENT_LIMIT = 100;

function formatClock(iso: string): string {
	const parsed = new Date(iso);
	if (Number.isNaN(parsed.getTime())) return '';
	return parsed.toLocaleTimeString(undefined, {
		hour12: false,
		hour: '2-digit',
		minute: '2-digit',
		second: '2-digit',
	});
}

function formatDuration(durationMs: number | null | undefined): string {
	if (typeof durationMs !== 'number' || !Number.isFinite(durationMs)) return '—';
	if (durationMs < 1000) return `${Math.round(durationMs)} ms`;
	return `${(durationMs / 1000).toFixed(1)} s`;
}

/** Fila de evento del control plane: mismo grid que la consola del hilo, sin sequence ni actor. */
function ProjectEventRow({ event }: { event: EventRecord }) {
	return (
		<div className="thread-console-row" data-type={event.type}>
			<time className="thread-console-time" dateTime={event.createdAt} title={event.createdAt}>
				{formatClock(event.createdAt)}
			</time>
			<span className="thread-console-seq mono">{event.severity || 'info'}</span>
			<span className="thread-console-actor">{event.projectId ? 'project' : 'platform'}</span>
			<div className="thread-console-body">
				<div className="thread-console-line">
					<strong>{event.type}</strong>
					{event.jobId ? <span className="thread-console-chip mono">{event.jobId}</span> : null}
				</div>
			</div>
		</div>
	);
}

/**
 * Titled, closable bottom dock with live Logs/Output tabs; `onClose` collapses the pane.
 *
 * `collapsed` corta la suscripción al stream: sin él, el dock cerrado seguiría haciendo polling.
 */
export function BottomPanel({
	onClose,
	collapsed = false,
	threadId = null,
	projectId = null,
	overview,
}: {
	onClose: () => void;
	collapsed?: boolean;
	threadId?: string | null;
	projectId?: string | null;
	overview?: Overview;
}) {
	const { t } = useI18n();
	const title = t('app.bottomPanel.title', 'Bottom panel');
	const [activeTab, setActiveTab] = useState('logs');
	const tabs = [
		{ id: 'logs', label: t('app.bottomPanel.logs', 'Logs') },
		{ id: 'output', label: t('app.bottomPanel.output', 'Output') },
	];

	const streamThreadId = collapsed || activeTab !== 'logs' ? null : threadId;
	const { events: threadEvents, error: streamError } = useThreadEventStream(streamThreadId);

	const projectEvents = useMemo<EventRecord[]>(() => {
		if (!overview || threadId) return [];
		const scoped = projectId
			? overview.events.filter((event) => event.projectId === projectId)
			: overview.events;
		return scoped.slice(0, PROJECT_EVENT_LIMIT);
	}, [overview, projectId, threadId]);

	const testResults = useMemo<TestResultRecord[]>(() => {
		if (!overview) return [];
		return projectId
			? overview.testResultRecords.filter((result) => result.projectId === projectId)
			: overview.testResultRecords;
	}, [overview, projectId]);

	const logsEmpty = (
		<EmptyState
			title={t('app.bottomPanel.emptyTitle', 'No active session')}
			body={t('app.bottomPanel.emptyBody', 'Logs and terminal output will appear here.')}
		/>
	);

	const logsBody = () => {
		if (streamError) {
			return (
				<div className="thread-console-status" data-tone="danger">
					{streamError}
				</div>
			);
		}
		if (threadId && threadEvents.length) {
			return (
				<div
					className="thread-execution-log"
					role="log"
					aria-live="polite"
					aria-relevant="additions"
				>
					{threadEvents.map((event) => (
						<ThreadConsoleRow key={event.id} event={event} />
					))}
				</div>
			);
		}
		if (!threadId && projectEvents.length) {
			return (
				<div
					className="thread-execution-log"
					role="log"
					aria-live="polite"
					aria-relevant="additions"
				>
					{projectEvents.map((event) => (
						<ProjectEventRow key={event.id} event={event} />
					))}
				</div>
			);
		}
		return logsEmpty;
	};

	return (
		<section className="bottom-panel" aria-label={title}>
			<header className="bottom-panel-header">
				<span className="bottom-panel-title">
					<Terminal aria-hidden="true" size={15} />
					{title}
				</span>
				<IconButton aria-label={t('app.bottomPanel.close', 'Close bottom panel')} onClick={onClose}>
					<X aria-hidden="true" size={16} />
				</IconButton>
			</header>
			<Tabs
				className="bottom-panel-tabs"
				idBase="bottom-dock"
				label={title}
				tabs={tabs}
				activeTab={activeTab}
				onChange={setActiveTab}
			>
				<div className="bottom-panel-body">
					{activeTab === 'logs' ? (
						logsBody()
					) : (
						<DataTable<TestResultRecord>
							caption={t('app.bottomPanel.outputCaption', 'Executed QA commands')}
							columns={[
								{
									key: 'command',
									label: t('app.bottomPanel.outputCommand', 'Command'),
									render: (row) => <span className="mono">{row.command}</span>,
								},
								{
									key: 'status',
									label: t('app.bottomPanel.outputStatus', 'Status'),
									render: (row) => row.status,
								},
								{
									key: 'duration',
									label: t('app.bottomPanel.outputDuration', 'Duration'),
									render: (row) => formatDuration(row.durationMs),
								},
								{
									key: 'createdAt',
									label: t('app.bottomPanel.outputWhen', 'When'),
									render: (row) => formatClock(row.createdAt),
								},
							]}
							rows={testResults}
							empty={
								<EmptyState
									title={t('app.bottomPanel.outputEmptyTitle', 'No command output yet')}
									body={t(
										'app.bottomPanel.outputEmptyBody',
										'QA commands executed by the loop will be listed here with their status.',
									)}
								/>
							}
						/>
					)}
				</div>
			</Tabs>
		</section>
	);
}
