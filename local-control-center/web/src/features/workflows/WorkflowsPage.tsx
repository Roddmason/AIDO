import { Background, Controls, ReactFlow, type Edge, type Node } from '@xyflow/react';
import { useEffect, useMemo, useState } from 'react';

import { downloadEvidenceArtifact, fetchEvidenceArtifact, type ArtifactPayload } from '../../api/client';
import type { Artifact, Overview, WorkflowStep } from '../../api/types';
import { Badge, DataTable, Drawer, EmptyState, PageHeader, StatusDot, Surface } from '../../components/primitives';
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

function objectValue(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

type WorkflowLinks = {
	runs: Overview['workflowRuns'];
	steps: Overview['workflowSteps'];
	events: Overview['workflowEvents'];
	workspaces: Overview['runtimeWorkspaces'];
	jobs: Overview['jobs'];
	agentRuns: Overview['agentRuns'];
	toolCalls: Overview['agentToolCalls'];
	permissionDecisions: Overview['permissionDecisions'];
	evidence: Overview['evidencePackages'];
	artifacts: Overview['artifacts'];
	testResults: Overview['testResultRecords'];
	approvals: Overview['actionRequests'];
};

type WorkflowTimelineItem = {
	id: string;
	label: string;
	status: string;
	tone: ReturnType<typeof toneForStatus>;
	detail: string;
	source: string;
	createdAt: string;
	sortKey: number;
};

const GATE_TO_STEP: Record<string, string> = {
	DeveloperAgent: 'developer_agent',
	QAAgent: 'qa_validation',
	SecurityAgent: 'security_review',
	ArchitectAgent: 'architecture_review',
	DevOpsAgent: 'devops_validation',
};

function stringValue(value: unknown, fallback = ''): string {
	return typeof value === 'string' && value.trim() ? value : fallback;
}

function sortTime(value?: string | null, fallback = 0): number {
	if (!value) return fallback;
	const parsed = Date.parse(value);
	return Number.isFinite(parsed) ? parsed : fallback;
}

function workflowEventStatus(severity?: string): string {
	if (severity === 'warning') return 'warning';
	if (severity === 'error') return 'error';
	if (severity === 'critical') return 'critical';
	return severity || 'info';
}

function workflowEventDetail(payload: Record<string, unknown>, fallback: string): string {
	const reason = stringValue(payload.reason);
	if (reason) return reason;
	const blockedGate = stringValue(payload.blockedGate);
	if (blockedGate) return `blocked gate: ${blockedGate}`;
	const gate = stringValue(payload.gate);
	if (gate) return `gate: ${gate}`;
	const status = stringValue(payload.status);
	if (status) return `status: ${status}`;
	const workflowStepId = stringValue(payload.workflowStepId);
	if (workflowStepId) return `step: ${shortId(workflowStepId)}`;
	return fallback;
}

function stepDetail(
	step: Overview['workflowSteps'][number],
	gateResult: Record<string, unknown> | undefined,
	evidence: Overview['evidencePackages'][number] | undefined,
	agentRun: Overview['agentRuns'][number] | undefined,
	job: Overview['jobs'][number] | undefined,
): string {
	const output = objectValue(step.output);
	const metadata = objectValue(step.metadata);
	const gateReason = stringValue(gateResult?.reason);
	if (gateReason) return gateReason;
	const outputReason = stringValue(output.reason);
	if (outputReason) return outputReason;
	if (evidence?.qaVerdict) return `evidence: ${evidence.qaVerdict}`;
	if (agentRun?.status) return `agent run: ${agentRun.status}`;
	if (job?.status) return `job: ${job.status}`;
	const gateState = stringValue(metadata.gateState);
	if (gateState) return gateState;
	return stringValue(step.taskType, 'waiting for linked execution records');
}

function latestRun(linked: WorkflowLinks): Overview['workflowRuns'][number] | undefined {
	return linked.runs
		.slice()
		.sort((left, right) => sortTime(String(right.startedAt ?? '')) - sortTime(String(left.startedAt ?? '')))[0];
}

function buildWorkflowTimeline(linked: WorkflowLinks): WorkflowTimelineItem[] {
	const run = latestRun(linked);
	const runId = String(run?.id ?? '');
	const steps = runId ? linked.steps.filter((step) => String(step.workflowRunId ?? '') === runId) : linked.steps;
	const events = runId ? linked.events.filter((event) => String(event.workflowRunId ?? '') === runId) : linked.events;
	const evidenceByStep = new Map(
		linked.evidence
			.filter((item) => !runId || String(item.workflowRunId ?? '') === runId)
			.map((item) => [String(item.workflowStepId ?? ''), item]),
	);
	const agentRunByStep = new Map(
		linked.agentRuns
			.filter((item) => !runId || String(item.workflowRunId ?? '') === runId)
			.map((item) => [String(item.workflowStepId ?? ''), item]),
	);
	const jobByStep = new Map(
		linked.jobs
			.filter((item) => !runId || String(item.workflowRunId ?? '') === runId)
			.map((item) => [String(item.workflowStepId ?? ''), item]),
	);
	const runMetadata = objectValue(run?.metadata);
	const gateResults = Array.isArray(runMetadata.gateResults)
		? runMetadata.gateResults.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
		: [];
	const gateByStep = new Map<string, Record<string, unknown>>();
	for (const gate of gateResults) {
		const stepName = GATE_TO_STEP[stringValue(gate.name)];
		if (stepName) gateByStep.set(stepName, gate);
	}
	const stepItems = steps
		.slice()
		.sort((left, right) => Number(objectValue(left.metadata).order ?? 0) - Number(objectValue(right.metadata).order ?? 0))
		.map((step, index) => {
			const gateResult = gateByStep.get(step.name);
			const evidence = evidenceByStep.get(step.id);
			const agentRun = agentRunByStep.get(step.id);
			const job = jobByStep.get(step.id);
			const status = stringValue(gateResult?.status, step.status);
			return {
				id: `step-${step.id}`,
				label: step.name,
				status,
				tone: toneForStatus(status),
				detail: stepDetail(step, gateResult, evidence, agentRun, job),
				source: `step ${index + 1}`,
				createdAt: step.updatedAt,
				sortKey: sortTime(step.createdAt) + index,
			};
		});
	const eventItems = events.map((event, index) => {
		const status = workflowEventStatus(event.severity);
		return {
			id: `event-${event.id}`,
			label: event.type,
			status,
			tone: toneForStatus(status),
			detail: workflowEventDetail(objectValue(event.payload), event.severity),
			source: 'workflow event',
			createdAt: event.createdAt,
			sortKey: sortTime(event.createdAt) + index / 100,
		};
	});
	return [...stepItems, ...eventItems].sort((left, right) => left.sortKey - right.sortKey);
}

function WorkflowTimeline({ items }: { items: WorkflowTimelineItem[] }) {
	if (!items.length) {
		return <EmptyState title="No workflow timeline" body="Run-linked workflow events and step evidence have not been recorded." />;
	}
	return (
		<ol className="workflow-timeline" aria-label="Workflow timeline">
			{items.map((item) => (
				<li className="workflow-timeline-item" key={item.id}>
					<StatusDot tone={item.tone} />
					<div className="workflow-timeline-content">
						<div className="workflow-timeline-heading">
							<strong>{item.label}</strong>
							<Badge tone={item.tone}>{item.status}</Badge>
						</div>
						<p className="workflow-timeline-detail">{item.detail}</p>
						<div className="workflow-timeline-meta">
							<span className="mono">{item.source}</span>
							<span className="mono">{item.createdAt ? new Date(item.createdAt).toLocaleString() : 'time unavailable'}</span>
						</div>
					</div>
				</li>
			))}
		</ol>
	);
}

export function WorkflowsPage({ overview, token }: { overview: Overview; token: string }) {
	const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
	const [previewArtifact, setPreviewArtifact] = useState<Artifact | null>(null);
	const [previewPayload, setPreviewPayload] = useState<ArtifactPayload | null>(null);
	const [previewLoadingId, setPreviewLoadingId] = useState('');
	const [downloadLoadingId, setDownloadLoadingId] = useState('');
	const [previewError, setPreviewError] = useState('');
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
	const openPreview = async (artifact: Artifact) => {
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
	const downloadArtifact = async (artifact: Artifact) => {
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setPreviewError('Artifact metadata is incomplete.');
			return;
		}
		setPreviewError('');
		setDownloadLoadingId(artifactId);
		try {
			await downloadEvidenceArtifact(token, evidenceId, artifactId, artifactDisplayName(artifact));
		} catch (error) {
			setPreviewError(error instanceof Error ? error.message : 'Artifact download failed.');
		} finally {
			setDownloadLoadingId('');
		}
	};
	const selectedWorkflow = overview.workflows.find((workflow) => workflow.id === selectedWorkflowId) ?? overview.workflows[0];
	const linked = useMemo<WorkflowLinks>(() => {
		if (!selectedWorkflow) {
			return {
				runs: [],
				steps: [],
				events: [],
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
			events: overview.workflowEvents.filter(
				(event) => String(event.workflowId ?? '') === selectedWorkflow.id || runIds.has(String(event.workflowRunId ?? '')),
			),
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
	const nodes = nodesFromSteps(linked.steps);
	const edges = edgesFromNodes(nodes);
	const timelineItems = useMemo(() => buildWorkflowTimeline(linked), [linked]);
	return (
		<>
			<PageHeader
				kicker="SDLC graph"
				title="Workflows"
				summary="Durable workflow runs with steps, evidence and permission checkpoints represented as operational state."
			/>
			<Surface title="Workflow graph">
				{selectedWorkflow && nodes.length ? (
					<div className="flow-board" aria-label={`Workflow graph for ${selectedWorkflow.title}`}>
						<ReactFlow nodes={nodes} edges={edges} fitView>
							<Background />
							<Controls />
						</ReactFlow>
					</div>
				) : (
					<EmptyState title="No workflow steps" body="Select or start a workflow run before reading a graph." />
				)}
			</Surface>
			<div className="grid two">
				<Surface title="Workflow catalog">
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
				<Surface title="Workflow runs">
					<DataTable
						rows={linked.runs}
						empty={<EmptyState title="No workflow runs" body="Starting a workflow creates a run record." />}
						columns={[
							{ key: 'run', label: 'Run', render: (row) => <span className="mono">{shortId(String(row.id ?? ''))}</span> },
							{
								key: 'workflow',
								label: 'Workflow',
								render: (row) => <span className="mono">{shortId(String(row.workflowId ?? ''))}</span>,
							},
							{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
						]}
					/>
				</Surface>
				<Surface title="Selected workflow steps">
					<DataTable
						rows={linked.steps}
						empty={<EmptyState title="No selected workflow steps" body="Inspect a workflow with a run to see its step plan." />}
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
							<Surface title="Workflow timeline" flat>
								<WorkflowTimeline items={timelineItems} />
							</Surface>
							<Surface title="Runtime and run state" flat>
								<DataTable rows={linked.runs} empty={<EmptyState title="No workflow runs" body="A run record appears after workflow execution starts." />} columns={[
									{ key: 'run', label: 'Run', render: (row) => <span className="mono">{shortId(String(row.id ?? ''))}</span> },
									{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
									{
										key: 'runtime',
										label: 'Runtime',
										render: (row) => {
											const metadata = objectValue(row.metadata);
											const runtime = objectValue(metadata.runtime);
											return <span className="mono">{String(runtime.id ?? 'not selected')}</span>;
										},
									},
									{
										key: 'qa',
										label: 'QA verdict',
										render: (row) => {
											const metadata = objectValue(row.metadata);
											return <Badge tone={toneForStatus(String(metadata.qaVerdict ?? 'not_run'))}>{String(metadata.qaVerdict ?? 'not_run')}</Badge>;
										},
									},
									{
										key: 'diff',
										label: 'Diff',
										render: (row) => {
											const metadata = objectValue(row.metadata);
											const diff = objectValue(metadata.diffSummary);
											const changedFiles = Array.isArray(diff.changedFiles) ? diff.changedFiles.length : Number(diff.changedFiles ?? 0);
											return <span className="mono">{String(diff.state ?? 'not captured')} / {Number.isFinite(changedFiles) ? changedFiles : 0} files</span>;
										},
									},
								]} />
								<DataTable rows={linked.agentRuns} empty={<EmptyState title="No agent runs" body="Agent run records appear after runtime selection." />} columns={[
									{ key: 'agent', label: 'Agent run', render: (row) => <span className="mono">{shortId(String(row.id ?? ''))}</span> },
									{
										key: 'profile',
										label: 'Profile',
										render: (row) => {
											const metadata = objectValue(row.metadata);
											return <span className="mono">{String(metadata.agentProfileId ?? metadata.runtimeType ?? 'unknown')}</span>;
										},
									},
									{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
								]} />
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
									{ key: 'source', label: 'Source', render: (row) => <span className="mono">{String(row.evidenceSource ?? 'operator_attested')}</span> },
									{ key: 'diffs', label: 'Diff refs', render: (row) => String(Array.isArray(row.diffRefs) ? row.diffRefs.length : 0) },
									{
										key: 'completeness',
										label: 'Completeness',
										render: (row) => {
											const hasQa = Array.isArray(row.testResults) && row.testResults.length > 0;
											const hasDiffRefs = Array.isArray(row.diffRefs) && row.diffRefs.length > 0;
											return <span className="mono">{hasQa && hasDiffRefs ? 'complete' : 'partial'}</span>;
										},
									},
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
											const downloading = downloadLoadingId === String(row.id ?? '');
											return (
												<div className="inline" aria-busy={loading || downloading}>
													<button className="button" type="button" aria-label={`Preview workflow artifact ${name}`} disabled={loading} onClick={() => void openPreview(row)}>
														{loading ? 'Opening' : 'Preview'}
													</button>
													<button className="button" type="button" aria-label={`Download workflow artifact ${name}`} disabled={downloading} onClick={() => void downloadArtifact(row)}>
														{downloading ? 'Downloading' : 'Download'}
													</button>
												</div>
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
									{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
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
									{
										key: 'runtime',
										label: 'Runtime',
										render: (row) => {
											const payload = objectValue(row.payload);
											const runtime = objectValue(payload.runtime);
											return <span className="mono">{String(runtime.id ?? 'not selected')}</span>;
										},
									},
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
							{downloadLoadingId ? <div className="sr-only" role="status">Downloading artifact</div> : null}
							{previewPayload?.text ? (
								<pre className="artifact-preview">{previewPayload.text}</pre>
							) : (
								<EmptyState title={previewLoadingId ? 'Loading artifact' : 'Binary or empty artifact'} body="Non-text artifacts remain downloadable, but are not rendered inline." />
							)}
							<button className="button primary" type="button" disabled={downloadLoadingId === String(previewArtifact.id ?? '')} aria-label={`Download workflow preview artifact ${artifactDisplayName(previewArtifact)}`} onClick={() => void downloadArtifact(previewArtifact)}>
								{downloadLoadingId === String(previewArtifact.id ?? '') ? 'Downloading artifact' : 'Download artifact'}
							</button>
						</>
					) : null}
				</div>
			</Drawer>
		</>
	);
}
