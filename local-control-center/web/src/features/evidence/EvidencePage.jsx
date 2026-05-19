import React from 'react';
import { CheckCircle2, FileCheck2 } from 'lucide-react';

import { Badge, DataTable, EmptyState, Surface } from '../../components/primitives.jsx';
import { asArray, formatDate, shortId, toneForStatus } from '../common/format.js';

function flattenTestResults(packages) {
	return packages.flatMap((evidence) =>
		asArray(evidence.testResults).map((result, index) => ({
			id: `${evidence.id}-${index}`,
			evidenceId: evidence.id,
			taskId: evidence.taskId,
			command: result.command || 'not recorded',
			status: result.status || 'unknown',
			durationMs: result.durationMs,
			outputRef: result.outputRef,
			createdAt: evidence.createdAt,
		})),
	);
}

export function EvidencePage({ data }) {
	const overview = data.overview || {};
	const evidencePackages = asArray(overview.evidencePackages);
	const testResults = flattenTestResults(evidencePackages);
	const passed = evidencePackages.filter((evidence) => evidence.qaVerdict === 'passed').length;
	const blocked = evidencePackages.filter((evidence) => ['failed', 'blocked', 'needs_human_review'].includes(evidence.qaVerdict)).length;

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Evidence & QA</p>
				<h1 className="editorial-title">QA decisions require recorded proof, not confidence notes.</h1>
			</div>
			<div className="section-grid" data-motion-item>
				<Surface tone="flat" className="metric">
					<div className="command-bar-group">
						<FileCheck2 size={18} aria-hidden="true" />
						<span className="metric-label">Evidence packages</span>
					</div>
					<strong className="metric-value">{evidencePackages.length}</strong>
					<span className="muted">{passed} passed</span>
				</Surface>
				<Surface tone="flat" className="metric">
					<div className="command-bar-group">
						<CheckCircle2 size={18} aria-hidden="true" />
						<span className="metric-label">Test results</span>
					</div>
					<strong className="metric-value">{testResults.length}</strong>
					<span className="muted">{blocked} blocked or failed packages</span>
				</Surface>
			</div>
			<Surface title="Evidence Packages" kicker="QA Verdicts">
				<DataTable
					rows={evidencePackages}
					empty={<EmptyState title="No evidence packages" body="QA evidence created by workflows and agents will appear here." />}
					columns={[
						{ key: 'qaVerdict', label: 'Verdict', render: (row) => <Badge status={row.qaVerdict} tone={toneForStatus(row.qaVerdict)}>{row.qaVerdict}</Badge> },
						{ key: 'taskId', label: 'Task' },
						{ key: 'workflowRunId', label: 'Workflow Run', render: (row) => <code>{shortId(row.workflowRunId)}</code> },
						{ key: 'testPlan', label: 'Plan' },
						{ key: 'createdAt', label: 'Created', render: (row) => formatDate(row.createdAt) },
					]}
				/>
			</Surface>
			<Surface title="Persisted Test Results" kicker="SQLite">
				<DataTable
					rows={testResults}
					empty={<EmptyState title="No test result records" body="Passed QA requires test results, diff refs or screenshot refs." />}
					columns={[
						{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status}>{row.status}</Badge> },
						{ key: 'taskId', label: 'Task' },
						{ key: 'command', label: 'Command', render: (row) => <code>{row.command}</code> },
						{ key: 'durationMs', label: 'Duration', render: (row) => (row.durationMs ? `${row.durationMs}ms` : 'not recorded') },
						{ key: 'evidenceId', label: 'Evidence', render: (row) => <code>{shortId(row.evidenceId)}</code> },
					]}
				/>
			</Surface>
		</div>
	);
}
