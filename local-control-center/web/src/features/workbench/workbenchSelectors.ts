/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import type { IssueToPatchResponse } from '../../api/client';
import type { Overview, Project, RuntimeProvider } from '../../api/types';
import { shortId } from '../../lib/format';

export type TimelineStatus = 'pending' | 'done' | 'active' | 'blocked' | 'failed';
export type IssueTimelineEntry = { id: string; status: TimelineStatus; detail: string };
export type Blocker = { id: string; label: string; detail: string; tone: 'warn' | 'danger' };

export const issueRuntimeCapabilities = new Set(['issue_to_patch', 'code_edit']);
export const issueTimelineOrder = [
	'created',
	'workspace_allocated',
	'runtime_selected',
	'running',
	'qa_running',
	'evidence_ready',
	'awaiting_approval',
] as const;

const failingRunStatuses = new Set([
	'failed',
	'runtime_unavailable',
	'qa_failed',
	'promotion_failed',
	'blocked',
	'cancelled',
]);

export function objectRecord(value: unknown) {
	return value && typeof value === 'object' ? (value as Record<string, unknown>) : undefined;
}

export function activeProjects(projects: Project[]) {
	return projects.filter((project) => String(project.status ?? 'active') === 'active');
}

export function runtimeSupportsIssueToPatch(runtime: RuntimeProvider) {
	const kind = String(runtime.kind ?? '').toLowerCase();
	return kind !== 'test' && kind !== 'simulation' && (runtime.capabilities ?? []).some((capability) => issueRuntimeCapabilities.has(capability));
}

export function runtimeIsExecutableIssueRuntime(runtime: RuntimeProvider) {
	return runtimeSupportsIssueToPatch(runtime) && runtime.executable === true;
}

export function timeOf(record: { updatedAt?: string | null; createdAt?: string | null; startedAt?: string | null; completedAt?: string | null }) {
	const value = record.updatedAt ?? record.completedAt ?? record.startedAt ?? record.createdAt ?? '';
	const parsed = Date.parse(String(value));
	return Number.isNaN(parsed) ? 0 : parsed;
}

export function sortByTimeDesc<T extends { updatedAt?: string | null; createdAt?: string | null; startedAt?: string | null; completedAt?: string | null }>(rows: T[]) {
	return [...rows].sort((left, right) => timeOf(right) - timeOf(left));
}

/**
 * Maps an issue_to_patch response (or its absence) to the staged run timeline.
 * Moved verbatim from the former CommandCenterPage so behavior is preserved.
 */
