import React from 'react';
import { LockKeyhole, ShieldAlert, ShieldCheck } from 'lucide-react';

import { Badge, DataTable, EmptyState, Surface } from '../../components/primitives.jsx';
import { asArray, formatDate, toneForStatus } from '../common/format.js';

export function SandboxSecurityPage({ data }) {
	const overview = data.overview || {};
	const security = overview.security || {};
	const actionRequests = asArray(overview.actionRequests);
	const auditEvents = asArray(overview.auditEvents).slice(0, 12);

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Sandbox/Security</p>
				<h1 className="editorial-title">Approvals are per action, not a blanket job permission.</h1>
			</div>
			<div className="section-grid" data-motion-item>
				<Surface title="Loopback Security" kicker="API">
					<div className="timeline">
						<div className="timeline-item">
							<LockKeyhole size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>{security.loopbackOnly ? 'Loopback only' : 'Network exposure not reported'}</strong>
								<span className="muted">Mutating routes require the local write token.</span>
							</div>
						</div>
						<div className="timeline-item">
							<ShieldCheck size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>{security.writeTokenRequired ? 'Write token required' : 'Write token not reported'}</strong>
								<span className="muted">Frontend receives the token from FastAPI handshake.</span>
							</div>
						</div>
					</div>
				</Surface>
				<Surface title="Sandbox Posture" kicker="Execution">
					<div className="timeline">
						<div className="timeline-item">
							<ShieldAlert size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>{security.sandboxMode || 'not exposed by API yet'}</strong>
								<span className="muted">Docker status should be surfaced by a read-only endpoint before UI claims high assurance.</span>
							</div>
						</div>
					</div>
				</Surface>
				<Surface title="Risk Categories" kicker="Policy">
					<div className="timeline">
						{['workspace writes', 'installs', 'dangerous shell', 'secrets', 'git writes', 'network-sensitive actions'].map((category) => (
							<div className="timeline-item" key={category}>
								<Badge tone="oxblood" status="approval_required">gated</Badge>
								<div className="timeline-content">
									<strong>{category}</strong>
									<span className="muted">requires individual action approval</span>
								</div>
							</div>
						))}
					</div>
				</Surface>
			</div>
			<Surface title="Approval Requests by Risk" kicker="Policy Queue">
				<DataTable
					rows={actionRequests}
					empty={<EmptyState title="No risky actions pending" body="Action-level requests are created by backend policy before execution." />}
					columns={[
						{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status} tone={toneForStatus(row.status)}>{row.status}</Badge> },
						{ key: 'riskLevel', label: 'Risk' },
						{ key: 'actionType', label: 'Action' },
						{ key: 'command', label: 'Command', render: (row) => <code>{row.command || 'not recorded'}</code> },
						{ key: 'requestedAt', label: 'Requested', render: (row) => formatDate(row.requestedAt) },
					]}
				/>
			</Surface>
			<Surface title="Audit Trail" kicker="Decisions">
				<DataTable
					rows={auditEvents}
					empty={<EmptyState title="No audit entries" body="Approvals, cancellations and retries will be recorded here." />}
					columns={[
						{ key: 'action', label: 'Action' },
						{ key: 'actor', label: 'Actor' },
						{ key: 'target', label: 'Target' },
						{ key: 'createdAt', label: 'Time', render: (row) => formatDate(row.createdAt) },
					]}
				/>
			</Surface>
		</div>
	);
}
