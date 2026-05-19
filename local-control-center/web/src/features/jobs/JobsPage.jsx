import React from 'react';
import { ExternalLink } from 'lucide-react';

import { approveAction, approveJob, cancelJob, retryJob, denyAction } from '../../api/platform-api.js';
import { Badge, Button, ConfirmAction, DataTable, EmptyState, Surface } from '../../components/primitives.jsx';
import { asArray, formatDate, shortId, toneForStatus } from '../common/format.js';

function RelatedLinks({ payload = {} }) {
	const links = [
		['pipeline', payload.pipelineId],
		['chat', payload.chatId],
		['session', payload.sessionId],
	].filter(([, value]) => value);
	if (!links.length) return <span className="muted">none</span>;
	return (
		<div className="link-list">
			{links.map(([label, value]) => (
				<a className="pill-link" key={label} href={`#${label}:${value}`}>
					<ExternalLink size={14} aria-hidden="true" />
					{label} {shortId(value)}
				</a>
			))}
		</div>
	);
}

export function JobsPage({ data }) {
	const overview = data.overview || {};
	const jobs = asArray(overview.jobs);
	const events = asArray(overview.events);
	const actions = asArray(overview.actionRequests);
	const pendingActions = actions.filter((action) => action.status === 'pending');

	const latestEventFor = (jobId) => events.find((event) => event.jobId === jobId);
	const reason = 'Approved from Control Center web UI';

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Jobs/Approvals</p>
				<h1 className="editorial-title">Granular control over queued work and sensitive actions.</h1>
			</div>
			<Surface
				title="Pending Action Requests"
				kicker="Granular Gates"
				actions={<Badge tone={pendingActions.length ? 'oxblood' : 'moss'} status={pendingActions.length ? 'pending' : 'approved'}>{pendingActions.length} pending</Badge>}
			>
				<DataTable
					rows={actions}
					empty={<EmptyState title="No action requests" body="Sensitive commands, installs, writes and sandbox escapes will appear here before execution." />}
					columns={[
						{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status} tone={toneForStatus(row.status)}>{row.status}</Badge> },
						{ key: 'actionType', label: 'Action' },
						{ key: 'riskLevel', label: 'Risk' },
						{ key: 'command', label: 'Command', render: (row) => <code>{row.command || 'not recorded'}</code> },
						{ key: 'jobId', label: 'Job', render: (row) => <code>{shortId(row.jobId)}</code> },
						{
							key: 'decide',
							label: 'Decision',
							render: (row) =>
								row.status === 'pending' ? (
									<div className="command-bar-group">
										<ConfirmAction confirmLabel="Approve action" onConfirm={() => data.mutate((token) => approveAction(token, row.jobId, row.id, reason))}>
											Approve action
										</ConfirmAction>
										<ConfirmAction variant="danger" confirmLabel="Deny action" onConfirm={() => data.mutate((token) => denyAction(token, row.jobId, row.id, 'Denied from Control Center web UI'))}>
											Deny
										</ConfirmAction>
									</div>
								) : (
									<span className="muted">decided {formatDate(row.decidedAt)}</span>
								),
						},
					]}
				/>
			</Surface>
			<Surface title="Queue and Leases" kicker="Durable Worker">
				<DataTable
					rows={jobs}
					empty={<EmptyState title="Queue is empty" body="Jobs created by pipelines, chats and prompt optimization will be claimed by workers when policy allows." />}
					columns={[
						{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status} tone={toneForStatus(row.status)}>{row.status}</Badge> },
						{ key: 'kind', label: 'Kind' },
						{ key: 'leaseOwner', label: 'Lease owner', render: (row) => <code>{row.leaseOwner || 'unleased'}</code> },
						{ key: 'leaseExpiresAt', label: 'Lease expires', render: (row) => formatDate(row.leaseExpiresAt) },
						{ key: 'latestEvent', label: 'Latest event', render: (row) => latestEventFor(row.id)?.type || 'job.created' },
						{ key: 'related', label: 'Related', render: (row) => <RelatedLinks payload={row.payload} /> },
						{
							key: 'actions',
							label: 'Actions',
							render: (row) => (
								<div className="command-bar-group">
									<Button
										variant="secondary"
										disabled={row.status !== 'approval_required'}
										onClick={() => data.mutate((token) => approveJob(token, row.id, 'Job gate acknowledged from web UI'))}
									>
										Acknowledge
									</Button>
									<ConfirmAction variant="danger" confirmLabel="Cancel job" disabled={row.status === 'cancelled'} onConfirm={() => data.mutate((token) => cancelJob(token, row.id, 'Cancelled from web UI'))}>
										Cancel
									</ConfirmAction>
									<Button variant="secondary" disabled={!['failed', 'cancelled'].includes(row.status)} onClick={() => data.mutate((token) => retryJob(token, row.id, 'Retried from web UI'))}>
										Retry
									</Button>
								</div>
							),
						},
					]}
				/>
			</Surface>
		</div>
	);
}
