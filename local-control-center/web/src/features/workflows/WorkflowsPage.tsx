/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { Background, Controls, ReactFlow, type Edge, type Node } from '@xyflow/react';
import { useEffect, useMemo, useState } from 'react';

import { cancelJob, downloadEvidenceArtifact, fetchEvidenceArtifact, retryJob, type ArtifactPayload } from '../../api/client';
import type { Artifact, Overview, WorkflowStep } from '../../api/types';
import { Badge, DataTable, Drawer, EmptyState, PageHeader, StatusDot, Surface } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { artifactDisplayName, artifactMimeType, artifactSizeLabel } from '../../lib/artifacts';
import { findPatchArtifact } from '../../lib/diff';
import { shortId, toneForStatus } from '../../lib/format';
import { redactVisibleText } from '../../lib/redaction';

function nodesFromSteps(steps: WorkflowStep[]): Node[] {
	return steps.slice(0, 12).map((step, index) => ({
		id: step.id,
		position: { x: (index % 4) * 210, y: Math.floor(index / 4) * 120 },
		data: { label: `${step.name}\n${step.status}` },
		style: {
			border: '1px solid var(--color-border-strong)',
			borderRadius: '8px',
			background: 'var(--color-surface-panel-raised)',
			color: 'var(--color-text-primary)',
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
	jobRuns: Overview['jobRuns'];
	agentRuns: Overview['agentRuns'];
	toolCalls: Overview['agentToolCalls'];
	modelCalls: Overview['modelCalls'];
	permissionDecisions: Overview['permissionDecisions'];
	evidence: Overview['evidencePackages'];
	artifacts: Overview['artifacts'];
	testResults: Overview['testResultRecords'];
	approvals: Overview['actionRequests'];
	auditEvents: Overview['auditEvents'];
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

type WorkflowBlocker = {
	id: string;
	source: string;
	status: string;
	reason: string;
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

function finiteNumber(value: unknown): number | null {
	if (typeof value === 'number') {
		return Number.isFinite(value) ? value : null;
	}
	if (typeof value === 'string' && value.trim().length > 0) {
		const parsed = Number(value);
		return Number.isFinite(parsed) ? parsed : null;
	}
	return null;
}

function tokenLabel(value: unknown, unknownLabel = 'unknown'): string {
	const valueNumber = finiteNumber(value);
	return valueNumber === null ? unknownLabel : String(valueNumber);
}

function costLabel(value: unknown, unknownLabel = 'unknown'): string {
	const valueNumber = finiteNumber(value);
	return valueNumber === null ? unknownLabel : `$${valueNumber.toFixed(4)}`;
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

function auditEventStatus(action: string, payload: Record<string, unknown>): string {
	const status = stringValue(payload.status);
	if (status) return status;
	const parts = action.split('.');
	return parts[parts.length - 1] || 'recorded';
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

function statusIsPassed(value: unknown): boolean {
	return ['passed', 'completed', 'approved', 'pr_created'].includes(String(value ?? '').toLowerCase());
}

function hasDiffEvidence(linked: WorkflowLinks): boolean {
	if (findPatchArtifact(linked.artifacts)) return true;
	return linked.evidence.some((item) => Array.isArray(item.diffRefs) && item.diffRefs.length > 0);
}

function hasPassingQaEvidence(linked: WorkflowLinks): boolean {
	if (linked.testResults.some((item) => statusIsPassed(item.status))) return true;
	return linked.evidence.some((item) =>
		Array.isArray(item.testResults) && item.testResults.some((result) => statusIsPassed(objectValue(result).status)),
	);
}

function completionMissing(linked: WorkflowLinks): string[] {
	const missing: string[] = [];
	const run = latestRun(linked);
	if (!run) return ['No workflow run recorded.'];
	if (!linked.steps.length) missing.push('No workflow steps recorded.');
	const unfinishedSteps = linked.steps.filter((step) => !statusIsPassed(step.status) && step.status !== 'skipped');
	if (unfinishedSteps.length) {
		missing.push(`Unfinished steps: ${unfinishedSteps.map((step) => step.name).join(', ')}.`);
	}
	if (!linked.evidence.length) missing.push('No evidence package recorded.');
	if (!hasDiffEvidence(linked)) missing.push('No diff evidence refs recorded.');
	if (!hasPassingQaEvidence(linked)) missing.push('No passing QA test results recorded.');
	const pendingApprovals = linked.approvals.filter((approval) => approval.status === 'pending');
	if (pendingApprovals.length) missing.push(`Pending approvals: ${pendingApprovals.length}.`);
	const blockingStatuses = new Set(['blocked', 'runtime_unavailable', 'qa_failed', 'failed']);
	if (blockingStatuses.has(String(run.status ?? ''))) missing.push(`Run status is ${String(run.status)}.`);
	return Array.from(new Set(missing));
}

function blockerReason(payload: Record<string, unknown>, fallback: string): string {
	return stringValue(payload.reason) || stringValue(payload.blockedReason) || stringValue(payload.error) || fallback;
}

function workflowBlockers(linked: WorkflowLinks): WorkflowBlocker[] {
	const blockers: WorkflowBlocker[] = [];
	const blockingStatuses = new Set(['blocked', 'runtime_unavailable', 'qa_failed', 'failed', 'denied', 'error', 'critical']);
	for (const run of linked.runs) {
		if (blockingStatuses.has(String(run.status ?? ''))) {
			blockers.push({
				id: `run-${run.id}`,
				source: `run ${shortId(String(run.id ?? ''))}`,
				status: String(run.status ?? ''),
				reason: blockerReason(objectValue(run.metadata), `Run status is ${String(run.status ?? '')}.`),
			});
		}
	}
	for (const step of linked.steps) {
		if (blockingStatuses.has(String(step.status ?? ''))) {
			blockers.push({
				id: `step-${step.id}`,
				source: `step ${step.name}`,
				status: step.status,
				reason: blockerReason(objectValue(step.output), blockerReason(objectValue(step.metadata), `Step status is ${step.status}.`)),
			});
		}
	}
	for (const event of linked.events) {
		if (['warning', 'error', 'critical'].includes(String(event.severity ?? ''))) {
			blockers.push({
				id: `event-${event.id}`,
				source: event.type,
				status: String(event.severity ?? ''),
				reason: blockerReason(objectValue(event.payload), event.type),
			});
		}
	}
	for (const job of linked.jobs) {
		if (blockingStatuses.has(String(job.status ?? ''))) {
			blockers.push({
				id: `job-${job.id}`,
				source: `job ${job.kind}`,
				status: job.status,
				reason: blockerReason(objectValue(job.payload), `Job status is ${job.status}.`),
			});
		}
	}
	const seen = new Set<string>();
	return blockers.filter((blocker) => {
		const key = `${blocker.source}:${blocker.status}:${blocker.reason}`;
		if (seen.has(key)) return false;
		seen.add(key);
		return true;
	});
}

function pullRequestUrlFrom(value: unknown): string {
	const payload = objectValue(value);
	const pullRequest = objectValue(payload.pullRequest);
	return stringValue(pullRequest.htmlUrl) || stringValue(pullRequest.html_url) || stringValue(pullRequest.url);
}

function workflowPullRequestUrls(linked: WorkflowLinks): string[] {
	const urls = [
		...linked.runs.map((run) => pullRequestUrlFrom(run.metadata)),
		...linked.auditEvents.map((event) => pullRequestUrlFrom(event.payload)),
		...linked.events.map((event) => pullRequestUrlFrom(event.payload)),
	].filter(Boolean);
	return Array.from(new Set(urls));
}

function buildWorkflowTimeline(linked: WorkflowLinks): WorkflowTimelineItem[] {
	const run = latestRun(linked);
	const runId = String(run?.id ?? '');
	const steps = runId ? linked.steps.filter((step) => String(step.workflowRunId ?? '') === runId) : linked.steps;
	const events = runId ? linked.events.filter((event) => String(event.workflowRunId ?? '') === runId) : linked.events;
	const auditEvents = runId
		? linked.auditEvents.filter((event) => String(event.target ?? '') === runId || stringValue(objectValue(event.payload).workflowRunId) === runId)
		: linked.auditEvents;
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
	const auditItems = auditEvents.map((event, index) => {
		const payload = objectValue(event.payload);
		const status = auditEventStatus(event.action, payload);
		return {
			id: `audit-${event.id}`,
			label: event.action,
			status,
			tone: toneForStatus(status),
			detail: workflowEventDetail(payload, status),
			source: 'audit event',
			createdAt: event.createdAt,
			sortKey: sortTime(event.createdAt) + index / 100 + 0.5,
		};
	});
	return [...stepItems, ...eventItems, ...auditItems].sort((left, right) => left.sortKey - right.sortKey);
}

function WorkflowTimeline({ items }: { items: WorkflowTimelineItem[] }) {
	const { t } = useI18n();
	if (!items.length) {
		return <EmptyState title={t('ui.static.no.workflow.timeline.workflows.2d8544e4', 'No workflow timeline')} body={t('ui.static.run.linked.workflow.events.and.step.evidence.have.not.been.recorded.6ad61027', 'Run-linked workflow events and step evidence have not been recorded.')} />;
	}
	return (
		<ol className="workflow-timeline" aria-label={t('ui.static.workflow.timeline.c2b65038', 'Workflow timeline')}>
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

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;

export function WorkflowsPage({ overview, token, mutate }: { overview: Overview; token: string; mutate: Mutate }) {
	const { t } = useI18n();
	const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
	const [previewArtifact, setPreviewArtifact] = useState<Artifact | null>(null);
	const [previewPayload, setPreviewPayload] = useState<ArtifactPayload | null>(null);
	const [previewLoadingId, setPreviewLoadingId] = useState('');
	const [downloadLoadingId, setDownloadLoadingId] = useState('');
	const [previewError, setPreviewError] = useState('');
	const [jobMutationReason, setJobMutationReason] = useState('');
	const [jobMutationBusyId, setJobMutationBusyId] = useState('');
	const [jobMutationError, setJobMutationError] = useState('');
	// Queue recovery (retry/cancel) relocated from the retired Jobs & Approvals page.
	// A non-empty reason is mandatory (the old page allowed an empty reason).
	const runJobMutation = async (op: 'retry' | 'cancel', jobId: string, reason: string) => {
		if (!reason.trim()) {
			setJobMutationError('Queue change reason is required.');
			return;
		}
		setJobMutationBusyId(jobId);
		setJobMutationError('');
		try {
			await mutate((writeToken) => (op === 'retry' ? retryJob(writeToken, jobId, reason.trim()) : cancelJob(writeToken, jobId, reason.trim())));
		} catch (error) {
			setJobMutationError(error instanceof Error ? error.message : 'Queue operation failed.');
		} finally {
			setJobMutationBusyId('');
		}
	};
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
			setPreviewError(t('app.workbench.evidence.incomplete', 'Artifact metadata is incomplete.'));
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
			setPreviewError(t('app.workbench.evidence.incomplete', 'Artifact metadata is incomplete.'));
			return;
		}
		setPreviewError('');
		setDownloadLoadingId(artifactId);
		try {
			await downloadEvidenceArtifact(token, evidenceId, artifactId, artifactDisplayName(artifact));
		} catch (error) {
			setPreviewError(error instanceof Error ? error.message : t('app.workbench.evidence.downloadError', 'Artifact download failed.'));
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
				jobRuns: [],
				agentRuns: [],
				toolCalls: [],
				modelCalls: [],
				permissionDecisions: [],
				evidence: [],
				artifacts: [],
				testResults: [],
				approvals: [],
				auditEvents: [],
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
			jobRuns: overview.jobRuns.filter((jobRun) => jobIds.has(String(jobRun.jobId ?? ''))),
			agentRuns,
			toolCalls,
			modelCalls: overview.modelCalls.filter((modelCall) => {
				const metadata = objectValue(modelCall.metadata);
				return agentRunIds.has(String(modelCall.agentRunId ?? '')) || runIds.has(stringValue(metadata.workflowRunId));
			}),
			permissionDecisions: overview.permissionDecisions.filter((decision) => permissionDecisionIds.has(String(decision.id ?? ''))),
			evidence,
			artifacts: overview.artifacts.filter((artifact) => evidenceIds.has(String(artifact.evidencePackageId ?? ''))),
			testResults: overview.testResultRecords.filter((item) => evidenceIds.has(String(item.evidencePackageId ?? ''))),
			approvals: overview.actionRequests.filter((approval) => jobIds.has(approval.jobId)),
			auditEvents: overview.auditEvents.filter((event) => {
				const payload = objectValue(event.payload);
				return runIds.has(String(event.target ?? '')) || runIds.has(stringValue(payload.workflowRunId));
			}),
		};
	}, [overview, selectedWorkflow]);
	const nodes = nodesFromSteps(linked.steps);
	const edges = edgesFromNodes(nodes);
	const timelineItems = useMemo(() => buildWorkflowTimeline(linked), [linked]);
	const missingForCompleted = useMemo(() => completionMissing(linked), [linked]);
	const blockers = useMemo(() => workflowBlockers(linked), [linked]);
	const patchArtifact = useMemo(() => findPatchArtifact(linked.artifacts), [linked.artifacts]);
	const pullRequestUrls = useMemo(() => workflowPullRequestUrls(linked), [linked]);
	return (
		<>
			<PageHeader
				kicker={t('ui.static.sdlc.graph.45dff846', 'SDLC graph')}
				title={t('app.nav.workflows', 'Workflows')}
				summary={t('ui.static.durable.workflow.runs.with.steps.evidence.and.permission.che.37de4524', 'Durable workflow runs with steps, evidence and permission checkpoints represented as operational state.')}
			/>
			<Surface title={t('ui.static.workflow.graph.32f4369a', 'Workflow graph')}>
				{selectedWorkflow && nodes.length ? (
					<div className="flow-board" aria-label={`Workflow graph for ${selectedWorkflow.title}`}>
						<ReactFlow nodes={nodes} edges={edges} fitView>
							<Background />
							<Controls />
						</ReactFlow>
					</div>
				) : (
					<EmptyState title={t('ui.static.no.workflow.steps', 'No workflow steps')} body={t('ui.static.select.or.start.workflow.run.before.reading.graph', 'Select or start a workflow run before reading a graph.')} />
				)}
			</Surface>
			<div className="grid two">
				<Surface title={t('ui.static.workflow.catalog', 'Workflow catalog')}>
					<DataTable
						rows={overview.workflows}
						empty={<EmptyState title={t('ui.static.no.workflows.52b4f780', 'No workflows')} body={t('ui.static.command.center.can.create.a.workflow.when.a.project.is.selec.7f8ad419', 'Command Center can create a workflow when a project is selected.')} />}
						columns={[
							{ key: 'title', label: t('ui.static.title.768e0c1c', 'Title'), render: (row) => row.title },
							{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
							{
								key: 'inspect',
								label: t('ui.static.inspect.18ca87af', 'Inspect'),
								render: (row) => (
									<button className="button" type="button" aria-label={`Inspect workflow ${row.title}`} onClick={() => setSelectedWorkflowId(row.id)}>
										Inspect
									</button>
								),
							},
						]}
					/>
				</Surface>
				<Surface title={t('app.workbench.timeline.runsTitle', 'Workflow runs')}>
					<DataTable
						rows={linked.runs}
						empty={<EmptyState title={t('app.workbench.timeline.runsEmptyTitle', 'No workflow runs')} body={t('ui.static.starting.workflow.creates.run.record', 'Starting a workflow creates a run record.')} />}
						columns={[
							{ key: 'run', label: t('ui.static.run.44c29edb', 'Run'), render: (row) => <span className="mono">{shortId(String(row.id ?? ''))}</span> },
							{
								key: 'workflow',
								label: t('app.workbench.timeline.colWorkflow', 'Workflow'),
								render: (row) => <span className="mono">{shortId(String(row.workflowId ?? ''))}</span>,
							},
							{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
						]}
					/>
				</Surface>
				<Surface title={t('ui.static.selected.workflow.steps', 'Selected workflow steps')}>
					<DataTable
						rows={linked.steps}
						empty={<EmptyState title={t('ui.static.no.selected.workflow.steps', 'No selected workflow steps')} body={t('ui.static.inspect.workflow.with.run.to.see.step.plan', 'Inspect a workflow with a run to see its step plan.')} />}
						columns={[
							{ key: 'name', label: t('ui.static.step.dc416e10', 'Step'), render: (row) => <span className="mono">{row.name}</span> },
							{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
							{ key: 'agent', label: t('ui.static.agent.5ce2e6f4', 'Agent'), render: (row) => row.agentProfileId ?? 'unassigned' },
						]}
					/>
				</Surface>
			</div>
			<Drawer label={t('ui.static.workflow.inspector.d8560d75', 'Workflow inspector')} open={selectedWorkflowId !== null} onClose={() => setSelectedWorkflowId(null)}>
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
							<Surface title={t('ui.static.what.is.missing.for.completed.f6ca783a', 'What is missing for completed')} flat>
								{missingForCompleted.length ? (
									<ul className="compact-list">
										{missingForCompleted.map((item) => (
											<li key={item}>{item}</li>
										))}
									</ul>
								) : (
									<p className="text-muted">{t('ui.static.completion.prerequisites.satisfied.by.linked.records.51ee9c46', 'Completion prerequisites satisfied by linked records.')}</p>
								)}
							</Surface>
							<Surface title={t('app.workbench.inspector.blockers', 'Blockers')} flat>
								<DataTable rows={blockers} empty={<EmptyState title={t('app.workbench.inspector.noBlockers', 'No blockers')} body={t('ui.static.no.linked.blocker.reason.has.been.recorded.e960291f', 'No linked blocker reason has been recorded.')} />} columns={[
									{ key: 'source', label: t('ui.static.source.6da13add', 'Source'), render: (row) => <span className="mono">{row.source}</span> },
									{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
									{ key: 'reason', label: t('ui.static.reason.f219cc06', 'Reason'), render: (row) => redactVisibleText(row.reason) },
								]} />
							</Surface>
							<Surface title={t('ui.static.links.0974c8fd', 'Links')} flat>
								<div className="stack">
									{patchArtifact ? (
										<div className="inline">
											<span className="mono">{t('ui.static.diff.artifact.4b3d14f2', 'Diff artifact')}</span>
											<span>{artifactDisplayName(patchArtifact)}</span>
											<span className="mono">{shortId(String(patchArtifact.hash ?? ''))}</span>
										</div>
									) : (
										<p className="text-muted">{t('ui.static.no.diff.artifact.link.recorded.074dd704', 'No diff artifact link recorded.')}</p>
									)}
									<div className="inline">
										{linked.evidence.length ? linked.evidence.map((item) => (
											<a className="button" href="#evidence" key={item.id}>Evidence {item.id}</a>
										)) : <span className="text-muted">{t('ui.static.no.evidence.link.recorded.090f170c', 'No evidence link recorded.')}</span>}
									</div>
									<div className="inline">
										{pullRequestUrls.length ? pullRequestUrls.map((url) => (
											<a className="button" href={url} key={url} rel="noreferrer" target="_blank">PR {url}</a>
										)) : <span className="text-muted">{t('ui.static.no.pr.link.recorded.5d6bb7dc', 'No PR link recorded.')}</span>}
									</div>
								</div>
							</Surface>
							<Surface title={t('ui.static.workflow.timeline.c2b65038', 'Workflow timeline')} flat>
								<WorkflowTimeline items={timelineItems} />
							</Surface>
							<Surface title={t('ui.static.runtime.and.run.state.944e320d', 'Runtime and run state')} flat>
								<DataTable rows={linked.runs} empty={<EmptyState title={t('app.workbench.timeline.runsEmptyTitle', 'No workflow runs')} body={t('ui.static.a.run.record.appears.after.workflow.execution.starts.2d1d2f67', 'A run record appears after workflow execution starts.')} />} columns={[
									{ key: 'run', label: t('ui.static.run.44c29edb', 'Run'), render: (row) => <span className="mono">{shortId(String(row.id ?? ''))}</span> },
									{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
									{
										key: 'runtime',
										label: t('ui.static.runtime.c4740e4c', 'Runtime'),
										render: (row) => {
											const metadata = objectValue(row.metadata);
											const runtime = objectValue(metadata.runtime);
											return <span className="mono">{String(runtime.id ?? t('app.workbench.workspace.missingPath', 'not selected'))}</span>;
										},
									},
									{
										key: 'qa',
										label: t('ui.static.qa.verdict.48a65c68', 'QA verdict'),
										render: (row) => {
											const metadata = objectValue(row.metadata);
											return <Badge tone={toneForStatus(String(metadata.qaVerdict ?? 'not_run'))}>{String(metadata.qaVerdict ?? 'not_run')}</Badge>;
										},
									},
									{
										key: 'diff',
										label: t('app.workbench.tab.diff', 'Diff'),
										render: (row) => {
											const metadata = objectValue(row.metadata);
											const diff = objectValue(metadata.diffSummary);
											const changedFiles = Array.isArray(diff.changedFiles) ? diff.changedFiles.length : Number(diff.changedFiles ?? 0);
											return <span className="mono">{String(diff.state ?? 'not captured')} / {Number.isFinite(changedFiles) ? changedFiles : 0} files</span>;
										},
									},
								]} />
								<DataTable rows={linked.agentRuns} empty={<EmptyState title={t('ui.static.no.agent.runs.5482c9e5', 'No agent runs')} body={t('ui.static.agent.run.records.appear.after.runtime.selection.72e0d9bd', 'Agent run records appear after runtime selection.')} />} columns={[
									{ key: 'agent', label: t('ui.static.agent.run.b458871f', 'Agent run'), render: (row) => <span className="mono">{shortId(String(row.id ?? ''))}</span> },
									{
										key: 'profile',
										label: t('ui.static.profile.ff4fc027', 'Profile'),
										render: (row) => {
											const metadata = objectValue(row.metadata);
											return <span className="mono">{String(metadata.agentProfileId ?? metadata.runtimeType ?? t('app.runtime.card.unknown', 'unknown'))}</span>;
										},
									},
									{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
								]} />
							</Surface>
							<Surface title={t('ui.static.model.calls.88e40906', 'Model calls')} flat>
								<DataTable rows={linked.modelCalls} empty={<EmptyState title={t('ui.static.no.model.calls.3e3394fe', 'No model calls')} body={t('ui.static.model.calls.linked.to.workflow.agent.runs.appear.here.0ae76932', 'Model calls linked to workflow agent runs appear here.')} />} columns={[
									{ key: 'provider', label: t('ui.static.provider.7ceee3f3', 'Provider'), render: (row) => <span className="mono">{String(row.provider ?? '')}</span> },
									{ key: 'model', label: t('ui.static.model.68c2cc7f', 'Model'), render: (row) => <span className="mono">{String(row.model ?? '')}</span> },
									{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
									{ key: 'tokens', label: t('ui.static.tokens.9a1f9463', 'Tokens'), render: (row) => <span className="mono">{tokenLabel(row.promptTokens, t('app.runtime.card.unknown', 'unknown'))} / {tokenLabel(row.completionTokens, t('app.runtime.card.unknown', 'unknown'))}</span> },
									{ key: 'cost', label: t('ui.static.cost.64ae43e8', 'Cost'), render: (row) => <span className="mono">{costLabel(row.costUsd, t('app.runtime.card.unknown', 'unknown'))}</span> },
								]} />
							</Surface>
							<Surface title={t('ui.static.steps.cdde4f20', 'Steps')} flat>
								<DataTable rows={linked.steps} empty={<EmptyState title={t('ui.static.no.steps.00a23c5b', 'No steps')} body={t('ui.static.start.the.workflow.to.expand.steps.a5f96b0b', 'Start the workflow to expand steps.')} />} columns={[
									{ key: 'name', label: t('ui.static.step.dc416e10', 'Step'), render: (row) => <span className="mono">{row.name}</span> },
									{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
									{ key: 'risk', label: t('ui.static.risk.5a8f23f5', 'Risk'), render: (row) => <span className="mono">{String(row.riskLevel ?? t('app.workbenchEvidence.notRecorded', 'not recorded'))}</span> },
									{ key: 'model', label: t('ui.static.model.mode.f9d81107', 'Model mode'), render: (row) => <span className="mono">{String(row.modelMode ?? row.manualModelOverride ?? 'policy')}</span> },
								]} />
							</Surface>
							<Surface title={t('ui.static.evidence.and.tests.0c061aed', 'Evidence and tests')} flat>
								<DataTable rows={linked.evidence} empty={<EmptyState title={t('ui.static.no.evidence.fade4ede', 'No evidence')} body={t('ui.static.qa.packages.linked.to.this.workflow.run.appear.here.37481b29', 'QA packages linked to this workflow run appear here.')} />} columns={[
									{ key: 'task', label: t('ui.static.task.7bb0ddf9', 'Task'), render: (row) => String(row.taskId ?? '') },
									{ key: 'verdict', label: t('ui.static.verdict.7f6e5a6e', 'Verdict'), render: (row) => <Badge tone={toneForStatus(String(row.qaVerdict ?? ''))}>{String(row.qaVerdict ?? '')}</Badge> },
									{ key: 'source', label: t('ui.static.source.6da13add', 'Source'), render: (row) => <span className="mono">{String(row.evidenceSource ?? 'operator_attested')}</span> },
									{ key: 'diffs', label: t('ui.static.diff.refs.cc464235', 'Diff refs'), render: (row) => String(Array.isArray(row.diffRefs) ? row.diffRefs.length : 0) },
									{
										key: 'completeness',
										label: t('ui.static.completeness.4008e0b6', 'Completeness'),
										render: (row) => {
											const hasQa = Array.isArray(row.testResults) && row.testResults.length > 0;
											const hasDiffRefs = Array.isArray(row.diffRefs) && row.diffRefs.length > 0;
											return <span className="mono">{hasQa && hasDiffRefs ? 'complete' : 'partial'}</span>;
										},
									},
								]} />
								<DataTable rows={linked.testResults} empty={<EmptyState title={t('ui.static.no.test.records.4878c3ab', 'No test records')} body={t('ui.static.test.results.appear.after.evidence.ingestion.26f3959e', 'Test results appear after evidence ingestion.')} />} columns={[
									{ key: 'command', label: t('app.workbench.evidence.colCommand', 'Command'), render: (row) => <span className="mono">{String(row.command ?? '')}</span> },
									{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
								]} />
							</Surface>
							<Surface title={t('ui.static.artifacts.a5b79f59', 'Artifacts')} flat>
								<DataTable rows={linked.artifacts} empty={<EmptyState title={t('ui.static.no.artifacts.3e0935de', 'No artifacts')} body={t('ui.static.logs.reports.and.screenshots.linked.to.evidence.appear.here.9ce88334', 'Logs, reports and screenshots linked to evidence appear here.')} />} columns={[
									{
										key: 'name',
										label: t('ui.static.name.709a2322', 'Name'),
										render: (row) => <span className="mono">{artifactDisplayName(row)}</span>,
									},
									{ key: 'kind', label: t('app.workbench.evidence.colKind', 'Kind'), render: (row) => <Badge>{String(row.kind ?? t('app.review.artifact', 'artifact'))}</Badge> },
									{ key: 'size', label: t('ui.static.size.b7152342', 'Size'), render: (row) => artifactSizeLabel(row) },
									{ key: 'hash', label: t('ui.static.hash.873507a0', 'Hash'), render: (row) => <span className="mono">{shortId(String(row.hash ?? ''))}</span> },
									{
										key: 'action',
										label: t('ui.static.action.97c89a4d', 'Action'),
										render: (row) => {
											const name = artifactDisplayName(row);
											const loading = previewLoadingId === String(row.id ?? '');
											const downloading = downloadLoadingId === String(row.id ?? '');
											return (
												<div className="inline" aria-busy={loading || downloading}>
													<button className="button" type="button" aria-label={`Preview workflow artifact ${name}`} disabled={loading} onClick={() => void openPreview(row)}>
														{loading ? t('app.review.opening', 'Opening') : t('app.workbench.evidence.preview', 'Preview')}
													</button>
													<button className="button" type="button" aria-label={`Download workflow artifact ${name}`} disabled={downloading} onClick={() => void downloadArtifact(row)}>
														{downloading ? t('app.workbench.evidence.downloading', 'Downloading') : t('app.workbench.evidence.download', 'Download')}
													</button>
												</div>
											);
										},
									},
								]} />
							</Surface>
							<Surface title={t('ui.static.security.and.policy.decisions.f06bb7ad', 'Security and policy decisions')} flat>
								<DataTable rows={linked.permissionDecisions} empty={<EmptyState title={t('app.workflows.noPolicyDecisions', 'No policy decisions')} body={t('ui.static.policy.decisions.linked.through.workflow.tool.calls.appear.h.7cf16ea5', 'Policy decisions linked through workflow tool calls appear here.')} />} columns={[
									{ key: 'decision', label: t('ui.static.decision.7f59a1f1', 'Decision'), render: (row) => <Badge tone={toneForStatus(String(row.decision ?? ''))}>{String(row.decision ?? '')}</Badge> },
									{ key: 'risk', label: t('ui.static.risk.5a8f23f5', 'Risk'), render: (row) => <Badge tone={toneForStatus(String(row.riskLevel ?? ''))}>{String(row.riskLevel ?? '')}</Badge> },
									{
										key: 'categories',
										label: t('ui.static.categories.6ccb6007', 'Categories'),
										render: (row) => {
											const payload = row.payload as Record<string, unknown> | undefined;
											const categories = Array.isArray(payload?.categories) ? payload.categories.join(', ') : '';
											return <span className="mono">{categories}</span>;
										},
									},
									{ key: 'reason', label: t('ui.static.reason.f219cc06', 'Reason'), render: (row) => String(row.reason ?? '') },
								]} />
							</Surface>
							<Surface title={t('ui.static.tool.calls.and.approvals.3788745c', 'Tool calls and approvals')} flat>
								<DataTable rows={linked.toolCalls} empty={<EmptyState title={t('ui.static.no.tool.calls.7ab6a86e', 'No tool calls')} body={t('ui.static.agent.runtime.calls.linked.to.this.workflow.appear.here.7fb6ecbb', 'Agent runtime calls linked to this workflow appear here.')} />} columns={[
									{ key: 'tool', label: t('ui.static.tool.9a830c71', 'Tool'), render: (row) => <span className="mono">{String(row.toolName ?? '')}</span> },
									{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
									{ key: 'command', label: t('app.workbench.evidence.colCommand', 'Command'), render: (row) => {
										const payload = row.payload as Record<string, unknown> | undefined;
										return <span className="mono">{String(payload?.command ?? '')}</span>;
									} },
								]} />
								<DataTable rows={linked.approvals} empty={<EmptyState title={t('ui.static.no.approvals.380cc620', 'No approvals')} body={t('ui.static.granular.approvals.linked.to.workflow.jobs.appear.here.7308c236', 'Granular approvals linked to workflow jobs appear here.')} />} columns={[
									{ key: 'action', label: t('ui.static.action.97c89a4d', 'Action'), render: (row) => <span className="mono">{row.actionType}</span> },
									{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
									{ key: 'risk', label: t('ui.static.risk.5a8f23f5', 'Risk'), render: (row) => <Badge tone={toneForStatus(row.riskLevel)}>{row.riskLevel}</Badge> },
								]} />
							</Surface>
							<Surface title={t('app.nav.workspaces', 'Workspaces')} flat>
								<DataTable rows={linked.workspaces} empty={<EmptyState title={t('ui.static.no.workspaces.09e5b922', 'No workspaces')} body={t('ui.static.workspace.allocations.appear.after.implementation.steps.64d15c62', 'Workspace allocations appear after implementation steps.')} />} columns={[
									{ key: 'task', label: t('ui.static.task.7bb0ddf9', 'Task'), render: (row) => <span className="mono">{String(row.taskId ?? '')}</span> },
									{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
								]} />
							</Surface>
							<Surface title={t('ui.static.jobs.and.leases.4768364f', 'Jobs and leases')} flat>
								<div className="field">
									<label htmlFor="workflow-job-mutation-reason">{t('app.copy.standalone.3', 'Queue change reason')}</label>
									<textarea id="workflow-job-mutation-reason" className="textarea" value={jobMutationReason} onChange={(event) => { setJobMutationReason(event.target.value); setJobMutationError(''); }} />
									<div className="field-help">{t('app.workflows.queueReasonHelp', 'Retry and Cancel are disabled until a reason is recorded.')}</div>
								</div>
								{jobMutationError ? <div className="form-error" role="alert">{jobMutationError}</div> : null}
								<DataTable rows={linked.jobs} empty={<EmptyState title={t('ui.static.no.jobs.e0f919e2', 'No jobs')} body={t('ui.static.jobs.linked.to.workflow.runs.appear.here.7c0a2243', 'Jobs linked to workflow runs appear here.')} />} columns={[
									{ key: 'kind', label: t('app.workbench.evidence.colKind', 'Kind'), render: (row) => <span className="mono">{row.kind}</span> },
									{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
									{ key: 'lease', label: t('ui.static.lease.b24ef8c7', 'Lease'), render: (row) => <span>{row.leaseOwner ? `${row.leaseOwner} until ${row.leaseExpiresAt}` : t('app.runtime.card.none', 'none')}</span> },
									{
										key: 'runtime',
										label: t('ui.static.runtime.c4740e4c', 'Runtime'),
										render: (row) => {
											const payload = objectValue(row.payload);
											const runtime = objectValue(payload.runtime);
											return <span className="mono">{String(runtime.id ?? t('app.workbench.workspace.missingPath', 'not selected'))}</span>;
										},
									},
									{
										key: 'actions',
										label: t('ui.static.actions.c3cd636a', 'Actions'),
										render: (row) => {
											const busy = jobMutationBusyId === row.id;
											const blocked = !jobMutationReason.trim() || Boolean(jobMutationBusyId);
											return (
												<div className="inline" aria-busy={busy}>
													<button className="button" type="button" aria-label={`${t('ui.static.retry.9f5cd8a2', 'Retry')} job ${row.kind}`} disabled={blocked} onClick={() => void runJobMutation('retry', row.id, jobMutationReason)}>{busy ? 'Retrying' : t('ui.static.retry.9f5cd8a2', 'Retry')}</button>
													<button className="button danger" type="button" aria-label={`${t('ui.static.cancel.77dfd213', 'Cancel')} job ${row.kind}`} disabled={blocked} onClick={() => void runJobMutation('cancel', row.id, jobMutationReason)}>{busy ? 'Cancelling' : t('ui.static.cancel.77dfd213', 'Cancel')}</button>
												</div>
											);
										},
									},
								]} />
								<DataTable rows={linked.jobRuns} empty={<EmptyState title={t('ui.static.no.job.runs.4e3be714', 'No job runs')} body={t('ui.static.worker.lease.attempts.appear.here.when.jobs.execute.35553b89', 'Worker lease attempts appear here when jobs execute.')} />} columns={[
									{ key: 'run', label: t('ui.static.run.44c29edb', 'Run'), render: (row) => <span className="mono">{shortId(String(row.id ?? ''))}</span> },
									{ key: 'provider', label: t('ui.static.provider.7ceee3f3', 'Provider'), render: (row) => <span className="mono">{String(row.providerId ?? t('app.workbenchEvidence.notRecorded', 'not recorded'))}</span> },
									{ key: 'status', label: t('ui.static.status.bae7d5be', 'Status'), render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
									{ key: 'summary', label: t('ui.static.summary.b5473fa1', 'Summary'), render: (row) => redactVisibleText(row.summary) },
								]} />
							</Surface>
						</>
					) : (
						<EmptyState title={t('ui.static.no.workflow.selected.215ac484', 'No workflow selected')} body={t('ui.static.choose.a.workflow.to.inspect.its.linked.records.4f7e51a2', 'Choose a workflow to inspect its linked records.')} />
					)}
				</div>
			</Drawer>
			<Drawer label={t('ui.static.workflow.artifact.preview.df9b22a5', 'Workflow artifact preview')} open={Boolean(previewArtifact)} onClose={() => {
				setPreviewArtifact(null);
				setPreviewPayload(null);
				setPreviewError('');
			}}>
				<div className="drawer-body">
					{previewArtifact ? (
						<>
							<div className="stack">
								<div className="inline">
									<Badge>{String(previewArtifact.kind ?? t('app.review.artifact', 'artifact'))}</Badge>
									<Badge>{artifactMimeType(previewArtifact, previewPayload)}</Badge>
									<Badge>{artifactSizeLabel(previewArtifact)}</Badge>
								</div>
								<h3 className="artifact-title">{artifactDisplayName(previewArtifact)}</h3>
								<div className="mono">sha256 {String(previewPayload?.hash || previewArtifact.hash || t('app.workbenchEvidence.notRecorded', 'not recorded'))}</div>
							</div>
							{previewError ? <div className="form-error" role="alert">{previewError}</div> : null}
							{downloadLoadingId ? <div className="sr-only" role="status">{t('ui.static.downloading.artifact.b640e8fe', 'Downloading artifact')}</div> : null}
							{previewPayload?.text ? (
								<pre className="artifact-preview">{redactVisibleText(previewPayload.text, '')}</pre>
							) : (
								<EmptyState title={previewLoadingId ? t('app.workbench.evidence.loading', 'Loading artifact') : t('app.review.binaryOrEmptyArtifact', 'Binary or empty artifact')} body={t('ui.static.non.text.artifacts.remain.downloadable.but.are.not.rendered.42481492', 'Non-text artifacts remain downloadable, but are not rendered inline.')} />
							)}
							<button className="button primary" type="button" disabled={downloadLoadingId === String(previewArtifact.id ?? '')} aria-label={`Download workflow preview artifact ${artifactDisplayName(previewArtifact)}`} onClick={() => void downloadArtifact(previewArtifact)}>
								{downloadLoadingId === String(previewArtifact.id ?? '') ? t('ui.static.downloading.artifact.b640e8fe', 'Downloading artifact') : t('app.review.downloadArtifact', 'Download artifact')}
							</button>
						</>
					) : null}
				</div>
			</Drawer>
		</>
	);
}
