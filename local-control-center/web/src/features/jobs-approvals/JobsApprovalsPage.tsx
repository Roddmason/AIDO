import { useMemo, useState } from 'react';

import { approveAction, approveIssueToPatch, cancelJob, denyAction, downloadEvidenceArtifact, fetchEvidenceArtifact, retryJob } from '../../api/client';
import type { ArtifactPayload } from '../../api/client';
import type { ActionRequest, Artifact, Job, Overview } from '../../api/types';
import { Badge, DataTable, Drawer, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { artifactDisplayName, artifactMimeType, artifactSizeLabel } from '../../lib/artifacts';
import { shortId, toneForStatus } from '../../lib/format';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;

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

function issueToPatchWorkflowRunId(action: ActionRequest): string {
	if (action.actionType !== 'workflow.issue_to_patch.approve_patch') return '';
	const payload = asRecord(action.payload);
	const workflowRunId = payload.workflowRunId;
	return typeof workflowRunId === 'string' ? workflowRunId : '';
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
}: {
	overview: Overview;
	token: string;
	mutate: Mutate;
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
	const pendingActions = overview.actionRequests.filter((item) => item.status === 'pending');
	const selectedAction = pendingActions.find((item) => item.id === selectedActionId) ?? null;
	const selectedArtifacts = useMemo(() => selectedAction ? linkedArtifacts(selectedAction, overview) : [], [overview, selectedAction]);
	const selectedEvidence = useMemo(() => selectedAction ? linkedEvidence(selectedAction, overview) : [], [overview, selectedAction]);
	const trimmedDecisionReason = decisionReason.trim();
	const decisionBlocked = !trimmedDecisionReason;

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
	};
	const closeReview = () => {
		setSelectedActionId('');
		setDecisionReason('');
		setDecisionError('');
		setPreviewArtifact(null);
		setPreviewPayload(null);
		setPreviewError('');
	};
	const decide = async (kind: 'approve' | 'reject') => {
		if (!selectedAction) return;
		if (!trimmedDecisionReason) {
			setDecisionError('A human reason is required before this request can be decided.');
			return;
		}
		setDecisionError('');
		try {
			await mutate(async (writeToken) => {
				if (kind === 'reject') {
					return denyAction(writeToken, selectedAction.jobId, selectedAction.id, trimmedDecisionReason);
				}
				const approved = await approveAction(writeToken, selectedAction.jobId, selectedAction.id, trimmedDecisionReason);
				const workflowRunId = issueToPatchWorkflowRunId(selectedAction);
				if (workflowRunId) {
					return approveIssueToPatch(writeToken, workflowRunId, trimmedDecisionReason);
				}
				return approved;
			});
			closeReview();
		} catch (error) {
			setDecisionError(error instanceof Error ? error.message : 'Decision failed.');
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
								<div className="field-help">Approve or reject is blocked until this reason is recorded.</div>
							</div>
							{decisionError ? <div className="form-error" role="alert">{decisionError}</div> : null}
							<div className="inline">
								<button className="button primary" type="button" disabled={decisionBlocked} onClick={() => void decide('approve')}>Approve</button>
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
								<pre className="artifact-preview">{previewPayload.text}</pre>
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
