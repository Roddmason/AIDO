/**
 * Slide-over that surfaces pending action requests awaiting a human approval.
 */
import type { Overview } from '../api/types';
import { Badge, DataTable, Drawer, EmptyState } from '../components/primitives';
import { shortId, toneForStatus } from '../lib/format';

/**
 * Slide-over listing the pending action requests awaiting a human decision.
 * Opened from the WorkbenchHeader "Open approvals drawer" button and dismissed
 * with Escape (handled by the shell shortcut layer).
 */
export function ApprovalsDrawer({
	open,
	onClose,
	actionRequests,
}: {
	open: boolean;
	onClose: () => void;
	actionRequests: Overview['actionRequests'];
}) {
	return (
		<Drawer label="Approval drawer" open={open} onClose={onClose}>
			<DataTable
				rows={actionRequests.filter((item) => item.status === 'pending').slice(0, 12)}
				empty={
					<EmptyState
						title="No pending approvals"
						body="Action requests appear here when policy gates execution."
					/>
				}
				columns={[
					{
						key: 'action',
						label: 'Action',
						render: (row) => <span className="mono">{row.actionType}</span>,
					},
					{
						key: 'risk',
						label: 'Risk',
						render: (row) => <Badge tone={toneForStatus(row.riskLevel)}>{row.riskLevel}</Badge>,
					},
					{
						key: 'command',
						label: 'Command',
						render: (row) => <span className="mono">{row.command || 'n/a'}</span>,
					},
					{
						key: 'job',
						label: 'Job',
						render: (row) => <span className="mono">{shortId(row.jobId)}</span>,
					},
				]}
			/>
		</Drawer>
	);
}
