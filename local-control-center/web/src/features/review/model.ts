/**
 * Domain model and pure helpers for the review board: derives card-friendly
 * `ReviewItem`s from the raw overview, classifies them into lanes, and owns the
 * single source of truth for the patch evidence gate (refs, QA, security).
 * No React here — every export is deterministic and unit-testable.
 */
import type { ArtifactPayload } from '../../api/client';
import type { ActionRequest, Artifact, Overview } from '../../api/types';
import { artifactDisplayName } from '../../lib/artifacts';
import { findPatchArtifact, hasRealPatchChanges } from '../../lib/diff';
import { shortId } from '../../lib/format';

export type PatchWorkflowKind = 'issue_to_patch' | 'issue_to_pr';
export type PatchWorkflowApproval = { kind: PatchWorkflowKind; runId: string };

export type Tone = 'ok' | 'warn' | 'danger' | 'info';
export type RiskLevel = ActionRequest['riskLevel'];

export type ReviewColumn = 'needs_review' | 'blocked' | 'ready' | 'done';
export type ReviewItemSource = 'patch_workflow' | 'standalone_action' | 'decided_action';

/**
 * One review card. Holds only ids and display-ready fields — never the heavy
 * records — so the board memoizes cheaply and the decision drawer re-resolves
 * the live records from `overview` on open.
 */
export interface ReviewItem {
	key: string;
	source: ReviewItemSource;
	projectId: string;
	projectName: string;
	runLabel: string;
	workflowKind: string | null;
	runStatus: string | null;
	riskLevel: RiskLevel | null;
	qaVerdict: string | null;
	runId: string | null;
	actionId: string | null;
	jobId: string | null;
	evidencePackageId: string | null;
	hasDiff: boolean;
	hasEvidence: boolean;
	requiresPatchGate: boolean;
	canDecide: boolean;
	decisionStatus: ActionRequest['status'] | null;
	decidedBy: string | null;
	decidedAt: string | null;
	decisionReason: string | null;
	sortTime: number;
}

/** Maximum decided actions surfaced in the Done column (informational feed). */
export const DONE_LIMIT = 25;

const RISK_RANK: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1 };

// ----------------------------------------------------------------------------
// Pure helpers — the single source of truth for the evidence gate (the legacy
// JobsApprovalsPage that once duplicated these was retired).
// ----------------------------------------------------------------------------

/** Narrows an unknown to a plain object record; arrays and non-objects become `{}`. */
export function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: {};
}

export function prettyJson(value: unknown): string {
	return JSON.stringify(value ?? null, null, 2);
}

/**
 * Every artifact/evidence id an action points at, gathered from its evidence
 * refs, diff refs (string or `{artifactId,...}` shapes) and payload keys. The
 * union an artifact must match to count as "linked" to this action.
 */
export function referenceIds(action: ActionRequest): Set<string> {
	const ids = new Set<string>();
	for (const ref of action.evidenceRefs ?? []) ids.add(String(ref));
	for (const ref of action.diffRefs ?? []) {
		if (typeof ref === 'string') {
			ids.add(ref);
			continue;
		}
		const record = asRecord(ref);
		for (const key of ['artifactId', 'artifactID', 'id', 'evidencePackageId']) {
			const value = record[key];
			if (typeof value === 'string' && value.trim()) ids.add(value);
		}
	}
	const payload = asRecord(action.payload);
	for (const key of ['artifactId', 'diffArtifactId', 'evidencePackageId']) {
		const value = payload[key];
		if (typeof value === 'string' && value.trim()) ids.add(value);
	}
	return ids;
}

/** Artifacts in the overview whose id or evidence-package id this action references. */
export function linkedArtifacts(action: ActionRequest, overview: Overview): Artifact[] {
	const ids = referenceIds(action);
	return overview.artifacts.filter((artifact) => {
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		return ids.has(artifactId) || ids.has(evidenceId);
	});
}

