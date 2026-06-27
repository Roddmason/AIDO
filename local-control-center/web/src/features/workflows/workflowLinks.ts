/**
 * Pure join + derivation helpers for workflow runs: gather every record linked to a workflow
 * (or a single run) from the overview, and derive the merged timeline, blockers, "what's missing
 * for completed" gap list and PR links. No React/JSX here — every export is deterministic and
 * unit-testable, shared by the Workflows launcher page and the run-detail Inspector.
 * @author Rodrigo Mason
 */
import type { Overview } from '../../api/types';
import { findPatchArtifact } from '../../lib/diff';
import { shortId, toneForStatus } from '../../lib/format';

export type Translate = (key: string, fallback?: string) => string;

export type WorkflowLinks = {
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

export type WorkflowTimelineItem = {
	id: string;
	label: string;
	status: string;
	tone: ReturnType<typeof toneForStatus>;
	detail: string;
	source: string;
	createdAt: string;
	sortKey: number;
};

export type WorkflowBlocker = {
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

/** An empty links bundle, returned when no workflow/run resolves. */
export const EMPTY_LINKS: WorkflowLinks = {
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

export function objectValue(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: {};
}

export function stringValue(value: unknown, fallback = ''): string {
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

export function tokenLabel(value: unknown, unknownLabel = 'unknown'): string {
	const valueNumber = finiteNumber(value);
	return valueNumber === null ? unknownLabel : String(valueNumber);
}

export function costLabel(value: unknown, unknownLabel = 'unknown'): string {
	const valueNumber = finiteNumber(value);
	return valueNumber === null ? unknownLabel : `$${valueNumber.toFixed(4)}`;
}

export function sortTime(value?: string | null, fallback = 0): number {
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
	t: Translate,
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
	return stringValue(
		step.taskType,
		t('app.workflows.stepDetailWaiting', 'waiting for linked execution records'),
	);
}

export function latestRun(linked: WorkflowLinks): Overview['workflowRuns'][number] | undefined {
	return linked.runs
		.slice()
		.sort(
			(left, right) =>
				sortTime(String(right.startedAt ?? '')) - sortTime(String(left.startedAt ?? '')),
		)[0];
}

function statusIsPassed(value: unknown): boolean {
	return ['passed', 'completed', 'approved', 'pr_created'].includes(
		String(value ?? '').toLowerCase(),
	);
}

function hasDiffEvidence(linked: WorkflowLinks): boolean {
	if (findPatchArtifact(linked.artifacts)) return true;
	return linked.evidence.some((item) => Array.isArray(item.diffRefs) && item.diffRefs.length > 0);
}

function hasPassingQaEvidence(linked: WorkflowLinks): boolean {
	if (linked.testResults.some((item) => statusIsPassed(item.status))) return true;
	return linked.evidence.some(
		(item) =>
			Array.isArray(item.testResults) &&
			item.testResults.some((result) => statusIsPassed(objectValue(result).status)),
	);
}

export function completionMissing(linked: WorkflowLinks, t: Translate): string[] {
	const missing: string[] = [];
	const run = latestRun(linked);
	if (!run) return [t('app.workflows.missingNoRun', 'No workflow run recorded.')];
	if (!linked.steps.length)
		missing.push(t('app.workflows.missingNoSteps', 'No workflow steps recorded.'));
	const unfinishedSteps = linked.steps.filter(
		(step) => !statusIsPassed(step.status) && step.status !== 'skipped',
	);
	if (unfinishedSteps.length) {
		missing.push(
			`${t('app.workflows.missingUnfinishedSteps', 'Unfinished steps:')} ${unfinishedSteps.map((step) => step.name).join(', ')}.`,
		);
	}
	if (!linked.evidence.length)
		missing.push(t('app.workflows.missingNoEvidence', 'No evidence package recorded.'));
	if (!hasDiffEvidence(linked))
		missing.push(t('app.workflows.missingNoDiffRefs', 'No diff evidence refs recorded.'));
	if (!hasPassingQaEvidence(linked))
		missing.push(t('app.workflows.missingNoPassingQa', 'No passing QA test results recorded.'));
	const pendingApprovals = linked.approvals.filter((approval) => approval.status === 'pending');
	if (pendingApprovals.length)
		missing.push(
			`${t('app.workflows.missingPendingApprovals', 'Pending approvals:')} ${pendingApprovals.length}.`,
		);
	const blockingStatuses = new Set(['blocked', 'runtime_unavailable', 'qa_failed', 'failed']);
	if (blockingStatuses.has(String(run.status ?? '')))
		missing.push(
			`${t('app.workflows.missingRunStatusIs', 'Run status is')} ${String(run.status)}.`,
		);
	return Array.from(new Set(missing));
}

function blockerReason(payload: Record<string, unknown>, fallback: string): string {
	return (
		stringValue(payload.reason) ||
		stringValue(payload.blockedReason) ||
		stringValue(payload.error) ||
		fallback
	);
}

export function workflowBlockers(linked: WorkflowLinks, t: Translate): WorkflowBlocker[] {
	const blockers: WorkflowBlocker[] = [];
	const blockingStatuses = new Set([
		'blocked',
		'runtime_unavailable',
		'qa_failed',
		'failed',
		'denied',
		'error',
		'critical',
	]);
	for (const run of linked.runs) {
		if (blockingStatuses.has(String(run.status ?? ''))) {
			blockers.push({
				id: `run-${run.id}`,
				source: `${t('app.workflows.blockerSourceRun', 'run')} ${shortId(String(run.id ?? ''))}`,
				status: String(run.status ?? ''),
				reason: blockerReason(
					objectValue(run.metadata),
					`${t('app.workflows.blockerRunStatusIs', 'Run status is')} ${String(run.status ?? '')}.`,
				),
			});
		}
	}
	for (const step of linked.steps) {
		if (blockingStatuses.has(String(step.status ?? ''))) {
			blockers.push({
				id: `step-${step.id}`,
				source: `${t('app.workflows.blockerSourceStep', 'step')} ${step.name}`,
				status: step.status,
				reason: blockerReason(
					objectValue(step.output),
					blockerReason(
						objectValue(step.metadata),
						`${t('app.workflows.blockerStepStatusIs', 'Step status is')} ${step.status}.`,
					),
				),
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
				source: `${t('app.workflows.blockerSourceJob', 'job')} ${job.kind}`,
				status: job.status,
				reason: blockerReason(
					objectValue(job.payload),
					`${t('app.workflows.blockerJobStatusIs', 'Job status is')} ${job.status}.`,
				),
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
	return (
		stringValue(pullRequest.htmlUrl) ||
		stringValue(pullRequest.html_url) ||
		stringValue(pullRequest.url)
	);
}

export function workflowPullRequestUrls(linked: WorkflowLinks): string[] {
	const urls = [
		...linked.runs.map((run) => pullRequestUrlFrom(run.metadata)),
		...linked.auditEvents.map((event) => pullRequestUrlFrom(event.payload)),
		...linked.events.map((event) => pullRequestUrlFrom(event.payload)),
	].filter(Boolean);
	return Array.from(new Set(urls));
}

export function buildWorkflowTimeline(linked: WorkflowLinks, t: Translate): WorkflowTimelineItem[] {
	const run = latestRun(linked);
	const runId = String(run?.id ?? '');
	const steps = runId
		? linked.steps.filter((step) => String(step.workflowRunId ?? '') === runId)
		: linked.steps;
	const events = runId
		? linked.events.filter((event) => String(event.workflowRunId ?? '') === runId)
		: linked.events;
	const auditEvents = runId
		? linked.auditEvents.filter(
				(event) =>
					String(event.target ?? '') === runId ||
					stringValue(objectValue(event.payload).workflowRunId) === runId,
			)
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
		? runMetadata.gateResults.filter(
				(item): item is Record<string, unknown> =>
					Boolean(item) && typeof item === 'object' && !Array.isArray(item),
			)
		: [];
	const gateByStep = new Map<string, Record<string, unknown>>();
	for (const gate of gateResults) {
		const stepName = GATE_TO_STEP[stringValue(gate.name)];
		if (stepName) gateByStep.set(stepName, gate);
	}
	const stepItems = steps
		.slice()
		.sort(
			(left, right) =>
				Number(objectValue(left.metadata).order ?? 0) -
				Number(objectValue(right.metadata).order ?? 0),
		)
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
				detail: stepDetail(step, gateResult, evidence, agentRun, job, t),
				source: `${t('app.workflows.timelineSourceStep', 'step')} ${index + 1}`,
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
			source: t('app.workflows.timelineSourceWorkflowEvent', 'workflow event'),
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
			source: t('app.workflows.timelineSourceAuditEvent', 'audit event'),
			createdAt: event.createdAt,
			sortKey: sortTime(event.createdAt) + index / 100 + 0.5,
		};
	});
	return [...stepItems, ...eventItems, ...auditItems].sort(
		(left, right) => left.sortKey - right.sortKey,
	);
}

/**
 * Joins every record linked to a single workflow run into one bundle. Used by the run-detail
 * Inspector so each tab reads run-scoped data without re-deriving the join.
 */
export function linksForRun(overview: Overview, runId: string): WorkflowLinks {
	const run = overview.workflowRuns.find((item) => String(item.id ?? '') === runId);
	if (!run) return EMPTY_LINKS;
	const runIds = new Set([runId]);
	const steps = overview.workflowSteps.filter((step) => String(step.workflowRunId ?? '') === runId);
	const events = overview.workflowEvents.filter(
		(event) => String(event.workflowRunId ?? '') === runId,
	);
	return joinFromRunScope(overview, [run], runIds, steps, events);
}

/**
 * Shared downstream join keyed off a set of run ids plus the seed steps/events. Splitting it out
 * keeps `linksForRun` focused on selecting the seed records; the downstream fan-out (jobs, agent
 * runs, tool/model calls, evidence, artifacts, approvals, audit) is identical regardless of seed.
 */
function joinFromRunScope(
	overview: Overview,
	runList: Overview['workflowRuns'],
	runIds: Set<string>,
	steps: Overview['workflowSteps'],
	events: Overview['workflowEvents'],
): WorkflowLinks {
	const stepIds = new Set(steps.map((step) => step.id));
	const jobs = overview.jobs.filter((job) =>
		runIds.has(String(job.payload?.workflowRunId ?? job.workflowRunId ?? '')),
	);
	const jobIds = new Set(jobs.map((job) => job.id));
	const agentRuns = overview.agentRuns.filter(
		(run) =>
			runIds.has(String(run.workflowRunId ?? '')) || stepIds.has(String(run.workflowStepId ?? '')),
	);
	const agentRunIds = new Set(agentRuns.map((run) => String(run.id ?? '')));
	const toolCalls = overview.agentToolCalls.filter((toolCall) =>
		agentRunIds.has(String(toolCall.agentRunId ?? '')),
	);
	const permissionDecisionIds = new Set(
		toolCalls
			.map((toolCall) => {
				const payload = toolCall.payload as Record<string, unknown> | undefined;
				return String(payload?.permissionDecisionId ?? '');
			})
			.filter(Boolean),
	);
	const evidence = overview.evidencePackages.filter((item) =>
		runIds.has(String(item.workflowRunId ?? '')),
	);
	const evidenceIds = new Set(evidence.map((item) => String(item.id ?? '')));
	return {
		runs: runList,
		steps,
		events,
		workspaces: overview.runtimeWorkspaces.filter((workspace) =>
			runIds.has(String(workspace.workflowRunId ?? '')),
		),
		jobs,
		jobRuns: overview.jobRuns.filter((jobRun) => jobIds.has(String(jobRun.jobId ?? ''))),
		agentRuns,
		toolCalls,
		modelCalls: overview.modelCalls.filter((modelCall) => {
			const metadata = objectValue(modelCall.metadata);
			return (
				agentRunIds.has(String(modelCall.agentRunId ?? '')) ||
				runIds.has(stringValue(metadata.workflowRunId))
			);
		}),
		permissionDecisions: overview.permissionDecisions.filter((decision) =>
			permissionDecisionIds.has(String(decision.id ?? '')),
		),
		evidence,
		artifacts: overview.artifacts.filter((artifact) =>
			evidenceIds.has(String(artifact.evidencePackageId ?? '')),
		),
		testResults: overview.testResultRecords.filter((item) =>
			evidenceIds.has(String(item.evidencePackageId ?? '')),
		),
		approvals: overview.actionRequests.filter((approval) => jobIds.has(approval.jobId)),
		auditEvents: overview.auditEvents.filter((event) => {
			const payload = objectValue(event.payload);
			return (
				runIds.has(String(event.target ?? '')) || runIds.has(stringValue(payload.workflowRunId))
			);
		}),
	};
}
