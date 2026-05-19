import React from 'react';
import { AlertTriangle, CheckCircle2, Database, GitBranch, ShieldCheck, Workflow } from 'lucide-react';

import { Badge, EmptyState, StatusDot, Surface, Timeline } from '../../components/primitives.jsx';
import { asArray, countBy, formatDate, toneForStatus } from '../common/format.js';

function Metric({ label, value, status, detail }) {
	return (
		<Surface tone="flat" className="metric">
			<div className="command-bar-group">
				<StatusDot status={status} />
				<span className="metric-label">{label}</span>
			</div>
			<strong className="metric-value">{value}</strong>
			{detail ? <span className="muted">{detail}</span> : null}
		</Surface>
	);
}

export function OverviewPage({ data }) {
	const overview = data.overview || {};
	const retrieval = data.retrievalStatus || {};
	const jobs = asArray(overview.jobs);
	const workspaceState = overview.workspaceState || {};
	const actionRequests = asArray(overview.actionRequests);
	const events = asArray(overview.events).slice(0, 8);
	const pendingActions = actionRequests.filter((action) => action.status === 'pending');
	const jobCounts = countBy(jobs, 'status');
	const running = jobCounts.running || 0;
	const queued = jobCounts.queued || 0;
	const failed = jobCounts.failed || 0;
	const retrievalStatus = retrieval.available ? retrieval.backend || 'faiss' : retrieval.degraded ? 'degraded' : 'not indexed';

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Command ledger</p>
				<h1 className="editorial-title">Operate approvals, leases and memory from one local plane.</h1>
			</div>
			<div className="section-grid" data-motion-item>
				<Metric label="Queued jobs" value={queued} status="queued" detail={`${running} running`} />
				<Metric label="Pending approvals" value={pendingActions.length} status={pendingActions.length ? 'approval_required' : 'ok'} detail="granular action requests" />
				<Metric label="Retrieval backend" value={retrievalStatus} status={retrieval.degraded ? 'degraded' : 'ok'} detail={`${retrieval.indexedItems || 0} indexed items`} />
				<Metric label="Worker failures" value={failed} status={failed ? 'failed' : 'ok'} detail="from SQLite job state" />
			</div>
			<div className="wide-grid" data-motion-item>
				<Surface title="Runtime Health" kicker="Python FastAPI" className="span-4">
					<div className="timeline">
						<div className="timeline-item">
							<CheckCircle2 size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>Loopback API</strong>
								<span className="muted">Write token {overview.security?.writeTokenRequired ? 'required' : 'not required'}</span>
							</div>
						</div>
						<div className="timeline-item">
							<ShieldCheck size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>Sandbox posture</strong>
								<span className="muted">{overview.security?.sandboxMode || 'not exposed by API yet'}</span>
							</div>
						</div>
						<div className="timeline-item">
							<Database size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>SQLite canonical store</strong>
								<span className="muted">{asArray(overview.memoryItems).length} memory records</span>
							</div>
						</div>
					</div>
				</Surface>
				<Surface title="Operational Mix" kicker="Queue State" className="span-4">
					<div className="timeline">
						{Object.entries(jobCounts).map(([status, total]) => (
							<div className="timeline-item" key={status}>
								<StatusDot status={status} />
								<div className="timeline-content">
									<strong>{status}</strong>
									<span className="muted mono">{total} jobs</span>
								</div>
							</div>
						))}
						{!Object.keys(jobCounts).length ? <EmptyState title="No jobs yet" body="The queue is empty. Create or start a pipeline to populate this lane." /> : null}
					</div>
				</Surface>
				<Surface title="Capability Posture" kicker="System" className="span-4">
					<div className="timeline">
						<div className="timeline-item">
							<Workflow size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>Pipelines</strong>
								<span className="muted">{asArray(workspaceState.pipelines).length} workspace records exposed by Python</span>
							</div>
						</div>
						<div className="timeline-item">
							<GitBranch size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>Git writes</strong>
								<span className="muted">optional and gated; current folder can remain non-repo</span>
							</div>
						</div>
						<div className="timeline-item">
							<AlertTriangle size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>Dangerous actions</strong>
								<span className="muted">{pendingActions.length ? `${pendingActions.length} require decision` : 'no pending action requests'}</span>
							</div>
						</div>
					</div>
				</Surface>
				<Surface title="Recent Events" kicker="Audit Pulse" className="span-12">
					<Timeline
						items={events.map((event) => ({
							...event,
							body: event.jobId ? `job ${event.jobId}` : JSON.stringify(event.payload || {}),
							createdAt: formatDate(event.createdAt),
							status: event.type?.includes('failed') ? 'failed' : toneForStatus(event.type) || 'queued',
						}))}
					/>
				</Surface>
			</div>
		</div>
	);
}