/** Evidence packages this action references by id, or that share its `jobId`. */
export function linkedEvidence(
	action: ActionRequest,
	overview: Overview,
): Overview['evidencePackages'] {
	const ids = referenceIds(action);
	return overview.evidencePackages.filter(
		(evidence) =>
			ids.has(String(evidence.id ?? '')) || String(evidence.jobId ?? '') === action.jobId,
	);
}

/** Narrows an arbitrary value to a known patch-workflow kind, else `null`. */
export function patchWorkflowKind(value: unknown): PatchWorkflowKind | null {
	return value === 'issue_to_patch' || value === 'issue_to_pr' ? value : null;
}

/**
 * Reads the patch-workflow approval an action represents — its kind plus the
 * `workflowRunId` it approves — from the action type and payload. `null` when
 * the action is not a recognized patch/PR approval, which binds it to a run.
 */
export function patchWorkflowApproval(action: ActionRequest): PatchWorkflowApproval | null {
	const kind =
		action.actionType === 'workflow.issue_to_patch.approve_patch'
			? 'issue_to_patch'
			: action.actionType === 'workflow.issue_to_pr.approve_issue_to_pr'
				? 'issue_to_pr'
				: null;
	if (!kind) return null;
	const payload = asRecord(action.payload);
	const workflowRunId = payload.workflowRunId;
	return typeof workflowRunId === 'string' && workflowRunId.trim()
		? { kind, runId: workflowRunId }
		: null;
}

/** True when approving this action must clear the full patch evidence gate. */
export function requiresPatchEvidenceGate(action: ActionRequest): boolean {
	return (
		Boolean(patchWorkflowApproval(action)) || action.actionType === 'agent.developer.approve_patch'
	);
}

/** Identifies the security-findings artifact by its display name or kind. */
export function isSecurityFindingsArtifact(artifact: Artifact): boolean {
	const name = artifactDisplayName(artifact).toLowerCase();
	const kind = String(artifact.kind ?? '').toLowerCase();
	return name === 'security-findings.json' || kind.includes('security_findings');
}

export function findSecurityFindingsArtifact(artifacts: Artifact[]): Artifact | null {
	return artifacts.find(isSecurityFindingsArtifact) ?? null;
}

/** Whether an evidence package recorded at least one QA test result with `status: passed`. */
export function evidenceHasPassingQa(evidence: Overview['evidencePackages'][number]): boolean {
	return (evidence.testResults ?? []).some((result) => {
		const record = asRecord(result);
		const status = String(record.status ?? '').toLowerCase();
		return status === 'passed';
	});
}

/**
 * Parses a security-findings artifact and returns true only when it is valid
 * JSON, not globally `blocked`, and every finding carries an allowing decision
 * (no `deny`/`requires_human`/`requires_approval`). Fail-closed: malformed or
 * missing payloads return false so the gate stays shut.
 */
export function securityFindingsAreNonBlocking(payload: ArtifactPayload | null): boolean {
	if (!payload?.text) return false;
	try {
		const parsed = JSON.parse(payload.text) as unknown;
		const record = asRecord(parsed);
		if (String(record.status ?? '').toLowerCase() === 'blocked') return false;
		const findings = Array.isArray(record.findings) ? record.findings : [];
		return findings.every((finding) => {
			const decision = String(asRecord(finding).decision ?? '').toLowerCase();
			return !['deny', 'requires_human', 'requires_approval'].includes(decision);
		});
	} catch {
		return false;
	}
}

/**
 * The patch evidence gate: turns the loaded artifacts/payloads into a per-check
 * pass/fail list (evidence package, diff refs, readable patch with real changes,
 * passing QA, readable non-blocking security findings). `complete` is the AND of
 * every check and gates Approve; `reasons` drives the operator-visible checklist.
 * When no gate applies the action is trivially complete.
 */
