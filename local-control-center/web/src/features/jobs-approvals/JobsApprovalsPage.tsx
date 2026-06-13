import { useEffect, useMemo, useState } from 'react';

import {
	approveAction,
	approveIssueToPatch,
	approveIssueToPr,
	cancelJob,
	createPullRequestFromPromotedBranch,
	createPullRequestFromIssueToPr,
	denyAction,
	downloadEvidenceArtifact,
	fetchEvidenceArtifact,
	promoteIssueToPrBranch,
	promotePatchToBranch,
	retryJob,
} from '../../api/client';
import type { ArtifactPayload } from '../../api/client';
import type { ActionRequest, Artifact, Job, Overview } from '../../api/types';
import { Badge, DataTable, Drawer, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { artifactDisplayName, artifactMimeType, artifactSizeLabel } from '../../lib/artifacts';
import { findPatchArtifact, hasRealPatchChanges } from '../../lib/diff';
import { shortId, toneForStatus } from '../../lib/format';
import { redactVisibleText } from '../../lib/redaction';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;
type Refresh = (silent?: boolean) => Promise<void>;
type PatchWorkflowKind = 'issue_to_patch' | 'issue_to_pr';
type PatchWorkflowApproval = { kind: PatchWorkflowKind; runId: string };

function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function prettyJson(value: unknown): string {
	return JSON.stringify(value ?? null, null, 2);
}

function referenceIds(action: ActionRequest): Set<string> {
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

function linkedArtifacts(action: ActionRequest, overview: Overview): Artifact[] {
	const ids = referenceIds(action);
	return overview.artifacts.filter((artifact) => {
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		return ids.has(artifactId) || ids.has(evidenceId);
	});
}

function linkedEvidence(action: ActionRequest, overview: Overview) {
	const ids = referenceIds(action);
	return overview.evidencePackages.filter((evidence) => ids.has(String(evidence.id ?? '')) || String(evidence.jobId ?? '') === action.jobId);
}

function patchWorkflowKind(value: unknown): PatchWorkflowKind | null {
	return value === 'issue_to_patch' || value === 'issue_to_pr' ? value : null;
}

function patchWorkflowApproval(action: ActionRequest): PatchWorkflowApproval | null {
	const kind = action.actionType === 'workflow.issue_to_patch.approve_patch'
		? 'issue_to_patch'
		: action.actionType === 'workflow.issue_to_pr.approve_issue_to_pr'
			? 'issue_to_pr'
			: null;
	if (!kind) return null;
	const payload = asRecord(action.payload);
	const workflowRunId = payload.workflowRunId;
	return typeof workflowRunId === 'string' && workflowRunId.trim() ? { kind, runId: workflowRunId } : null;
}

function requiresPatchEvidenceGate(action: ActionRequest): boolean {
	return Boolean(patchWorkflowApproval(action)) || action.actionType === 'agent.developer.approve_patch';
}

function isSecurityFindingsArtifact(artifact: Artifact): boolean {
	const name = artifactDisplayName(artifact).toLowerCase();
	const kind = String(artifact.kind ?? '').toLowerCase();
	return name === 'security-findings.json' || kind.includes('security_findings');
}

function findSecurityFindingsArtifact(artifacts: Artifact[]) {
	return artifacts.find(isSecurityFindingsArtifact) ?? null;
}

function evidenceHasPassingQa(evidence: Overview['evidencePackages'][number]): boolean {
	return (evidence.testResults ?? []).some((result) => {
		const record = asRecord(result);
		const status = String(record.status ?? '').toLowerCase();
		return status === 'passed';
	});
}

function securityFindingsAreNonBlocking(payload: ArtifactPayload | null): boolean {
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

function evidenceCompleteness({
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
	if (!required) return { required, complete: true, reasons: ['No patch evidence gate is required for this action request.'] };
	const patchArtifact = findPatchArtifact(artifacts);
	const securityArtifact = findSecurityFindingsArtifact(artifacts);
	const hasEvidencePackage = evidence.length > 0;
	const hasDiffRefs = evidence.some((item) => Array.isArray(item.diffRefs) && item.diffRefs.length > 0) || Boolean(action.diffRefs?.length);
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

function DetailRow({ label, value }: { label: string; value: string }) {
	return (
		<div className="diff-row" role="row">
			<div role="cell" className="mono">{label}</div>
			<div role="cell">{value || 'not recorded'}</div>
		</div>
	);
}

export function JobsApprovalsPage({
	overview,
	token,
	mutate,
	refresh,
}: {
	overview: Overview;
	token: string;
	mutate: Mutate;
	refresh: Refresh;
}) {
	const [selectedActionId, setSelectedActionId] = useState('');
	const [decisionReason, setDecisionReason] = useState('');
	const [decisionError, setDecisionError] = useState('');
	const [jobReason, setJobReason] = useState('');
	const [previewArtifact, setPreviewArtifact] = useState<Artifact | null>(null);
	const [previewPayload, setPreviewPayload] = useState<ArtifactPayload | null>(null);
	const [previewLoadingId, setPreviewLoadingId] = useState('');
	const [downloadLoadingId, setDownloadLoadingId] = useState('');
	const [previewError, setPreviewError] = useState('');
	const [patchPayload, setPatchPayload] = useState<ArtifactPayload | null>(null);
	const [patchLoadingId, setPatchLoadingId] = useState('');
	const [patchError, setPatchError] = useState('');
	const [securityPayload, setSecurityPayload] = useState<ArtifactPayload | null>(null);
	const [securityLoadingId, setSecurityLoadingId] = useState('');
	const [securityError, setSecurityError] = useState('');
	const [workflowOperationReason, setWorkflowOperationReason] = useState('');
	const [promotionBranchName, setPromotionBranchName] = useState('');
	const [pullRequestTitle, setPullRequestTitle] = useState('');
	const [pullRequestBaseBranch, setPullRequestBaseBranch] = useState('');
	const [workflowOperationError, setWorkflowOperationError] = useState('');
	const [workflowOperationBusyId, setWorkflowOperationBusyId] = useState('');
	const [lastOperation, setLastOperation] = useState<{ status: string; reason: string; runId: string } | null>(null);
	const pendingActions = overview.actionRequests.filter((item) => item.status === 'pending');
	const selectedAction = pendingActions.find((item) => item.id === selectedActionId) ?? null;
	const selectedArtifacts = useMemo(() => selectedAction ? linkedArtifacts(selectedAction, overview) : [], [overview, selectedAction]);
	const selectedEvidence = useMemo(() => selectedAction ? linkedEvidence(selectedAction, overview) : [], [overview, selectedAction]);
	const selectedPatchArtifact = useMemo(() => findPatchArtifact(selectedArtifacts), [selectedArtifacts]);
	const selectedSecurityArtifact = useMemo(() => findSecurityFindingsArtifact(selectedArtifacts), [selectedArtifacts]);
	const patchGate = useMemo(() => selectedAction
		? evidenceCompleteness({
			action: selectedAction,
			artifacts: selectedArtifacts,
			diffError: patchError,
			diffLoading: Boolean(patchLoadingId),
			diffPayload: patchPayload,
			evidence: selectedEvidence,
			securityError,
			securityLoading: Boolean(securityLoadingId),
			securityPayload,
		})
		: null, [patchError, patchLoadingId, patchPayload, securityError, securityLoadingId, securityPayload, selectedAction, selectedArtifacts, selectedEvidence]);
	const workflowOperations = useMemo(() => {
		const workflows = new Map(overview.workflows.map((workflow) => [workflow.id, workflow]));
		return overview.workflowRuns
			.map((run) => {
				const workflow = workflows.get(run.workflowId);
				const kind = patchWorkflowKind(workflow?.kind);
				if (!workflow || !kind) return null;
				const metadata = asRecord(run.metadata);
				return {
					kind,
					run,
					workflow,
					evidencePackageId: String(metadata.promotionEvidencePackageId ?? metadata.originalEvidencePackageId ?? metadata.evidencePackageId ?? ''),
					branchName: String(metadata.promotedBranchName ?? ''),
				};
			})
			.filter((item): item is NonNullable<typeof item> => Boolean(item))
			.sort((left, right) => Date.parse(String(right.run.startedAt ?? '')) - Date.parse(String(left.run.startedAt ?? '')));
	}, [overview.workflowRuns, overview.workflows]);
	const trimmedDecisionReason = decisionReason.trim();
	const decisionBlocked = !trimmedDecisionReason;
	const approveBlocked = decisionBlocked || Boolean(patchGate?.required && !patchGate.complete);
	const trimmedWorkflowOperationReason = workflowOperationReason.trim();
	useEffect(() => {
		setPatchPayload(null);
		setPatchError('');
		const artifact = selectedPatchArtifact;
		if (!selectedAction || !artifact || !requiresPatchEvidenceGate(selectedAction)) {
			setPatchLoadingId('');
			return undefined;
		}
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setPatchError('Patch artifact metadata is incomplete.');
			return undefined;
		}
		let cancelled = false;
		setPatchLoadingId(artifactId);
		void fetchEvidenceArtifact(token, evidenceId, artifactId)
			.then((payload) => {
				if (!cancelled) setPatchPayload(payload);
			})
			.catch((error) => {
				if (!cancelled) setPatchError(error instanceof Error ? error.message : 'Patch artifact preview failed.');
			})
			.finally(() => {
				if (!cancelled) setPatchLoadingId('');
			});
		return () => {
			cancelled = true;
		};
	}, [selectedAction, selectedPatchArtifact, token]);
	useEffect(() => {
		setSecurityPayload(null);
		setSecurityError('');
		const artifact = selectedSecurityArtifact;
		if (!selectedAction || !artifact || !requiresPatchEvidenceGate(selectedAction)) {
			setSecurityLoadingId('');
			return undefined;
		}
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setSecurityError('Security findings artifact metadata is incomplete.');
			return undefined;
		}
		let cancelled = false;
		setSecurityLoadingId(artifactId);
		void fetchEvidenceArtifact(token, evidenceId, artifactId)
			.then((payload) => {
				if (!cancelled) setSecurityPayload(payload);
			})
			.catch((error) => {
				if (!cancelled) setSecurityError(error instanceof Error ? error.message : 'Security findings preview failed.');
			})
			.finally(() => {
				if (!cancelled) setSecurityLoadingId('');
			});
		return () => {
			cancelled = true;
		};
	}, [selectedAction, selectedSecurityArtifact, token]);

	const run = (operation: (token: string) => Promise<unknown>) => {
		void mutate(operation);
	};
	const openReview = (action: ActionRequest) => {
		setSelectedActionId(action.id);
		setDecisionReason('');
		setDecisionError('');
		setPreviewArtifact(null);
		setPreviewPayload(null);
		setPreviewError('');
		setPatchPayload(null);
		setPatchError('');
		setPatchLoadingId('');
		setSecurityPayload(null);
		setSecurityError('');
		setSecurityLoadingId('');
	};
	const closeReview = () => {
		setSelectedActionId('');
		setDecisionReason('');
		setDecisionError('');
		setPreviewArtifact(null);
		setPreviewPayload(null);
		setPreviewError('');
		setPatchPayload(null);
		setPatchError('');
		setPatchLoadingId('');
		setSecurityPayload(null);
		setSecurityError('');
		setSecurityLoadingId('');
	};
	const recordOperation = (result: unknown, fallbackStatus: string) => {
		const record = asRecord(result);
		const workflowRun = asRecord(record.workflowRun);
		setLastOperation({
			status: String(record.status ?? workflowRun.status ?? fallbackStatus),
			reason: String(record.reason ?? ''),
			runId: String(workflowRun.id ?? ''),
		});
	};
	const decide = async (kind: 'approve' | 'reject') => {
		if (!selectedAction) return;
		if (!trimmedDecisionReason) {
			setDecisionError('A human reason is required before this request can be decided.');
			return;
		}
		if (kind === 'approve' && patchGate?.required && !patchGate.complete) {
			setDecisionError('Complete linked evidence, a readable non-empty patch artifact, passing QA evidence, and non-blocking security findings are required before approve patch.');
			return;
		}
		setDecisionError('');
		try {
			const result = await mutate(async (writeToken) => {
				if (kind === 'reject') {
					return denyAction(writeToken, selectedAction.jobId, selectedAction.id, trimmedDecisionReason);
				}
				const approved = await approveAction(writeToken, selectedAction.jobId, selectedAction.id, trimmedDecisionReason);
				const workflowApproval = patchWorkflowApproval(selectedAction);
				if (workflowApproval?.kind === 'issue_to_patch') {
					return approveIssueToPatch(writeToken, workflowApproval.runId, trimmedDecisionReason);
				}
				if (workflowApproval?.kind === 'issue_to_pr') {
					return approveIssueToPr(writeToken, workflowApproval.runId, trimmedDecisionReason);
				}
				return approved;
			});
			recordOperation(result, kind === 'reject' ? 'denied' : 'approved');
			closeReview();
		} catch (error) {
			setDecisionError(error instanceof Error ? error.message : 'Decision failed.');
		}
	};
	const runWorkflowOperation = async (
		operation: 'promote' | 'pull-request',
		runId: string,
		workflowKind: PatchWorkflowKind,
	) => {
		if (!trimmedWorkflowOperationReason) {
			setWorkflowOperationError('Workflow operation reason is required.');
			return;
		}
		setWorkflowOperationError('');
		setWorkflowOperationBusyId(`${operation}:${workflowKind}:${runId}`);
		try {
			if (operation === 'promote') {
				const branchName = promotionBranchName.trim();
				const body = {
					reason: trimmedWorkflowOperationReason,
					...(branchName ? { branchName } : {}),
				};
				const result = workflowKind === 'issue_to_pr'
					? await promoteIssueToPrBranch(token, runId, body)
					: await promotePatchToBranch(token, runId, body);
				recordOperation(result, 'promoted_to_branch');
			} else {
				const title = pullRequestTitle.trim();
				const baseBranch = pullRequestBaseBranch.trim();
				const body = {
					reason: trimmedWorkflowOperationReason,
					...(title ? { title } : {}),
					...(baseBranch ? { baseBranch } : {}),
				};
				const result = workflowKind === 'issue_to_pr'
					? await createPullRequestFromIssueToPr(token, runId, body)
					: await createPullRequestFromPromotedBranch(token, runId, body);
				recordOperation(result, 'pull_request_requested');
			}
			void refresh(true).catch(() => undefined);
		} catch (error) {
			setWorkflowOperationError(error instanceof Error ? error.message : 'Workflow operation failed.');
		} finally {
			setWorkflowOperationBusyId('');
		}
	};
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

	return (
		<>
			<PageHeader
				kicker="Human gates"
				title="Jobs & Approvals"
				summary="Queue leases and command-level approvals. Approving a job never approves all sensitive actions by implication."
			/>
			<div className="grid two">
				<Surface title="Pending action requests">
					<DataTable<ActionRequest>
						rows={pendingActions}
						empty={<EmptyState title="No pending approvals" body="Sensitive commands will appear here before execution." />}
						columns={[
							{ key: 'kind', label: 'Action', render: (row) => <span className="mono">{row.actionType}</span> },
							{ key: 'risk', label: 'Risk', render: (row) => <Badge tone={toneForStatus(row.riskLevel)}>{row.riskLevel}</Badge> },
							{ key: 'command', label: 'Command', render: (row) => <span className="mono">{row.command || 'n/a'}</span> },
							{ key: 'scope', label: 'Scope', render: (row) => <span className="mono">{row.runtimeId || 'runtime n/a'} · {shortId(row.workspaceId || row.jobId)}</span> },
							{
								key: 'actions',
								label: 'Review',
								render: (row) => (
									<button className="button primary" type="button" onClick={() => openReview(row)}>
										Review request
									</button>
								),
							},
						]}
					/>
				</Surface>
				<Surface title="Queue state">
					<div className="field">
						<label htmlFor="job-mutation-reason">Queue mutation reason</label>
						<textarea id="job-mutation-reason" className="textarea" value={jobReason} onChange={(event) => setJobReason(event.target.value)} />
					</div>
					<DataTable<Job>
						rows={overview.jobs}
						empty={<EmptyState title="No jobs" body="Create or start a workflow to populate the queue." />}
						columns={[
							{ key: 'kind', label: 'Kind', render: (row) => <span className="mono">{row.kind}</span> },
							{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
							{ key: 'lease', label: 'Lease', render: (row) => <span>{row.leaseOwner ? `${row.leaseOwner} until ${row.leaseExpiresAt}` : 'none'}</span> },
							{
								key: 'actions',
								label: 'Actions',
								render: (row) => (
									<div className="inline">
										<button className="button" type="button" onClick={() => run((writeToken) => retryJob(writeToken, row.id, jobReason.trim()))}>Retry</button>
										<button className="button" type="button" onClick={() => run((writeToken) => cancelJob(writeToken, row.id, jobReason.trim()))}>Cancel</button>
									</div>
								),
							},
						]}
					/>
				</Surface>
			</div>
			<Surface title="Patch workflow operations">
				<div className="grid two">
					<div className="field">
						<label htmlFor="workflow-operation-reason">Workflow operation reason</label>
						<textarea
							id="workflow-operation-reason"
							className="textarea"
							value={workflowOperationReason}
							onChange={(event) => {
								setWorkflowOperationReason(event.target.value);
								setWorkflowOperationError('');
							}}
						/>
						<div className="field-help">Promotion and PR creation stay disabled until a reason is recorded.</div>
					</div>
					<div className="stack">
						<div className="field">
							<label htmlFor="promotion-branch-name">Promotion branch (optional)</label>
							<input id="promotion-branch-name" className="input" value={promotionBranchName} onChange={(event) => setPromotionBranchName(event.target.value)} />
						</div>
						<div className="grid two">
							<div className="field">
								<label htmlFor="pull-request-title">PR title (optional)</label>
								<input id="pull-request-title" className="input" value={pullRequestTitle} onChange={(event) => setPullRequestTitle(event.target.value)} />
							</div>
							<div className="field">
								<label htmlFor="pull-request-base">PR base branch (optional)</label>
								<input id="pull-request-base" className="input" value={pullRequestBaseBranch} onChange={(event) => setPullRequestBaseBranch(event.target.value)} />
							</div>
						</div>
					</div>
				</div>
				{workflowOperationError ? <div className="form-error" role="alert">{workflowOperationError}</div> : null}
				{lastOperation ? (
					<div className="inline" role="status">
						<Badge tone={toneForStatus(lastOperation.status)}>{lastOperation.status}</Badge>
						<span>{lastOperation.reason || 'operation recorded'}</span>
						{lastOperation.runId ? <span className="mono">{shortId(lastOperation.runId)}</span> : null}
					</div>
				) : null}
				<DataTable
					rows={workflowOperations}
					empty={<EmptyState title="No patch workflow operations" body="Approve a reviewed patch before branch promotion and PR creation become available." />}
					columns={[
						{ key: 'workflow', label: 'Workflow', render: (row) => row.workflow.title },
						{ key: 'kind', label: 'Kind', render: (row) => <span className="mono">{row.kind}</span> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.run.status)}>{row.run.status}</Badge> },
						{
							key: 'evidence',
							label: 'Evidence / branch',
							render: (row) => (
								<div className="stack compact">
									<span className="mono">{row.evidencePackageId || 'evidence not recorded'}</span>
									<span className="mono">{row.branchName || 'branch not promoted'}</span>
								</div>
							),
						},
						{
							key: 'operation',
							label: 'Operation',
							render: (row) => {
								const canPromote = row.run.status === 'approved_for_integration' || row.run.status === 'promotion_failed';
								const canCreatePr = row.run.status === 'promoted_to_branch';
								const promoteBusy = workflowOperationBusyId === `promote:${row.kind}:${row.run.id}`;
								const prBusy = workflowOperationBusyId === `pull-request:${row.kind}:${row.run.id}`;
								return (
									<div className="inline">
										<button
											className="button"
											type="button"
											aria-label={`Promote branch for ${row.workflow.title}`}
											disabled={!canPromote || !trimmedWorkflowOperationReason || Boolean(workflowOperationBusyId)}
											onClick={() => void runWorkflowOperation('promote', row.run.id, row.kind)}
										>
											{promoteBusy ? 'Promoting branch' : 'Promote branch'}
										</button>
										<button
											className="button primary"
											type="button"
											aria-label={`Create PR for ${row.workflow.title}`}
											disabled={!canCreatePr || !trimmedWorkflowOperationReason || Boolean(workflowOperationBusyId)}
											onClick={() => void runWorkflowOperation('pull-request', row.run.id, row.kind)}
										>
											{prBusy ? 'Creating PR' : 'Create PR'}
										</button>
									</div>
								);
							},
						},
					]}
				/>
			</Surface>
			<Surface title="Audit trail">
				<DataTable
					rows={overview.auditEvents.slice(0, 12)}
					empty={<EmptyState title="No audit entries" body="Mutating decisions are recorded here." />}
					columns={[
						{ key: 'action', label: 'Action', render: (row) => <span className="mono">{String(row.action ?? '')}</span> },
						{ key: 'target', label: 'Target', render: (row) => <span>{shortId(String(row.target ?? ''))}</span> },
						{ key: 'actor', label: 'Actor', render: (row) => <span>{String(row.actor ?? 'system')}</span> },
					]}
				/>
			</Surface>
			<Drawer label="Action request review" open={Boolean(selectedAction)} onClose={closeReview}>
				<div className="drawer-body">
					{selectedAction ? (
						<div className="stack">
							<div className="inline">
								<Badge tone={toneForStatus(selectedAction.riskLevel)}>{selectedAction.riskLevel}</Badge>
								<Badge>{selectedAction.actionType}</Badge>
								<Badge>{selectedAction.expiresAt ? `expires ${selectedAction.expiresAt}` : 'no expiration recorded'}</Badge>
							</div>
							<div className="diff-grid" role="table" aria-label="Action request scope">
								<DetailRow label="Command" value={selectedAction.command || 'not recorded'} />
								<DetailRow label="Argv" value={(selectedAction.commandArgv ?? []).join(' ')} />
								<DetailRow label="Workspace" value={`${selectedAction.workspaceId || 'not recorded'} ${selectedAction.workspacePath || ''}`.trim()} />
								<DetailRow label="Runtime" value={`${selectedAction.runtimeId || 'not recorded'} ${prettyJson(selectedAction.runtime)}`} />
								<DetailRow label="Job" value={selectedAction.jobId} />
								<DetailRow label="Project" value={selectedAction.projectId} />
							</div>
							<Surface title="Policy reason" flat>
								<pre className="artifact-preview">{selectedAction.reason}</pre>
							</Surface>
							<Surface title="Diff and evidence references" flat>
								<div className="grid two">
									<div>
										<div className="field-label">Evidence refs</div>
										<pre className="artifact-preview">{prettyJson(selectedAction.evidenceRefs ?? [])}</pre>
									</div>
									<div>
										<div className="field-label">Diff refs</div>
										<pre className="artifact-preview">{prettyJson(selectedAction.diffRefs ?? [])}</pre>
									</div>
								</div>
							</Surface>
							<Surface title="Linked evidence packages" flat>
								<DataTable
									rows={selectedEvidence}
									empty={<EmptyState title="No linked evidence packages" body="This request did not record evidence package references." />}
									columns={[
										{ key: 'id', label: 'Evidence', render: (row) => <span className="mono">{String(row.id ?? '')}</span> },
										{ key: 'verdict', label: 'QA', render: (row) => <Badge tone={toneForStatus(String(row.qaVerdict ?? ''))}>{String(row.qaVerdict ?? '')}</Badge> },
										{ key: 'source', label: 'Source', render: (row) => <span className="mono">{String(row.evidenceSource ?? 'operator_attested')}</span> },
										{ key: 'task', label: 'Task', render: (row) => String(row.taskId ?? '') },
									]}
								/>
							</Surface>
							<Surface title="Linked artifacts" flat>
								<DataTable
									rows={selectedArtifacts}
									empty={<EmptyState title="No linked artifacts" body="Diff and evidence artifacts must be attached by the requesting runtime." />}
									columns={[
										{ key: 'name', label: 'Name', render: (row) => artifactDisplayName(row) },
										{ key: 'kind', label: 'Kind', render: (row) => <span className="mono">{String(row.kind ?? '')}</span> },
										{ key: 'hash', label: 'Hash', render: (row) => <span className="mono">{String(row.hash ?? '').slice(0, 12) || 'not recorded'}</span> },
										{
											key: 'action',
											label: 'Action',
											render: (row) => {
												const name = artifactDisplayName(row);
												const loading = previewLoadingId === String(row.id ?? '');
												const downloading = downloadLoadingId === String(row.id ?? '');
												return (
													<div className="inline" aria-busy={loading || downloading}>
														<button className="button" type="button" aria-label={`Preview artifact ${name}`} disabled={loading} onClick={() => void openPreview(row)}>
															{loading ? 'Opening' : 'Preview'}
														</button>
														<button className="button" type="button" aria-label={`Download artifact ${name}`} disabled={downloading} onClick={() => void downloadArtifact(row)}>
															{downloading ? 'Downloading' : 'Download'}
														</button>
													</div>
												);
											},
										},
									]}
								/>
							</Surface>
							{patchGate?.required ? (
								<Surface title="Evidence completeness" flat>
									<div className="inline">
										<Badge tone={patchGate.complete ? 'ok' : 'warn'}>{patchGate.complete ? 'evidence_complete' : 'evidence_incomplete'}</Badge>
										<span>{patchGate.complete ? 'Approve patch can proceed after a human reason.' : 'Approve patch is blocked until evidence is complete.'}</span>
									</div>
									<pre className="artifact-preview">{patchGate.reasons.join('\n')}</pre>
								</Surface>
							) : null}
							{patchGate?.required ? (
								<Surface title="Full diff before approval" flat>
									{patchLoadingId ? <EmptyState title="Loading patch artifact" body="The diff is read through the protected evidence artifact endpoint." /> : null}
									{patchError ? <div className="form-error" role="alert">{patchError}</div> : null}
									{!selectedPatchArtifact ? <EmptyState title="No patch artifact recorded" body="A patch approval requires a linked diff artifact before the approve patch action is enabled." /> : null}
									{selectedPatchArtifact && patchPayload?.text && !hasRealPatchChanges(patchPayload.text) ? <EmptyState title="Patch artifact has no proven changes" body="The patch artifact is empty or malformed, so approve patch remains blocked." /> : null}
									{patchPayload?.text ? <pre className="artifact-preview">{redactVisibleText(patchPayload.text, '')}</pre> : null}
								</Surface>
							) : null}
							{patchGate?.required ? (
								<Surface title="Security findings before approval" flat>
									{securityLoadingId ? <EmptyState title="Loading security findings" body="Security evidence is read through the protected evidence artifact endpoint." /> : null}
									{securityError ? <div className="form-error" role="alert">{securityError}</div> : null}
									{!selectedSecurityArtifact ? <EmptyState title="No security findings recorded" body="Patch approval requires linked non-blocking security findings before the approve patch action is enabled." /> : null}
									{selectedSecurityArtifact && securityPayload?.text && !securityFindingsAreNonBlocking(securityPayload) ? <EmptyState title="Security findings are blocking or unreadable" body="The security findings artifact must be valid JSON with no blocking policy decision." /> : null}
									{securityPayload?.text ? <pre className="artifact-preview">{redactVisibleText(securityPayload.text, '')}</pre> : null}
								</Surface>
							) : null}
							<div className="field">
								<label htmlFor="action-decision-reason">Human decision reason</label>
								<textarea
									id="action-decision-reason"
									className="textarea"
									value={decisionReason}
									onChange={(event) => {
										setDecisionReason(event.target.value);
										setDecisionError('');
									}}
								/>
								<div className="field-help">{patchGate?.required ? 'Approve patch also requires complete linked evidence, non-blocking security findings, and a real diff. Reject only requires a recorded reason.' : 'Approve or reject is blocked until this reason is recorded.'}</div>
							</div>
							{decisionError ? <div className="form-error" role="alert">{decisionError}</div> : null}
							<div className="inline">
								<button className="button primary" type="button" disabled={approveBlocked} onClick={() => void decide('approve')}>{selectedAction && requiresPatchEvidenceGate(selectedAction) ? 'Approve patch' : 'Approve'}</button>
								<button className="button danger" type="button" disabled={decisionBlocked} onClick={() => void decide('reject')}>Reject</button>
							</div>
						</div>
					) : null}
				</div>
			</Drawer>
			<Drawer label="Approval artifact preview" open={Boolean(previewArtifact)} onClose={() => {
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
								<pre className="artifact-preview">{redactVisibleText(previewPayload.text, '')}</pre>
							) : (
								<EmptyState title={previewLoadingId ? 'Loading artifact' : 'Binary or empty artifact'} body="Non-text artifacts remain downloadable, but are not rendered inline." />
							)}
							<button className="button primary" type="button" disabled={downloadLoadingId === String(previewArtifact.id ?? '')} aria-label={`Download approval preview artifact ${artifactDisplayName(previewArtifact)}`} onClick={() => void downloadArtifact(previewArtifact)}>
								{downloadLoadingId === String(previewArtifact.id ?? '') ? 'Downloading artifact' : 'Download artifact'}
							</button>
						</>
					) : null}
				</div>
			</Drawer>
		</>
	);
}