export function buildIssueTimeline(result: IssueToPatchResponse | null, isSubmittingTask: boolean, hasExecutableRuntime: boolean): IssueTimelineEntry[] {
	if (!result) {
		return [
			{ id: 'created', status: isSubmittingTask ? 'done' : 'pending', detail: isSubmittingTask ? 'request submitted' : 'not started' },
			{ id: 'workspace_allocated', status: 'pending', detail: 'waiting for workflow run' },
			{ id: 'runtime_selected', status: hasExecutableRuntime ? 'pending' : 'blocked', detail: hasExecutableRuntime ? 'waiting for run' : 'runtime_unavailable' },
			{ id: 'running', status: 'pending', detail: 'waiting for executable runtime' },
			{ id: 'qa_running', status: 'pending', detail: 'waiting for runtime output' },
			{ id: 'evidence_ready', status: 'pending', detail: 'waiting for artifact package' },
			{ id: 'awaiting_approval', status: 'pending', detail: 'waiting for evidence' },
			{ id: 'completed/failed', status: 'pending', detail: 'no terminal verdict' },
		];
	}
	const rawResult = result as unknown as Record<string, unknown>;
	const status = String(result.status ?? '');
	const workflowRun = objectRecord(result.workflowRun);
	const workspace = objectRecord(result.workspace);
	const runtime = objectRecord(result.runtime);
	const runtimeResult = objectRecord(result.runtimeResult);
	const evidence = objectRecord(result.evidencePackage);
	const qaResults = Array.isArray(result.qaResults) ? result.qaResults : [];
	const isRuntimeUnavailable = status === 'runtime_unavailable' || status === 'unavailable';
	const isFailed = isRuntimeUnavailable || status === 'failed' || String(workflowRun?.status ?? '') === 'failed';
	const isCompleted = status === 'completed' || String(workflowRun?.status ?? '') === 'completed';
	const isApprovedForIntegration = status === 'approved_for_integration' || String(workflowRun?.status ?? '') === 'approved_for_integration';
	const awaitingApproval = Boolean(rawResult.actionRequest) || (Boolean(rawResult.approvalRequired) && (status === 'evidence_ready' || String(workflowRun?.status ?? '') === 'awaiting_permission'));
	const stageDetails: Record<(typeof issueTimelineOrder)[number], string> = {
		created: String(workflowRun?.id ?? objectRecord(result.workflow)?.id ?? 'workflow not recorded'),
		workspace_allocated: String(workspace?.id ?? 'workspace not allocated'),
		runtime_selected: String(runtime?.id ?? 'runtime not selected'),
		running: String(runtimeResult?.status ?? (status || 'not started')),
		qa_running: qaResults.length ? String(objectRecord(qaResults[0])?.status ?? objectRecord(qaResults[0])?.verdict ?? 'qa_recorded') : 'qa_not_run',
		evidence_ready: String(evidence?.id ?? 'evidence not created'),
		awaiting_approval: isApprovedForIntegration ? 'approved for integration' : awaitingApproval ? 'approval gate open' : 'no pending approval',
	};
	const stageStatuses: Record<(typeof issueTimelineOrder)[number], TimelineStatus> = {
		created: workflowRun?.id || objectRecord(result.workflow)?.id ? 'done' : isFailed ? 'failed' : 'pending',
		workspace_allocated: workspace?.id ? 'done' : isFailed ? 'failed' : 'pending',
		runtime_selected: isRuntimeUnavailable ? 'failed' : runtime?.id ? 'done' : isFailed ? 'failed' : 'pending',
		running: isFailed ? 'failed' : isCompleted || runtimeResult ? 'done' : isSubmittingTask ? 'active' : 'pending',
		qa_running: qaResults.length ? (String(objectRecord(qaResults[0])?.status ?? objectRecord(qaResults[0])?.verdict ?? '') === 'failed' ? 'failed' : 'done') : 'pending',
		evidence_ready: evidence?.id ? 'done' : isFailed ? 'failed' : 'pending',
		awaiting_approval: awaitingApproval ? 'active' : isCompleted || isApprovedForIntegration ? 'done' : 'pending',
	};
	const terminalId = isCompleted ? 'completed' : isApprovedForIntegration ? 'approved_for_integration' : isFailed ? 'failed' : 'completed/failed';
	const terminalStatus: TimelineStatus = isCompleted || isApprovedForIntegration ? 'done' : isFailed ? 'failed' : 'pending';
	return [
		...issueTimelineOrder.map((id) => ({ id, status: stageStatuses[id], detail: stageDetails[id] })),
		{ id: terminalId, status: terminalStatus, detail: status || String(workflowRun?.status ?? 'not terminal') },
	];
}

/** Derives the project blockers shown in the inspector from real overview data. */
export function deriveBlockers(params: {
	projectId: string;
	workflowRuns: Overview['workflowRuns'];
	testResults: Overview['testResultRecords'];
	risks: Overview['riskRegister'];
	hasExecutableRuntime: boolean;
	runtimeBlockerReason: string;
}): Blocker[] {
	const { projectId, workflowRuns, testResults, risks, hasExecutableRuntime, runtimeBlockerReason } = params;
	const blockers: Blocker[] = [];
	if (!hasExecutableRuntime) {
		blockers.push({ id: 'runtime', label: 'runtime_unavailable', detail: runtimeBlockerReason, tone: 'danger' });
	}
	for (const run of sortByTimeDesc(workflowRuns.filter((run) => run.projectId === projectId && failingRunStatuses.has(String(run.status))))) {
		const tone = run.status === 'blocked' || run.status === 'cancelled' ? 'warn' : 'danger';
		blockers.push({ id: `run-${run.id}`, label: String(run.status), detail: `run ${shortId(run.id)}`, tone });
	}
	for (const result of testResults.filter((result) => result.projectId === projectId && result.status === 'failed').slice(0, 3)) {
		blockers.push({ id: `qa-${result.id}`, label: 'qa_failed', detail: result.command || shortId(result.id), tone: 'danger' });
	}
	for (const risk of risks.filter((risk) => risk.projectId === projectId && risk.status === 'open' && (risk.severity === 'high' || risk.severity === 'critical'))) {
		blockers.push({ id: `risk-${risk.id}`, label: `risk_${risk.severity}`, detail: risk.title, tone: risk.severity === 'critical' ? 'danger' : 'warn' });
	}
	return blockers.slice(0, 6);
}