export function evidenceCompleteness({
	action,
	artifacts,
	diffError,
	diffLoading,
	diffPayload,
	evidence,
	securityError,
	securityLoading,
	securityPayload,
}: {
	action: ActionRequest;
	artifacts: Artifact[];
	diffError: string;
	diffLoading: boolean;
	diffPayload: ArtifactPayload | null;
	evidence: Overview['evidencePackages'];
	securityError: string;
	securityLoading: boolean;
	securityPayload: ArtifactPayload | null;
}) {
	const required = requiresPatchEvidenceGate(action);
	if (!required)
		return {
			required,
			complete: true,
			reasons: ['No patch evidence gate is required for this action request.'],
		};
	const patchArtifact = findPatchArtifact(artifacts);
	const securityArtifact = findSecurityFindingsArtifact(artifacts);
	const hasEvidencePackage = evidence.length > 0;
	const hasDiffRefs =
		evidence.some((item) => Array.isArray(item.diffRefs) && item.diffRefs.length > 0) ||
		Boolean(action.diffRefs?.length);
	const hasPassingQa = evidence.some(evidenceHasPassingQa);
	const hasPatchArtifact = Boolean(patchArtifact);
	const hasPatchHash = Boolean(patchArtifact?.hash || diffPayload?.hash);
	const hasLoadedPatch = Boolean(diffPayload?.text);
	const hasPatchChanges = hasLoadedPatch && hasRealPatchChanges(diffPayload?.text ?? '');
	const hasSecurityArtifact = Boolean(securityArtifact);
	const hasSecurityHash = Boolean(securityArtifact?.hash || securityPayload?.hash);
	const hasNonBlockingSecurityFindings = securityFindingsAreNonBlocking(securityPayload);
	const checks = [
		{ ok: hasEvidencePackage, message: 'linked evidence package recorded' },
		{ ok: hasDiffRefs, message: 'diff refs recorded' },
		{ ok: hasPatchArtifact, message: 'patch artifact linked' },
		{ ok: !diffLoading, message: 'patch artifact loaded' },
		{ ok: !diffError, message: diffError || 'patch artifact readable' },
		{ ok: hasPatchHash, message: 'patch artifact hash recorded' },
		{ ok: hasPatchChanges, message: 'patch artifact contains real unified diff changes' },
		{ ok: hasPassingQa, message: 'QA command evidence passed' },
		{ ok: hasSecurityArtifact, message: 'security findings artifact linked' },
		{ ok: !securityLoading, message: 'security findings artifact loaded' },
		{ ok: !securityError, message: securityError || 'security findings artifact readable' },
		{ ok: hasSecurityHash, message: 'security findings artifact hash recorded' },
		{ ok: hasNonBlockingSecurityFindings, message: 'security findings are non-blocking' },
	];
	return {
		required,
		complete: checks.every((check) => check.ok),
		reasons: checks.map((check) => `${check.ok ? 'ok' : 'missing'}: ${check.message}`),
	};
}

// ----------------------------------------------------------------------------
// Review-item derivation.
// ----------------------------------------------------------------------------

/** Risk → tone. `toneForStatus` only maps `critical`, so risk needs its own map. */
export function riskTone(level: string | null | undefined): Tone {
	if (level === 'critical' || level === 'high') return 'danger';
	if (level === 'medium') return 'warn';
	return 'info';
}

function parseTime(value: unknown): number {
	const parsed = Date.parse(String(value ?? ''));
	return Number.isFinite(parsed) ? parsed : 0;
}

function mostRecentEvidence(
	evidence: Overview['evidencePackages'],
): Overview['evidencePackages'][number] | null {
	if (!evidence.length) return null;
	return [...evidence].sort(
		(left, right) => parseTime(right.createdAt) - parseTime(left.createdAt),
	)[0];
}

function evidenceHasDiffRefs(evidence: Overview['evidencePackages']): boolean {
	return evidence.some((item) => Array.isArray(item.diffRefs) && item.diffRefs.length > 0);
}

