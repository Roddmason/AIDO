/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import type { ActionRequest } from '../../api/types';
import { Badge, DataTable, EmptyState, Surface } from '../../components/primitives';
import { artifactDisplayName } from '../../lib/artifacts';
import { hasRealPatchChanges } from '../../lib/diff';
import { toneForStatus } from '../../lib/format';
import { redactVisibleText } from '../../lib/redaction';
import { useI18n } from '../../i18n/I18nProvider';
import { prettyJson, riskTone, securityFindingsAreNonBlocking } from './model';
import { useReviewDecision } from './useReviewDecision';
import { useArtifactPreview } from './useArtifactPreview';

type ReviewDecision = ReturnType<typeof useReviewDecision>;
type ArtifactPreview = ReturnType<typeof useArtifactPreview>;

function DetailRow({ label, value }: { label: string; value: string }) {
	const { t } = useI18n();
	return (
		<div className="diff-row" role="row">
			<div role="cell" className="mono">{label}</div>
			<div role="cell">{value || t('app.review.notRecorded', 'not recorded')}</div>
		</div>
	);
}

/**
 * The human approve/reject panel for one action request: scope, policy reason,
 * linked evidence and artifacts, the patch evidence/diff/security gate, and the
 * decision reason with Approve/Reject. Reuses the existing approval endpoints
 * through the {@link useReviewDecision} state passed in by the page.
 */
