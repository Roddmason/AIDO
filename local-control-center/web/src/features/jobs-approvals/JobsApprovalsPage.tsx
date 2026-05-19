import { useState } from 'react';

import { approveAction, cancelJob, denyAction, retryJob } from '../../api/client';
import type { ActionRequest, Job, Overview } from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { shortId, toneForStatus } from '../../lib/format';

export function JobsApprovalsPage({
	overview,
	mutate,
}: {
	overview: Overview;
	mutate: <T>(operation: (token: string) => Promise<T>) => Promise<T>;
}) {
	const [reason, setReason] = useState('Operator reviewed policy context.');
	const pendingActions = overview.actionRequests.filter((item) => item.status === 'pending');

	const run = (operation: (token: string) => Promise<unknown>) => {
		void mutate(operation);
	};

	return (
		<>
			<PageHeader
				kicker="Human gates"
				title="Jobs & Approvals"
				summary="Queue leases and command-level approvals. Approving a job never approves all sensitive actions by implication."
			/>
			<Surface title="Approval reason">
				<div className="field">
					<label htmlFor="approval-reason">Reason required for approve, deny, cancel and retry</label>
					<textarea id="approval-reason" className="textarea" value={reason} onChange={(event) => setReason(event.target.value)} />
				</div>
			</Surface>
			<div className="grid two">
				<Surface title="Pending action requests">
					<DataTable<ActionRequest>
						rows={pendingActions}
						empty={<EmptyState title="No pending approvals" body="Sensitive commands will appear here before execution." />}
						columns={[
							{ key: 'kind', label: 'Action', render: (row) => <span className="mono">{row.actionType}</span> },
							{ key: 'risk', label: 'Risk', render: (row) => <Badge tone={toneForStatus(row.riskLevel)}>{row.riskLevel}</Badge> },
							{ key: 'command', label: 'Command', render: (row) => <span className="mono">{row.command || 'n/a'}</span> },
							{
								key: 'actions',
								label: 'Decision',
								render: (row) => (
									<div className="inline">
										<button className="button primary" onClick={() => run((token) => approveAction(token, row.jobId, row.id, reason))}>Approve action</button>
										<button className="button danger" onClick={() => run((token) => denyAction(token, row.jobId, row.id, reason))}>Deny</button>
									</div>
								),
							},
						]}
					/>
				</Surface>
				<Surface title="Queue state">
					<DataTable<Job>
						rows={overview.jobs}
						empty={<EmptyState title="No jobs" body="Create or start a workflow to populate the queue." />}
						columns={[
							{ key: 'kind', label: 'Kind', render: (row) => <span className="mono">{row.kind}</span> },
							{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
							{ key: 'lease', label: 'Lease', render: (row) => <span>{row.leaseOwner ? `${row.leaseOwner} until ${row.leaseExpiresAt}` : 'none'}</span> },
							{
								key: 'actions',
								label: 'Actions',
								render: (row) => (
									<div className="inline">
										<button className="button" onClick={() => run((token) => retryJob(token, row.id, reason))}>Retry</button>
										<button className="button danger" onClick={() => run((token) => cancelJob(token, row.id, reason))}>Cancel</button>
									</div>
								),
							},
						]}
					/>
				</Surface>
			</div>
			<Surface title="Audit trail">
				<DataTable
					rows={overview.auditEvents.slice(0, 12)}
					empty={<EmptyState title="No audit entries" body="Mutating decisions are recorded here." />}
					columns={[
						{ key: 'action', label: 'Action', render: (row) => <span className="mono">{String(row.action ?? '')}</span> },
						{ key: 'target', label: 'Target', render: (row) => <span>{shortId(String(row.target ?? ''))}</span> },
						{ key: 'actor', label: 'Actor', render: (row) => <span>{String(row.actor ?? 'system')}</span> },
					]}
				/>
			</Surface>
		</>
	);
}