/**
 * Joins workflow runs, workflows, actions, evidence and artifacts into one
 * card-friendly list. Three passes with a shared `claimed` set guarantee each
 * action surfaces on exactly one card (no double counting):
 *   1. patch-workflow runs (the primary cards), claiming every action bound to
 *      the run via `payload.workflowRunId`;
 *   2. standalone pending actions not already claimed;
 *   3. recent decisions (non-pending, unclaimed actions), bounded to DONE_LIMIT.
 */
export function buildReviewItems(overview: Overview): ReviewItem[] {
	const projectName = new Map(overview.projects.map((project) => [project.id, project.name]));
	const workflowById = new Map(overview.workflows.map((workflow) => [workflow.id, workflow]));
	const resolveProject = (projectId: string) => projectName.get(projectId) ?? shortId(projectId);

	const items: ReviewItem[] = [];
	const claimed = new Set<string>();

	// Pass 1 — patch-workflow runs.
	for (const run of overview.workflowRuns) {
		const workflow = workflowById.get(run.workflowId);
		const kind = patchWorkflowKind(workflow?.kind);
		if (!workflow || !kind) continue;

		const evidence = overview.evidencePackages.filter(
			(item) => String(item.workflowRunId ?? '') === run.id,
		);
		const evidenceIds = new Set(evidence.map((item) => String(item.id ?? '')));
		const artifacts = overview.artifacts.filter((artifact) =>
			evidenceIds.has(String(artifact.evidencePackageId ?? '')),
		);
		const bestEvidence = mostRecentEvidence(evidence);

		const runActions = overview.actionRequests.filter(
			(action) => patchWorkflowApproval(action)?.runId === run.id,
		);
		for (const action of runActions) claimed.add(action.id);
		const pending = runActions.find((action) => action.status === 'pending') ?? null;

		const projectId = pending?.projectId || run.projectId;
		const runLabel =
			(workflow.title && workflow.title.trim()) || `${workflow.kind} · ${shortId(run.id)}`;

		items.push({
			key: `run:${run.id}`,
			source: 'patch_workflow',
			projectId,
			projectName: resolveProject(projectId),
			runLabel,
			workflowKind: workflow.kind,
			runStatus: run.status,
			riskLevel: pending?.riskLevel ?? null,
			qaVerdict: bestEvidence ? String(bestEvidence.qaVerdict ?? '') || null : null,
			runId: run.id,
			actionId: pending?.id ?? null,
			jobId: pending?.jobId ?? null,
			evidencePackageId: bestEvidence ? String(bestEvidence.id ?? '') || null : null,
			hasDiff: Boolean(findPatchArtifact(artifacts)) || evidenceHasDiffRefs(evidence),
			hasEvidence: evidence.length > 0,
			requiresPatchGate: pending ? requiresPatchEvidenceGate(pending) : false,
			canDecide: Boolean(pending?.id && pending?.jobId),
			decisionStatus: null,
			decidedBy: null,
			decidedAt: null,
			decisionReason: null,
			sortTime: Math.max(
				parseTime(run.completedAt),
				parseTime(run.startedAt),
				parseTime(pending?.requestedAt),
			),
		});
	}

	// Pass 2 — standalone pending actions not bound to a pass-1 run.
	for (const action of overview.actionRequests) {
		if (action.status !== 'pending' || claimed.has(action.id)) continue;
		claimed.add(action.id);
		const evidence = linkedEvidence(action, overview);
		const artifacts = linkedArtifacts(action, overview);
		const bestEvidence = mostRecentEvidence(evidence);
		items.push({
			key: `action:${action.id}`,
			source: 'standalone_action',
			projectId: action.projectId,
			projectName: resolveProject(action.projectId),
			runLabel: action.actionType,
			workflowKind: null,
			runStatus: null,
			riskLevel: action.riskLevel,
			qaVerdict: bestEvidence ? String(bestEvidence.qaVerdict ?? '') || null : null,
			runId: null,
			actionId: action.id,
			jobId: action.jobId,
			evidencePackageId: bestEvidence ? String(bestEvidence.id ?? '') || null : null,
			hasDiff: Boolean(findPatchArtifact(artifacts)) || Boolean(action.diffRefs?.length),
			hasEvidence: evidence.length > 0,
			requiresPatchGate: requiresPatchEvidenceGate(action),
			canDecide: Boolean(action.id && action.jobId),
			decisionStatus: null,
			decidedBy: null,
			decidedAt: null,
			decisionReason: null,
			sortTime: parseTime(action.requestedAt),
		});
	}

	// Pass 3 — recent decisions (bounded).
	const decided = overview.actionRequests
		.filter((action) => action.status !== 'pending' && !claimed.has(action.id))
		.sort(
			(left, right) =>
				parseTime(right.decidedAt ?? right.requestedAt) -
				parseTime(left.decidedAt ?? left.requestedAt),
		)
		.slice(0, DONE_LIMIT);
	for (const action of decided) {
		items.push({
			key: `action:${action.id}`,
			source: 'decided_action',
			projectId: action.projectId,
			projectName: resolveProject(action.projectId),
			runLabel: action.actionType,
			workflowKind: null,
			runStatus: null,
			riskLevel: action.riskLevel,
			qaVerdict: null,
			runId: null,
			actionId: action.id,
			jobId: action.jobId,
			evidencePackageId: null,
			hasDiff: Boolean(action.diffRefs?.length),
			hasEvidence: Boolean(action.evidenceRefs?.length),
			requiresPatchGate: false,
			canDecide: false,
			decisionStatus: action.status,
			decidedBy: action.decidedBy ?? null,
			decidedAt: action.decidedAt ?? null,
			decisionReason: action.reason || null,
			sortTime: parseTime(action.decidedAt ?? action.requestedAt),
		});
	}

	return items;
}