export function ApprovalDecisionPanel({
	selectedAction,
	decision,
	artifactPreview,
	requiresGate,
	onApprove,
	onReject,
}: {
	selectedAction: ActionRequest;
	decision: ReviewDecision;
	artifactPreview: ArtifactPreview;
	requiresGate: boolean;
	onApprove: () => void;
	onReject: () => void;
}) {
	const { t } = useI18n();
	return (
		<div className="stack">
			<div className="inline">
				<Badge tone={riskTone(selectedAction.riskLevel)}>{selectedAction.riskLevel}</Badge>
				<Badge>{selectedAction.actionType}</Badge>
				<Badge>{selectedAction.expiresAt ? `${t('app.review.expiresPrefix', 'expires')} ${selectedAction.expiresAt}` : t('app.review.noExpiration', 'no expiration recorded')}</Badge>
			</div>
			<div className="diff-grid" role="table" aria-label={t('ui.static.action.request.scope.57c3a545', 'Action request scope')}>
				<DetailRow label={t('app.workbench.evidence.colCommand', 'Command')} value={redactVisibleText(selectedAction.command || t('app.review.notRecorded', 'not recorded'), t('app.review.notRecorded', 'not recorded'))} />
				<DetailRow label={t('ui.static.argv.2f50d8a9', 'Argv')} value={redactVisibleText((selectedAction.commandArgv ?? []).join(' '), '')} />
				<DetailRow label={t('ui.static.workspace.4ca0a75c', 'Workspace')} value={`${selectedAction.workspaceId || t('app.review.notRecorded', 'not recorded')} ${selectedAction.workspacePath || ''}`.trim()} />
				<DetailRow label={t('ui.static.runtime.c4740e4c', 'Runtime')} value={`${selectedAction.runtimeId || t('app.review.notRecorded', 'not recorded')} ${redactVisibleText(prettyJson(selectedAction.runtime), '')}`} />
				<DetailRow label={t('ui.static.job.30c8cb83', 'Job')} value={selectedAction.jobId} />
				<DetailRow label={t('ui.static.project.f6f4da8d', 'Project')} value={selectedAction.projectId} />
			</div>
			<Surface title={t('ui.static.policy.reason.bcb14f77', 'Policy reason')} flat>
				<pre className="artifact-preview">{redactVisibleText(selectedAction.reason, '')}</pre>
			</Surface>
			<Surface title={t('ui.static.linked.evidence.packages.2437b4d6', 'Linked evidence packages')} flat>
				<DataTable
					rows={decision.evidence}
					empty={<EmptyState title={t('ui.static.no.linked.evidence.packages.97fa8274', 'No linked evidence packages')} body={t('ui.static.this.request.did.not.record.evidence.package.references.cfffb0d8', 'This request did not record evidence package references.')} />}
					columns={[
						{ key: 'id', label: t('ui.static.evidence.7ea014de', 'Evidence'), render: (row) => <span className="mono">{String(row.id ?? '')}</span> },
						{ key: 'verdict', label: 'QA', render: (row) => <Badge tone={toneForStatus(String(row.qaVerdict ?? ''))}>{String(row.qaVerdict ?? '')}</Badge> },
						{ key: 'source', label: t('ui.static.source.6da13add', 'Source'), render: (row) => <span className="mono">{String(row.evidenceSource ?? 'operator_attested')}</span> },
						{ key: 'task', label: t('ui.static.task.7bb0ddf9', 'Task'), render: (row) => String(row.taskId ?? '') },
					]}
				/>
			</Surface>
			<Surface title={t('ui.static.linked.artifacts.78fba781', 'Linked artifacts')} flat>
				<DataTable
					rows={decision.artifacts}
					empty={<EmptyState title={t('ui.static.no.linked.artifacts.4d728786', 'No linked artifacts')} body={t('ui.static.diff.and.evidence.artifacts.must.be.attached.by.the.requesting.runtime.e7c8c851', 'Diff and evidence artifacts must be attached by the requesting runtime.')} />}
					columns={[
						{ key: 'name', label: t('ui.static.name.709a2322', 'Name'), render: (row) => artifactDisplayName(row) },
						{ key: 'kind', label: t('app.workbench.evidence.colKind', 'Kind'), render: (row) => <span className="mono">{String(row.kind ?? '')}</span> },
						{ key: 'hash', label: t('ui.static.hash.873507a0', 'Hash'), render: (row) => <span className="mono">{String(row.hash ?? '').slice(0, 12) || t('app.review.notRecorded', 'not recorded')}</span> },
						{
							key: 'action',
							label: t('ui.static.action.97c89a4d', 'Action'),
							render: (row) => {
								const loading = artifactPreview.loadingId === String(row.id ?? '');
								const downloading = artifactPreview.downloadingId === String(row.id ?? '');
								// Serialize downloads: while any artifact is downloading, every Download
								// button is disabled so two concurrent downloads cannot race on the shared
								// downloadingId/error slot. The active row keeps its "Downloading" label.
								const downloadBusy = Boolean(artifactPreview.downloadingId);
								return (
									<div className="inline" aria-busy={loading || downloading}>
										<button className="button" type="button" aria-label={`${t('app.review.previewArtifact', 'Preview artifact')} ${artifactDisplayName(row)}`} disabled={loading} onClick={() => void artifactPreview.openPreview(row)}>{loading ? t('app.review.opening', 'Opening') : t('app.workbench.evidence.preview', 'Preview')}</button>
										<button className="button" type="button" aria-label={`${t('app.review.downloadArtifact', 'Download artifact')} ${artifactDisplayName(row)}`} disabled={downloadBusy} onClick={() => void artifactPreview.download(row)}>{downloading ? t('app.workbench.evidence.downloading', 'Downloading') : t('app.workbench.evidence.download', 'Download')}</button>
									</div>
								);
							},
						},
					]}
				/>
				{artifactPreview.error ? <div className="form-error" role="alert">{artifactPreview.error}</div> : null}
				{artifactPreview.artifact ? (
					<div className="stack">
						<div className="inline">
							<Badge>{String(artifactPreview.artifact.kind ?? t('app.review.artifact', 'artifact'))}</Badge>
							<span className="mono">sha256 {String(artifactPreview.payload?.hash || artifactPreview.artifact.hash || t('app.review.notRecorded', 'not recorded'))}</span>
						</div>
						{artifactPreview.payload?.text ? (
							<pre className="artifact-preview">{redactVisibleText(artifactPreview.payload.text, '')}</pre>
						) : (
							<EmptyState title={artifactPreview.loadingId ? t('app.workbench.evidence.loading', 'Loading artifact') : t('app.review.binaryOrEmptyArtifact', 'Binary or empty artifact')} body={t('ui.static.non.text.artifacts.remain.downloadable.but.are.not.rendered.42481492', 'Non-text artifacts remain downloadable, but are not rendered inline.')} />
						)}
					</div>
				) : null}
			</Surface>
			{decision.patchGate?.required ? (
				<Surface title={t('ui.static.evidence.completeness.9a6c2f01', 'Evidence completeness')} flat>
					<div className="inline">
						<Badge tone={decision.patchGate.complete ? 'ok' : 'warn'}>{decision.patchGate.complete ? 'evidence_complete' : 'evidence_incomplete'}</Badge>
						<span>{decision.patchGate.complete ? t('app.review.patchGateComplete', 'Approve patch can proceed after a human reason.') : t('app.review.patchGateBlocked', 'Approve patch is blocked until evidence is complete.')}</span>
					</div>
					<pre className="artifact-preview">{decision.patchGate.reasons.join('\n')}</pre>
				</Surface>
			) : null}
			{decision.patchGate?.required ? (
				<Surface title={t('ui.static.full.diff.before.approval.4c0c93c5', 'Full diff before approval')} flat>
					{decision.patchLoading ? <EmptyState title={t('ui.static.loading.patch.artifact.19f1d8a6', 'Loading patch artifact')} body={t('ui.static.the.diff.is.read.through.the.protected.evidence.artifact.endpoint.e9955146', 'The diff is read through the protected evidence artifact endpoint.')} /> : null}
					{decision.patchError ? <div className="form-error" role="alert">{decision.patchError}</div> : null}
					{!decision.patchArtifact ? <EmptyState title={t('ui.static.no.patch.artifact.recorded.a6eb2ba0', 'No patch artifact recorded')} body={t('ui.static.a.patch.approval.requires.a.linked.diff.artifact.before.the.approve.patch.action.is.enabled.9f5a1b2c', 'A patch approval requires a linked diff artifact before the approve patch action is enabled.')} /> : null}
					{decision.patchArtifact && decision.patchPayload?.text && !hasRealPatchChanges(decision.patchPayload.text) ? <EmptyState title={t('ui.static.patch.artifact.has.no.proven.changes.66c87331', 'Patch artifact has no proven changes')} body={t('ui.static.the.patch.artifact.is.empty.or.malformed.so.approve.patch.remains.blocked.b0f0d46a', 'The patch artifact is empty or malformed, so approve patch remains blocked.')} /> : null}
					{decision.patchPayload?.text ? <pre className="artifact-preview">{redactVisibleText(decision.patchPayload.text, '')}</pre> : null}
				</Surface>
			) : null}
			{decision.patchGate?.required ? (
				<Surface title={t('ui.static.security.findings.before.approval.967fe426', 'Security findings before approval')} flat>
					{decision.securityLoading ? <EmptyState title={t('ui.static.loading.security.findings.0b677e6f', 'Loading security findings')} body={t('ui.static.security.evidence.is.read.through.the.protected.evidence.artifact.endpoint.7dd786fd', 'Security evidence is read through the protected evidence artifact endpoint.')} /> : null}
					{decision.securityError ? <div className="form-error" role="alert">{decision.securityError}</div> : null}
					{!decision.securityArtifact ? <EmptyState title={t('ui.static.no.security.findings.recorded.5199261c', 'No security findings recorded')} body={t('ui.static.patch.approval.requires.linked.non.blocking.security.findings.before.the.approve.patch.action.is.enabled.22bc9b5c', 'Patch approval requires linked non-blocking security findings before the approve patch action is enabled.')} /> : null}
					{decision.securityArtifact && decision.securityPayload?.text && !securityFindingsAreNonBlocking(decision.securityPayload) ? <EmptyState title={t('ui.static.security.findings.are.blocking.or.unreadable.62a7c2ac', 'Security findings are blocking or unreadable')} body={t('app.review.securityFindingsInvalid', 'The security findings artifact must be valid JSON with no blocking policy decision.')} /> : null}
					{decision.securityPayload?.text ? <pre className="artifact-preview">{redactVisibleText(decision.securityPayload.text, '')}</pre> : null}
				</Surface>
			) : null}
			<div className="field">
				<label htmlFor="review-decision-reason">{t('app.review.copy.24', 'Human decision reason')}</label>
				<textarea
					id="review-decision-reason"
					className="textarea"
					value={decision.decisionReason}
					onChange={(event) => {
						decision.setDecisionReason(event.target.value);
						decision.setDecisionError('');
					}}
				/>
				<div className="field-help">{decision.patchGate?.required ? t('app.review.copy.25', 'Approve patch also requires complete linked evidence, non-blocking security findings, and a real diff. Reject only requires a recorded reason.') : t('app.review.copy.26', 'Approve or reject is blocked until this reason is recorded.')}</div>
			</div>
			{decision.decisionError ? <div className="form-error" role="alert">{decision.decisionError}</div> : null}
			<div className="inline">
				<button className="button primary" type="button" disabled={decision.approveBlocked} onClick={onApprove}>
					{requiresGate ? t('app.review.copy.28', 'Approve patch') : t('ui.static.approve.78a1f3c9', 'Approve')}
				</button>
				<button className="button danger" type="button" disabled={decision.decisionBlocked} onClick={onReject}>
					{t('ui.static.reject.4c7c9dde', 'Reject')}
				</button>
			</div>
		</div>
	);
}
