import React from 'react';
import { FileCheck2, FolderGit2, Workflow } from 'lucide-react';

import { Badge, DataTable, EmptyState, Surface, Timeline } from '../../components/primitives.jsx';
import { asArray, formatDate, shortId, toneForStatus } from '../common/format.js';

export function WorkflowsPage({ data }) {
	const overview = data.overview || {};
	const workflows = asArray(overview.workflows);
	const workflowRuns = asArray(overview.workflowRuns);
	const workflowSteps = asArray(overview.workflowSteps);
	const workspaces = asArray(overview.runtimeWorkspaces);
	const evidencePackages = asArray(overview.evidencePackages);
	const activeWorkflow = workflows.find((workflow) => workflow.status === 'running') || workflows[0];
	const activeRuns = workflowRuns.filter((run) => run.workflowId === activeWorkflow?.id);
	const activeRunIds = new Set(activeRuns.map((run) => run.id));
	const activeSteps = workflowSteps.filter((step) => activeRunIds.has(step.workflowRunId));
	const linkedWorkspaces = workspaces.filter((workspace) => activeRunIds.has(workspace.workflowRunId));
	const linkedEvidence = evidencePackages.filter((evidence) => activeRunIds.has(evidence.workflowRunId));

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Workflows</p>
				<h1 className="editorial-title">Idea-to-release runs stay inspectable across steps, workspaces and evidence.</h1>
			</div>
			<div className="split-pane" data-motion-item>
				<Surface title="Workflow Ledger" kicker="Runs">
					<DataTable
						rows={workflows}
						empty={<EmptyState title="No workflows" body="Create or start a workflow to populate this ledger." />}
						columns={[
							{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status} tone={toneForStatus(row.status)}>{row.status}</Badge> },
							{ key: 'title', label: 'Title' },
							{ key: 'kind', label: 'Kind' },
							{ key: 'updatedAt', label: 'Updated', render: (row) => formatDate(row.updatedAt) },
						]}
					/>
				</Surface>
				<Surface title="Step Timeline" kicker={activeWorkflow?.title || 'No active workflow'}>
					{activeSteps.length ? (
						<Timeline
							items={activeSteps.map((step) => ({
								id: step.id,
								type: step.name,
								status: step.status,
								body: `agent ${shortId(step.agentProfileId)} · run ${shortId(step.workflowRunId)}`,
								createdAt: formatDate(step.updatedAt),
							}))}
						/>
					) : (
						<EmptyState title="No workflow steps" body="Starting a workflow creates the canonical SDLC step sequence." />
					)}
				</Surface>
			</div>
			<div className="section-grid" data-motion-item>
				<Surface title="Linked Workspaces" kicker="Isolation">
					<div className="timeline">
						{linkedWorkspaces.map((workspace) => (
							<div className="timeline-item" key={workspace.id}>
								<FolderGit2 size={18} aria-hidden="true" />
								<div className="timeline-content">
									<strong>{workspace.taskId}</strong>
									<span className="muted">{workspace.isolationType} · {workspace.status}</span>
									<span className="subtle mono">{workspace.path}</span>
								</div>
							</div>
						))}
						{!linkedWorkspaces.length ? <EmptyState title="No linked workspaces" body="Workspace allocation with workflow IDs will appear here." /> : null}
					</div>
				</Surface>
				<Surface title="Evidence Packages" kicker="QA">
					<div className="timeline">
						{linkedEvidence.map((evidence) => (
							<div className="timeline-item" key={evidence.id}>
								<FileCheck2 size={18} aria-hidden="true" />
								<div className="timeline-content">
									<strong>{evidence.taskId}</strong>
									<span className="muted">{evidence.qaVerdict} · {asArray(evidence.testResults).length} test records</span>
								</div>
							</div>
						))}
						{!linkedEvidence.length ? <EmptyState title="No linked evidence" body="QA packages linked to workflow runs will appear here." /> : null}
					</div>
				</Surface>
				<Surface title="Run Inventory" kicker="State">
					<DataTable
						rows={activeRuns}
						empty={<EmptyState title="No workflow runs" body="Start a workflow to create a run." />}
						columns={[
							{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status}>{row.status}</Badge> },
							{ key: 'id', label: 'Run', render: (row) => <code>{shortId(row.id)}</code> },
							{ key: 'startedAt', label: 'Started', render: (row) => formatDate(row.startedAt) },
						]}
					/>
				</Surface>
			</div>
		</div>
	);
}