/**
 * Maps a review item to exactly one board column from the real lifecycle
 * statuses. A pending human action always outranks run health (the human must
 * act), so it lands in Needs review with run health shown as a badge — never
 * silently parked in Blocked. This is a lifecycle map, not `toneForStatus`.
 */
export function classifyReviewItem(item: ReviewItem): ReviewColumn {
	if (
		item.decisionStatus === 'approved' ||
		item.decisionStatus === 'denied' ||
		item.decisionStatus === 'expired'
	)
		return 'done';
	if (item.runStatus === 'completed' || item.runStatus === 'pr_created') return 'done';
	if (item.canDecide) return 'needs_review';
	switch (item.runStatus) {
		case 'failed':
		case 'cancelled':
		case 'blocked':
		case 'runtime_unavailable':
		case 'qa_failed':
		case 'promotion_failed':
			return 'blocked';
		case 'approved_for_integration':
		case 'promoted_to_branch':
			return 'ready';
		case 'evidence_ready':
		case 'running':
		default:
			return 'needs_review';
	}
}

/** Risk descending, then most-recent first. */
export function sortReviewItems(items: ReviewItem[]): ReviewItem[] {
	return [...items].sort((left, right) => {
		const risk = (RISK_RANK[right.riskLevel ?? ''] ?? 0) - (RISK_RANK[left.riskLevel ?? ''] ?? 0);
		if (risk !== 0) return risk;
		return right.sortTime - left.sortTime;
	});
}

export const REVIEW_COLUMNS: ReviewColumn[] = ['needs_review', 'blocked', 'ready', 'done'];

export const COLUMN_TONE: Record<ReviewColumn, Tone> = {
	needs_review: 'warn',
	blocked: 'danger',
	ready: 'info',
	done: 'ok',
};

/** Groups items into the ordered columns, each internally sorted. */
export function groupReviewItems(items: ReviewItem[]): Record<ReviewColumn, ReviewItem[]> {
	const groups: Record<ReviewColumn, ReviewItem[]> = {
		needs_review: [],
		blocked: [],
		ready: [],
		done: [],
	};
	for (const item of items) groups[classifyReviewItem(item)].push(item);
	for (const column of REVIEW_COLUMNS) groups[column] = sortReviewItems(groups[column]);
	return groups;
}
