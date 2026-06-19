/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useMemo, useState } from 'react';

import type { Overview } from '../api/types';
import { Badge, DataTable, Drawer, EmptyState } from '../components/primitives';
import { shortId, toneForStatus } from '../lib/format';

/**
 * Slide-over showing recent operational events with a free-text filter over
 * type, severity, ids and payload. Owns its own filter state; opened from the
 * WorkbenchHeader "Open event drawer" button (and Ctrl+Alt+E).
 */
export function EventsDrawer({
	open,
	onClose,
	events,
}: {
	open: boolean;
	onClose: () => void;
	events: Overview['events'];
}) {
	const [eventFilter, setEventFilter] = useState('');
	const filteredEvents = useMemo(() => {
		const query = eventFilter.trim().toLowerCase();
		if (!query) return events;
		return events.filter((event) => {
			const payload = JSON.stringify(event.payload ?? {}).toLowerCase();
			return (
				event.type.toLowerCase().includes(query) ||
				String(event.severity ?? '')
					.toLowerCase()
					.includes(query) ||
				String(event.projectId ?? '')
					.toLowerCase()
					.includes(query) ||
				String(event.jobId ?? '')
					.toLowerCase()
					.includes(query) ||
				payload.includes(query)
			);
		});
	}, [eventFilter, events]);

	return (
		<Drawer label="Event drawer" open={open} onClose={onClose}>
			<div className="drawer-body">
				<div className="field">
					<label htmlFor="event-filter">Event filter</label>
					<input
						id="event-filter"
						className="input"
						value={eventFilter}
						onChange={(event) => setEventFilter(event.target.value)}
						placeholder="Filter by type, severity, id or payload"
					/>
				</div>
			</div>
			<DataTable
				rows={filteredEvents.slice(0, 16)}
				empty={
					<EmptyState
						title="No events"
						body="Workflow, job, policy, and evidence events appear here."
					/>
				}
				columns={[
					{ key: 'type', label: 'Type', render: (row) => <span className="mono">{row.type}</span> },
					{
						key: 'severity',
						label: 'Severity',
						render: (row) => (
							<Badge tone={toneForStatus(row.severity)}>{row.severity ?? 'info'}</Badge>
						),
					},
					{
						key: 'id',
						label: 'Event',
						render: (row) => <span className="mono">{shortId(row.id)}</span>,
					},
				]}
			/>
		</Drawer>
	);
}
