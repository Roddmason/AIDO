import React from 'react';
import { AlertTriangle, Landmark, ListChecks } from 'lucide-react';

import { Badge, DataTable, EmptyState, Surface } from '../../components/primitives.jsx';
import { asArray, formatDate, shortId, toneForStatus } from '../common/format.js';

function severityTone(severity) {
	if (['critical', 'high'].includes(severity)) return 'oxblood';
	if (severity === 'medium') return 'amber';
	return 'moss';
}

function priorityTone(priority) {
	if (priority === 'urgent' || priority === 'high') return 'oxblood';
	if (priority === 'medium') return 'amber';
	return 'moss';
}

function linkedIds(ids, emptyLabel = 'none') {
	const values = asArray(ids);
	if (!values.length) return <span className="muted">{emptyLabel}</span>;
	return (
		<div className="link-list">
			{values.map((id) => (
				<code key={id}>{shortId(id)}</code>
			))}
		</div>
	);
}

export function GovernancePage({ data }) {
	const overview = data.overview || {};
	const decisions = asArray(overview.architectureDecisions);
	const risks = asArray(overview.riskRegister);
	const nextSteps = asArray(overview.nextSteps);
	const accepted = decisions.filter((decision) => decision.status === 'accepted').length;
	const openSevereRisks = risks.filter((risk) => ['open', 'monitoring'].includes(risk.status) && ['critical', 'high'].includes(risk.severity)).length;
	const activeSteps = nextSteps.filter((step) => ['planned', 'in_progress'].includes(step.status)).length;

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Governance Ledger</p>
				<h1 className="editorial-title">Make architecture decisions, risks and next steps operational.</h1>
			</div>
			<div className="section-grid" data-motion-item>
				<Surface tone="flat" className="metric">
					<div className="command-bar-group">
						<Landmark size={18} aria-hidden="true" />
						<span className="metric-label">Architecture decisions</span>
					</div>
					<strong className="metric-value">{decisions.length}</strong>
					<span className="muted">{accepted} accepted</span>
				</Surface>
				<Surface tone="flat" className="metric">
					<div className="command-bar-group">
						<AlertTriangle size={18} aria-hidden="true" />
						<span className="metric-label">Open high risks</span>
					</div>
					<strong className="metric-value">{openSevereRisks}</strong>
					<span className="muted">high and critical risks require mitigation</span>
				</Surface>
				<Surface tone="flat" className="metric">
					<div className="command-bar-group">
						<ListChecks size={18} aria-hidden="true" />
						<span className="metric-label">Active next steps</span>
					</div>
					<strong className="metric-value">{activeSteps}</strong>
					<span className="muted">{nextSteps.length} total recorded</span>
				</Surface>
			</div>
			<Surface title="Architecture Decisions" kicker="ADRs">
				<DataTable
					rows={decisions}
					empty={<EmptyState title="No architecture decisions" body="Accepted ADRs and pending proposals will appear here after backend governance writes." />}
					columns={[
						{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status} tone={toneForStatus(row.status)}>{row.status}</Badge> },
						{ key: 'title', label: 'Decision' },
						{ key: 'decision', label: 'Rationale', render: (row) => row.decision || row.context || 'not recorded' },
						{ key: 'linkedRiskIds', label: 'Linked Risks', render: (row) => linkedIds(row.linkedRiskIds) },
						{ key: 'nextStepIds', label: 'Next Steps', render: (row) => linkedIds(row.nextStepIds) },
						{ key: 'updatedAt', label: 'Updated', render: (row) => formatDate(row.updatedAt) },
					]}
				/>
			</Surface>
			<div className="split-pane" data-motion-item>
				<Surface title="Risk Register" kicker="Mitigations">
					<DataTable
						rows={risks}
						empty={<EmptyState title="No risks recorded" body="Risks should be tracked here before they become hidden project debt." />}
						columns={[
							{ key: 'severity', label: 'Severity', render: (row) => <Badge status={row.severity} tone={severityTone(row.severity)}>{row.severity}</Badge> },
							{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status} tone={toneForStatus(row.status)}>{row.status}</Badge> },
							{ key: 'title', label: 'Risk' },
							{ key: 'mitigation', label: 'Mitigation', render: (row) => row.mitigation || 'missing' },
							{ key: 'owner', label: 'Owner', render: (row) => row.owner || 'unassigned' },
						]}
					/>
				</Surface>
				<Surface title="Next Steps" kicker="Execution">
					<DataTable
						rows={nextSteps}
						empty={<EmptyState title="No next steps" body="Follow-up work linked to risks and ADRs will appear here." />}
						columns={[
							{ key: 'priority', label: 'Priority', render: (row) => <Badge status={row.priority} tone={priorityTone(row.priority)}>{row.priority}</Badge> },
							{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status} tone={toneForStatus(row.status)}>{row.status}</Badge> },
							{ key: 'title', label: 'Next Step' },
							{ key: 'sourceRiskId', label: 'Source Risk', render: (row) => (row.sourceRiskId ? <code>{shortId(row.sourceRiskId)}</code> : 'none') },
							{ key: 'owner', label: 'Owner', render: (row) => row.owner || 'unassigned' },
							{ key: 'dueAt', label: 'Due', render: (row) => formatDate(row.dueAt) },
						]}
					/>
				</Surface>
			</div>
		</div>
	);
}
