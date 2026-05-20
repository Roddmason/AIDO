import { Background, Controls, ReactFlow, type Edge, type Node } from '@xyflow/react';
import { useEffect, useMemo, useState } from 'react';

import { fetchEvidenceArtifact, type ArtifactPayload } from '../../api/client';
import type { Dictionary, Overview, WorkflowStep } from '../../api/types';
import { Badge, DataTable, Drawer, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { artifactDisplayName, artifactMimeType, artifactSizeLabel } from '../../lib/artifacts';
import { shortId, toneForStatus } from '../../lib/format';

function nodesFromSteps(steps: WorkflowStep[]): Node[] {
	return steps.slice(0, 12).map((step, index) => ({
		id: step.id,
		position: { x: (index % 4) * 210, y: Math.floor(index / 4) * 120 },
		data: { label: `${step.name}\n${step.status}` },
		style: {
			border: '1px solid var(--line-strong)',
			borderRadius: '8px',
			background: 'var(--surface-panel-strong)',
			color: 'var(--ink)',
			fontFamily: 'var(--font-data)',
			whiteSpace: 'pre-line',
		},
	}));
}

function edgesFromNodes(nodes: Node[]): Edge[] {
	return nodes.slice(1).map((node, index) => ({
		id: `edge-${nodes[index].id}-${node.id}`,
		source: nodes[index].id,
		target: node.id,
		animated: true,
	}));
}

export function WorkflowsPage({ overview, token }: { overview: Overview; token: string }) {
	const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
	const [previewArtifact, setPreviewArtifact] = useState<Dictionary | null>(null);
	const [previewPayload, setPreviewPayload] = useState<ArtifactPayload | null>(null);
	const [previewLoadingId, setPreviewLoadingId] = useState('');
	const [previewError, setPreviewError] = useState('');
	const nodes = nodesFromSteps(overview.workflowSteps);
	const edges = edgesFromNodes(nodes);
	useEffect(() => {
		const onKeyDown = (event: KeyboardEvent) => {
			if (event.key === 'Escape') {
				if (previewArtifact) {
					setPreviewArtifact(null);
					setPreviewPayload(null);
					setPreviewError('');
					return;
				}
				setSelectedWorkflowId(null);
			}
		};
		window.addEventListener('keydown', onKeyDown);
		return () => window.removeEventListener('keydown', onKeyDown);
	}, [previewArtifact]);
	const openPreview = async (artifact: Dictionary) => {
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setPreviewError('Artifact metadata is incomplete.');
			return;
		}
		setPreviewArtifact(artifact);
		setPreviewPayload(null);
		setPreviewError('');
		setPreviewLoadingId(artifactId);
		try {
			const payload = await fetchEvidenceArtifact(token, evidenceId, artifactId);
			setPreviewPayload(payload);
		} catch (error) {
			setPreviewError(error instanceof Error ? error.message : 'Artifact preview failed.');
		} finally {
			setPreviewLoadingId('');
		}
	};
	const downloadPreview = () => {
		if (!previewArtifact || !previewPayload) return;
		const url = URL.createObjectURL(previewPayload.blob);
		const link = document.createElement('a');
		link.href = url;
		link.download = artifactDisplayName(previewArtifact);
		document.body.appendChild(link);
		link.click();
		link.remove();
		URL.revokeObjectURL(url);
	};
	const selectedWorkflow = overview.workflows.find((workflow) => workflow.id === selectedWorkflowId) ?? overview.workflows[0];
	const linked = useMemo(() => {
		if (!selectedWorkflow) {
			return {
				runs: [],
				steps: [],
				workspaces: [],
				jobs: [],
				agentRuns: [],
				toolCalls: [],
				permissionDecisions: [],
				evidence: [],
				artifacts: [],
				testResults: [],
				approvals: [],
			};
		}
		const runs = overview.workflowRuns.filter((run) => String(run.workflowId ?? '') === selectedWorkflow.id);
		const runIds = new Set(runs.map((run) => String(run.id ?? '')));
		const steps = overview.workflowSteps.filter((step) => step.workflowId === selectedWorkflow.id);
		const stepIds = new Set(steps.map((step) => step.id));
		const jobs = overview.jobs.filter((job) => runIds.has(String(job.payload?.workflowRunId ?? job.workflowRunId ?? '')));
		const jobIds = new Set(jobs.map((job) => job.id));
		const agentRuns = overview.agentRuns.filter((run) => runIds.has(String(run.workflowRunId ?? '')) || stepIds.has(String(run.workflowStepId ?? '')));
		const agentRunIds = new Set(agentRuns.map((run) => String(run.id ?? '')));
		const toolCalls = overview.agentToolCalls.filter((toolCall) => agentRunIds.has(String(toolCall.agentRunId ?? '')));
		const permissionDecisionIds = new Set(
			toolCalls
				.map((toolCall) => {
					const payload = toolCall.payload as Record<string, unknown> | undefined;
					return String(payload?.permissionDecisionId ?? '');
				})
				.filter(Boolean),
		);
		const evidence = overview.evidencePackages.filter((item) => runIds.has(String(item.workflowRunId ?? '')));
		const evidenceIds = new Set(evidence.map((item) => String(item.id ?? '')));
		return {
			runs,
			steps,
			workspaces: overview.runtimeWorkspaces.filter((workspace) => runIds.has(String(workspace.workflowRunId ?? ''))),
			jobs,
			agentRuns,
			toolCalls,
			permissionDecisions: overview.permissionDecisions.filter((decision) => permissionDecisionIds.has(String(decision.id ?? ''))),
			evidence,
			artifacts: overview.artifacts.filter((artifact) => evidenceIds.has(String(artifact.evidencePackageId ?? ''))),
			testResults: overview.testResultRecords.filter((item) => evidenceIds.has(String(item.evidencePackageId ?? ''))),
			approvals: overview.actionRequests.filter((approval) => jobIds.has(approval.jobId)),
		};
	}, [overview, selectedWorkflow]);
	return (
		<>
			<PageHeader
				kicker="SDLC graph"
				title="Workflows"
				summary="Durable workflow runs with steps, evidence and permission checkpoints represented as operational state."
			/>
			<Surface title="Workflow graph">
				<div className="flow-board" aria-label="Workflow graph">
					<ReactFlow nodes={nodes} edges={edges} fitView>
						<Background />
						<Controls />
					</ReactFlow>
				</div>
			</Surface>
			<div className="grid two">
				<Surface title="Runs">
					<DataTable
						rows={overview.workflows}
						empty={<EmptyState title="No workflows" body="Command Center can create a workflow when a project is selected." />}
						columns={[
							{ key: 'title', label: 'Title', render: (row) => row.title },
							{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
							{
								key: 'inspect',
								label: 'Inspect',
								render: (row) => (
									<button className="button" type="button" aria-label={`Inspect workflow ${row.title}`} onClick={() => setSelectedWorkflowId(row.id)}>
										Inspect
									</button>
								),
							},
						]}
					/>
				</Surface>
				<Surface title="Steps">
					<DataTable
						rows={overview.workflowSteps}
						empty={<EmptyState title="No steps" body="Starting a workflow expands the SDLC step plan." />}
						columns={[
							{ key: 'name', label: 'Step', render: (row) => <span className="mono">{row.name}</span> },
							{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
							{ key: 'agent', label: 'Agent', render: (row) => row.agentProfileId ?? 'unassigned' },
						]}
					/>
				</Surface>
			</div>
			<Drawer label="Workflow inspector" open={selectedWorkflowId !== null} onClose={() => setSelectedWorkflowId(null)}>
				<div className="drawer-body">
					{selectedWorkflow ? (
						<>
							<Surface title={selectedWorkflow.title} flat>
								<div className="inline">
									<Badge tone={toneForStatus(selectedWorkflow.status)}>{selectedWorkflow.status}</Badge>
									<span className="mono">{shortId(selectedWorkflow.id)}</span>
									<span className="mono">{selectedWorkflow.kind ?? 'workflow'}</span>
								</div>
							</Surface>
							<Surface title="Steps" flat>
								<DataTable rows={linked.steps} empty={<EmptyState title="No steps" body="Start the workflow to expand steps." />} columns={[
									{ key: 'name', label: 'Step', render: (row) => <span className="mono">{row.name}</span> },
									{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
								]} />
							</Surface>
							<Surface title="Evidence and tests" flat>
								<DataTable rows={linked.evidence} empty={<EmptyState title="No evidence" body="QA packages linked to this workflow run appear here." />} columns={[
									{ key: 'task', label: 'Task', render: (row) => String(row.taskId ?? '') },
									{ key: 'verdict', label: 'Verdict', render: (row) => <Badge tone={toneForStatus(String(row.qaVerdict ?? ''))}>{String(row.qaVerdict ?? '')}</Badge> },
								]} />
								<DataTable rows={linked.testResults} empty={<EmptyState title="No test records" body="Test results appear after evidence ingestion." />} columns={[
									{ key: 'command', label: 'Command', render: (row) => <span className="mono">{String(row.command ?? '')}</span> },
									{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
								]} />
							</Surface>
							<Surface title="Artifacts" flat>
								<DataTable rows={linked.artifacts} empty={<EmptyState title="No artifacts" body="Logs, reports and screenshots linked to evidence appear here." />} columns={[
									{
										key: 'name',
										label: 'Name',
										render: (row) => <span className="mono">{artifactDisplayName(row)}</span>,
									},
									{ key: 'kind', label: 'Kind', render: (row) => <Badge>{String(row.kind ?? 'artifact')}</Badge> },
									{ key: 'size', label: 'Size', render: (row) => artifactSizeLabel(row) },
									{ key: 'hash', label: 'Hash', render: (row) => <span className="mono">{shortId(String(row.hash ?? ''))}</span> },
									{
										key: 'action',
										label: 'Action',
										render: (row) => {
											const name = artifactDisplayName(row);
											const loading = previewLoadingId === String(row.id ?? '');
											return (
												<button className="button" type="button" aria-label={`Preview workflow artifact ${name}`} disabled={loading} onClick={() => void openPreview(row)}>
													{loading ? 'Opening' : 'Preview'}
												</button>
											);
										},
									},
								]} />
							</Surface>
							<Surface title="Policy decisions" flat>
								<DataTable rows={linked.permissionDecisions} empty={<EmptyState title="No policy decisions" body="Policy decisions linked through workflow tool calls appear here." />} columns={[
									{ key: 'decision', label: 'Decision', render: (row) => <Badge tone={toneForStatus(String(row.decision ?? ''))}>{String(row.decision ?? '')}</Badge> },
									{ key: 'risk', label: 'Risk', render: (row) => <Badge tone={toneForStatus(String(row.riskLevel ?? ''))}>{String(row.riskLevel ?? '')}</Badge> },
									{
										key: 'categories',
										label: 'Categories',
										render: (row) => {
											const payload = row.payload as Record<string, unknown> | undefined;
											const categories = Array.isArray(payload?.categories) ? payload.categories.join(', ') : '';
											return <span className="mono">{categories}</span>;
										},
									},
									{ key: 'reason', label: 'Reason', render: (row) => String(row.reason ?? '') },
								]} />
							</Surface>
							<Surface title="Tool calls and approvals" flat>
								<DataTable rows={linked.toolCalls} empty={<EmptyState title="No tool calls" body="Agent runtime calls linked to this workflow appear here." />} columns={[
									{ key: 'tool', label: 'Tool', render: (row) => <span className="mono">{String(row.toolName ?? '')}</span> },
									{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
									{ key: 'command', label: 'Command', render: (row) => {
										const payload = row.payload as Record<string, unknown> | undefined;
										return <span className="mono">{String(payload?.command ?? '')}</span>;
									} },
								]} />
								<DataTable rows={linked.approvals} empty={<EmptyState title="No approvals" body="Granular approvals linked to workflow jobs appear here." />} columns={[
									{ key: 'action', label: 'Action', render: (row) => <span className="mono">{row.actionType}</span> },
									{ key: 'risk', label: 'Risk', render: (row) => <Badge tone={toneForStatus(row.riskLevel)}>{row.riskLevel}</Badge> },
								]} />
							</Surface>
							<Surface title="Workspaces and jobs" flat>
								<DataTable rows={linked.workspaces} empty={<EmptyState title="No workspaces" body="Workspace allocations appear after implementation steps." />} columns={[
									{ key: 'task', label: 'Task', render: (row) => <span className="mono">{String(row.taskId ?? '')}</span> },
									{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
								]} />
								<DataTable rows={linked.jobs} empty={<EmptyState title="No jobs" body="Jobs linked to workflow runs appear here." />} columns={[
									{ key: 'kind', label: 'Kind', render: (row) => <span className="mono">{row.kind}</span> },
									{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
								]} />
							</Surface>
						</>
					) : (
						<EmptyState title="No workflow selected" body="Choose a workflow to inspect its linked records." />
					)}
				</div>
			</Drawer>
			<Drawer label="Workflow artifact preview" open={Boolean(previewArtifact)} onClose={() => {
				setPreviewArtifact(null);
				setPreviewPayload(null);
				setPreviewError('');
			}}>
				<div className="drawer-body">
					{previewArtifact ? (
						<>
							<div className="stack">
								<div className="inline">
									<Badge>{String(previewArtifact.kind ?? 'artifact')}</Badge>
									<Badge>{artifactMimeType(previewArtifact, previewPayload)}</Badge>
									<Badge>{artifactSizeLabel(previewArtifact)}</Badge>
								</div>
								<h3 className="artifact-title">{artifactDisplayName(previewArtifact)}</h3>
								<div className="mono">sha256 {String(previewPayload?.hash || previewArtifact.hash || 'not recorded')}</div>
							</div>
							{previewError ? <div className="form-error" role="alert">{previewError}</div> : null}
							{previewPayload?.text ? (
								<pre className="artifact-preview">{previewPayload.text}</pre>
							) : (
								<EmptyState title={previewLoadingId ? 'Loading artifact' : 'Binary or empty artifact'} body="Non-text artifacts remain downloadable, but are not rendered inline." />
							)}
							<button className="button primary" type="button" disabled={!previewPayload} aria-label={`Download workflow artifact ${artifactDisplayName(previewArtifact)}`} onClick={downloadPreview}>
								Download artifact
							</button>
						</>
					) : null}
				</div>
			</Drawer>
		</>
	);
}
