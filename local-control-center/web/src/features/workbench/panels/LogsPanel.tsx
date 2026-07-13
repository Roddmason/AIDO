/**
 * Workbench Logs panel: merges project events and workflow events into one
 * recency-sorted, client-filterable table, capped to the latest entries.
 * @author Rodrigo Mason
 */
import { useMemo, useState } from 'react';

import type { Overview } from '../../../api/types';
import { Badge, DataTable, EmptyState } from '../../../components/primitives';
import { useI18n } from '../../../i18n/I18nProvider';
import { formatTime, shortId, toneForStatus } from '../../../lib/format';

type LogRow = {
	id: string;
	source: string;
	type: string;
	severity: string;
	createdAt: string;
	detail: string;
};

const MAX_ROWS = 200;

export function LogsPanel({
	events,
	workflowEvents,
}: {
	events: Overview['events'];
	workflowEvents: Overview['workflowEvents'];
}) {
	const { t } = useI18n();
	const [filter, setFilter] = useState('');

	const rows = useMemo<LogRow[]>(() => {
		const merged: LogRow[] = [
			...events.map((event) => ({
				id: event.id,
				source: 'event',
				type: event.type,
				severity: String(event.severity ?? 'info'),
				createdAt: event.createdAt,
				detail: shortId(event.id),
			})),
			...workflowEvents.map((event) => ({
				id: event.id,
				source: 'workflow',
				type: event.type,
				severity: String(event.severity ?? 'info'),
				createdAt: event.createdAt,
				detail: shortId(String(event.workflowRunId ?? event.workflowId)),
			})),
		];
		return merged
			.sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt))
			.slice(0, MAX_ROWS);
	}, [events, workflowEvents]);

	const query = filter.trim().toLowerCase();
	const filtered = query
		? rows.filter((row) =>
				`${row.type} ${row.severity} ${row.source} ${row.id}`.toLowerCase().includes(query),
			)
		: rows;

	return (
		<div className="stack">
			<div className="field">
				<label htmlFor="logs-filter">{t('app.workbench.logs.filterLabel', 'Filter logs')}</label>
				<input
					id="logs-filter"
					className="input"
					value={filter}
					placeholder={t('app.workbench.logs.filterHint', 'Filter by type, severity, source or id')}
					onChange={(event) => setFilter(event.target.value)}
				/>
			</div>
			<DataTable
				rows={filtered}
				empty={
					<EmptyState
						title={t('app.workbench.logs.emptyTitle', 'No events')}
						body={t(
							'app.workbench.logs.emptyBody',
							'Workflow, job, policy and evidence events appear here.',
						)}
					/>
				}
				columns={[
					{
						key: 'type',
						label: t('app.workbench.logs.colType', 'Type'),
						render: (row) => <span className="mono">{row.type}</span>,
					},
					{
						key: 'severity',
						label: t('app.workbench.logs.colSeverity', 'Severity'),
						render: (row) => <Badge tone={toneForStatus(row.severity)}>{row.severity}</Badge>,
					},
					{
						key: 'source',
						label: t('app.workbench.logs.colSource', 'Source'),
						render: (row) => <span className="mono">{row.source}</span>,
					},
					{
						key: 'when',
						label: t('app.workbench.logs.colWhen', 'When'),
						render: (row) => (
							<span className="mono">
								{formatTime(row.createdAt, t('app.workbenchEvidence.notRecorded', 'not recorded'))}
							</span>
						),
					},
				]}
			/>
			{rows.length >= MAX_ROWS ? (
				<div className="field-help">
					{t('app.workbench.logs.capped', 'Showing the latest 200 events.')}
				</div>
			) : null}
		</div>
	);
}
