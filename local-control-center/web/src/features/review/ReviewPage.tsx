/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { AlertTriangle, FileCheck2, GitBranch, ShieldAlert } from 'lucide-react';

import type { ActionRequest, Overview } from '../../api/types';
import { Badge, DataTable, Drawer, EmptyState, PageHeader, StatusDot, Surface } from '../../components/primitives';
import { Disclosure } from '../../components/Disclosure';
import { artifactDisplayName } from '../../lib/artifacts';
import { hasRealPatchChanges } from '../../lib/diff';
import { shortId, toneForStatus } from '../../lib/format';
import { redactVisibleText } from '../../lib/redaction';
import { useI18n } from '../../i18n/I18nProvider';
import {
	COLUMN_TONE,
	REVIEW_COLUMNS,
	buildReviewItems,
	groupReviewItems,
	patchWorkflowKind,
	prettyJson,
	requiresPatchEvidenceGate,
	riskTone,
	securityFindingsAreNonBlocking,
} from './model';
import type { PatchWorkflowKind, ReviewColumn, ReviewItem } from './model';
import { useReviewDecision } from './useReviewDecision';
import { useShipOperations } from './useShipOperations';
import type { ShipOperation } from './useShipOperations';
import { useArtifactPreview } from './useArtifactPreview';

/**
 * A shippable board item: a reviewed patch-workflow run whose status allows the
 * next ship step. Promote when approved (or a prior promotion failed); create a
 * PR once promoted to a branch. Status guards mirror the Jobs queue contract.
 */
function shipOperationFor(item: ReviewItem): { operation: ShipOperation; kind: PatchWorkflowKind } | null {
	if (!item.runId) return null;
	const kind = patchWorkflowKind(item.workflowKind);
	if (!kind) return null;
	if (item.runStatus === 'approved_for_integration' || item.runStatus === 'promotion_failed') {
		return { operation: 'promote', kind };
	}
	if (item.runStatus === 'promoted_to_branch') {
		return { operation: 'pull-request', kind };
	}
	return null;
}

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;
type Refresh = (silent?: boolean) => Promise<void>;

function DetailRow({ label, value }: { label: string; value: string }) {
	const { t } = useI18n();
	return (
		<div className="diff-row" role="row">
			<div role="cell" className="mono">{label}</div>
			<div role="cell">{value || t('app.review.notRecorded', 'not recorded')}</div>
		</div>
	);
}

function ReviewCard({
	item,
	selected,
	onOpenReview,
	onOpenDetail,
}: {
	item: ReviewItem;
	selected: boolean;
	onOpenReview: (item: ReviewItem, trigger: HTMLElement | null) => void;
	onOpenDetail: (item: ReviewItem, trigger: HTMLElement | null) => void;
}) {
	const { t } = useI18n();
	const titleId = `review-card-${item.key}`;
	const triggerRef = useRef<HTMLButtonElement>(null);
	const shipTriggerRef = useRef<HTMLButtonElement>(null);
	const shipOp = shipOperationFor(item);
	const open = () => onOpenReview(item, triggerRef.current);

	const handleCardKeyDown = (event: KeyboardEvent<HTMLElement>) => {
		if (!item.canDecide) return;
		if (event.target !== event.currentTarget) return;
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			open();
		}
	};

	const riskBadge = item.riskLevel ? (
		<Badge tone={riskTone(item.riskLevel)}>
			{(item.riskLevel === 'critical' || item.riskLevel === 'high')
				? <ShieldAlert size={14} aria-hidden="true" />
				: <AlertTriangle size={14} aria-hidden="true" />}
			{item.riskLevel}
		</Badge>
	) : null;

	return (
		<article
			className="card review-card"
			data-risk={item.riskLevel ?? undefined}
			data-selected={selected ? 'true' : undefined}
			tabIndex={0}
			aria-labelledby={titleId}
			onKeyDown={handleCardKeyDown}
		>
			<div className="card-header">
				<h3 id={titleId} className="card-title">{item.projectName}</h3>
				{riskBadge}
			</div>
			<div className="card-meta">
				<StatusDot tone={toneForStatus(item.runStatus ?? undefined)} />
				<span className="mono">{shortId(item.runId ?? item.actionId)}</span>
				{item.qaVerdict ? <Badge tone={toneForStatus(item.qaVerdict)}>QA {item.qaVerdict}</Badge> : null}
				{item.runStatus ? <Badge tone={toneForStatus(item.runStatus)}>{item.runStatus}</Badge> : null}
			</div>
			<p className="card-body review-card-body">{item.runLabel}</p>
			<div className="inline review-card-actions">
				<span className="review-card-refs">
					{item.hasDiff ? <span className="inline review-ref"><GitBranch size={14} aria-hidden="true" />diff</span> : null}
					{item.hasEvidence ? <span className="inline review-ref"><FileCheck2 size={14} aria-hidden="true" />{t('app.review.copy.19', 'evidence')}</span> : null}
					{item.source === 'decided_action' && item.decidedBy
						? <span className="review-decided mono">{t('app.review.copy.21', 'by')} {item.decidedBy}</span>
						: null}
				</span>
				{item.canDecide ? (
					<button
						ref={triggerRef}
						className="button primary review-card-cta"
						type="button"
						aria-label={`${t('ui.static.review.c3f114cf', 'Review')}: ${item.runLabel} · ${item.projectName}`}
						onClick={open}
					>
						{t('ui.static.review.c3f114cf', 'Review')}
					</button>
				) : shipOp ? (
					<button
						ref={shipTriggerRef}
						className="button primary review-card-cta"
						type="button"
						aria-label={`${shipOp.operation === 'promote' ? t('app.review.promote', 'Promote branch') : t('app.review.createPr', 'Create PR')}: ${item.runLabel} · ${item.projectName}`}
						onClick={() => onOpenDetail(item, shipTriggerRef.current)}
					>
						{shipOp.operation === 'promote' ? t('app.review.promote', 'Promote branch') : t('app.review.createPr', 'Create PR')}
					</button>
				) : (item.hasEvidence ? (
					<button
						ref={shipTriggerRef}
						className="button review-evidence-link"
						type="button"
						aria-label={`${t('app.workbench.timeline.viewEvidence', 'View evidence')}: ${item.runLabel} · ${item.projectName}`}
						onClick={() => onOpenDetail(item, shipTriggerRef.current)}
					>
						{t('app.workbench.timeline.viewEvidence', 'View evidence')}
					</button>
				) : null)}
			</div>
		</article>
	);
}

export function ReviewPage({
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
	const { t } = useI18n();

	const [selectedActionId, setSelectedActionId] = useState('');
	const triggerRef = useRef<HTMLElement | null>(null);

	const items = useMemo(() => buildReviewItems(overview), [overview]);
	const columns = useMemo(() => groupReviewItems(items), [items]);

	const selectedAction = useMemo<ActionRequest | null>(
		() => overview.actionRequests.find((action) => action.id === selectedActionId) ?? null,
		[overview, selectedActionId],
	);
	const decision = useReviewDecision(selectedAction, overview, token, mutate);
	const artifactPreview = useArtifactPreview(token);

	// Ship lifecycle (promote/PR) for a reviewed, approved run — additive to the
	// decision flow. Keyed by item key so it re-resolves the live run after a
	// refresh (promote → the same drawer then offers Create PR).
	const ship = useShipOperations(token, refresh);
	const [shipItemKey, setShipItemKey] = useState('');
	const shipTriggerRef = useRef<HTMLElement | null>(null);
	const liveShipItem = useMemo(
		() => (shipItemKey ? items.find((item) => item.key === shipItemKey) ?? null : null),
		[items, shipItemKey],
	);
	const shipOp = liveShipItem ? shipOperationFor(liveShipItem) : null;
	const detailEvidence = useMemo(() => {
		if (!liveShipItem) return [];
		const pkgId = liveShipItem.evidencePackageId;
		const runId = liveShipItem.runId;
		return overview.evidencePackages.filter(
			(record) => (pkgId && String(record.id ?? '') === pkgId) || (runId && String(record.workflowRunId ?? '') === runId),
		);
	}, [overview, liveShipItem]);

	const openReview = (item: ReviewItem, trigger: HTMLElement | null) => {
		if (!item.actionId) return;
		triggerRef.current = trigger;
		setSelectedActionId(item.actionId);
	};
	const closeReview = () => {
		setSelectedActionId('');
		artifactPreview.clear();
		const trigger = triggerRef.current;
		triggerRef.current = null;
		if (trigger) trigger.focus();
	};
	const openDetail = (item: ReviewItem, trigger: HTMLElement | null) => {
		shipTriggerRef.current = trigger;
		ship.reset();
		setShipItemKey(item.key);
	};
	const closeDetail = () => {
		setShipItemKey('');
		ship.reset();
		const trigger = shipTriggerRef.current;
		shipTriggerRef.current = null;
		if (trigger) trigger.focus();
	};
	const submitDecision = async (kind: 'approve' | 'reject') => {
		const ok = await decision.decide(kind);
		if (!ok) return;
		closeReview();
		void refresh(true);
	};

	const requiresGate = selectedAction ? requiresPatchEvidenceGate(selectedAction) : false;

	const columnCopy: Record<ReviewColumn, { title: string; emptyTitle: string; emptyBody: string }> = {
		needs_review: {
			title: t('app.review.copy.4', 'Needs review'),
			emptyTitle: t('app.review.copy.5', 'Nothing waiting on you'),
			emptyBody: t('app.review.copy.6', 'Runs land here when QA produces evidence and a sensitive action needs a human decision.'),
		},
		blocked: {
			title: t('app.review.copy.7', 'Blocked'),
			emptyTitle: t('app.review.copy.8', 'No blocked runs'),
			emptyBody: t('app.review.copy.9', 'A run appears here when QA fails, a runtime is unavailable, or a decision was rejected.'),
		},
		ready: {
			title: t('app.review.copy.10', 'Ready'),
			emptyTitle: t('app.review.copy.11', 'Nothing ready to ship'),
			emptyBody: t('app.review.copy.12', 'Approved runs wait here for branch promotion or PR creation.'),
		},
		done: {
			title: t('app.review.colDoneTitle', 'Done'),
			emptyTitle: t('app.review.copy.14', 'No completed runs yet'),
			emptyBody: t('app.review.copy.15', 'Runs move here once a PR is created, the workflow completes, or a request is decided.'),
		},
	};

	return (
		<>
			<PageHeader kicker={t('app.review.copy.0', 'Review inbox')} title={t('app.review.title', 'Review')} summary={t('app.review.copy.2', 'One inbox for every human decision: pending approvals, QA failures, security blockers, evidence-ready runs, approvals for integration and recent decisions. Cards reflect server state — open one to approve or reject with full evidence.')} />
			<section className="review-board" role="region" aria-label={t('app.review.copy.3', 'Review board')}>
				{REVIEW_COLUMNS.map((column) => {
					const lane = columns[column];
					const meta = columnCopy[column];
					const headerId = `review-col-${column}`;
					return (
						<section
							key={column}
							className="review-column"
							data-focal={column === 'needs_review' ? 'true' : undefined}
							data-motion-item
							role="group"
							aria-labelledby={headerId}
						>
							<header className="review-column-header">
								<StatusDot tone={COLUMN_TONE[column]} />
								<span id={headerId} className="review-column-title">{meta.title}</span>
								<span className="review-column-count">
									<Badge tone={lane.length ? COLUMN_TONE[column] : undefined}>{lane.length}</Badge>
								</span>
							</header>
							<div className="review-column-scroll">
								{lane.length === 0 ? (
									<EmptyState title={meta.emptyTitle} body={meta.emptyBody} />
								) : (
									lane.map((item) => (
										<ReviewCard
											key={item.key}
											item={item}
											selected={item.actionId === selectedActionId && Boolean(selectedActionId)}
											onOpenReview={openReview}
											onOpenDetail={openDetail}
										/>
									))
								)}
							</div>
						</section>
					);
				})}
			</section>

			<Drawer label={t('ui.static.action.request.review.832a6e57', 'Action request review')} open={Boolean(selectedAction)} onClose={closeReview}>
				<div className="drawer-body">
					{selectedAction ? (
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
								<button className="button primary" type="button" disabled={decision.approveBlocked} onClick={() => void submitDecision('approve')}>
									{requiresGate ? t('app.review.copy.28', 'Approve patch') : t('ui.static.approve.78a1f3c9', 'Approve')}
								</button>
								<button className="button danger" type="button" disabled={decision.decisionBlocked} onClick={() => void submitDecision('reject')}>
									{t('ui.static.reject.4c7c9dde', 'Reject')}
								</button>
							</div>
						</div>
					) : null}
				</div>
			</Drawer>

			<Drawer label={t('app.review.shipDrawerLabel', 'Run detail')} open={Boolean(liveShipItem)} onClose={closeDetail}>
				<div className="drawer-body">
					{liveShipItem ? (
						<div className="stack">
							<div className="inline">
								<Badge tone={toneForStatus(liveShipItem.runStatus ?? undefined)}>{liveShipItem.runStatus}</Badge>
								{liveShipItem.workflowKind ? <Badge>{liveShipItem.workflowKind}</Badge> : null}
								<span className="mono">{shortId(liveShipItem.runId)}</span>
							</div>
							<p className="card-body">{liveShipItem.runLabel}</p>
								<Surface title={t('app.review.evidenceSectionTitle', 'Linked evidence')} flat>
									<DataTable
										rows={detailEvidence}
										empty={<EmptyState title={t('ui.static.no.linked.evidence.packages.97fa8274', 'No linked evidence packages')} body={t('app.review.noLinkedEvidenceRun', 'This run did not record evidence package references.')} />}
										columns={[
											{ key: 'id', label: t('ui.static.evidence.7ea014de', 'Evidence'), render: (row) => <span className="mono">{String(row.id ?? '')}</span> },
											{ key: 'verdict', label: 'QA', render: (row) => <Badge tone={toneForStatus(String(row.qaVerdict ?? ''))}>{String(row.qaVerdict ?? '')}</Badge> },
											{ key: 'source', label: t('ui.static.source.6da13add', 'Source'), render: (row) => <span className="mono">{String(row.evidenceSource ?? 'operator_attested')}</span> },
											{ key: 'task', label: t('ui.static.task.7bb0ddf9', 'Task'), render: (row) => String(row.taskId ?? '') },
										]}
									/>
									<a className="settings-console-link" href="#evidence">{t('app.review.openFullEvidence', 'Open full evidence')}</a>
								</Surface>
								{shipOp ? (
								<>
								{/* Ship operations: promote / create PR (shippable runs only) */}
							<div className="field">
								<label htmlFor="review-ship-reason">{t('app.review.shipReasonLabel', 'Workflow operation reason')}</label>
								<textarea
									id="review-ship-reason"
									className="textarea"
									value={ship.reason}
									onChange={(event) => {
										ship.setReason(event.target.value);
										ship.setError('');
									}}
								/>
								<div className="field-help">{t('app.review.shipReasonHelp', 'Branch promotion and PR creation stay blocked until a reason is recorded.')}</div>
							</div>
							<Disclosure title={t('app.review.shipAdvanced', 'Advanced options')}>
								<div className="field">
									<label htmlFor="review-ship-branch">{t('ui.static.promotion.branch.optional.1a9de010', 'Promotion branch (optional)')}</label>
									<input id="review-ship-branch" className="input" value={ship.branchName} onChange={(event) => ship.setBranchName(event.target.value)} />
								</div>
								<div className="field">
									<label htmlFor="review-ship-pr-title">{t('app.review.shipPrTitleLabel', 'PR title (optional)')}</label>
									<input id="review-ship-pr-title" className="input" value={ship.pullRequestTitle} onChange={(event) => ship.setPullRequestTitle(event.target.value)} />
								</div>
								<div className="field">
									<label htmlFor="review-ship-pr-base">{t('ui.static.pr.base.branch.optional.a00b34a5', 'PR base branch (optional)')}</label>
									<input id="review-ship-pr-base" className="input" value={ship.pullRequestBaseBranch} onChange={(event) => ship.setPullRequestBaseBranch(event.target.value)} />
								</div>
							</Disclosure>
							{ship.error ? <div className="form-error" role="alert">{ship.error}</div> : null}
							{ship.lastOperation ? (
								<div className="inline" role="status">
									<Badge tone={toneForStatus(ship.lastOperation.status)}>{ship.lastOperation.status}</Badge>
									<span>{redactVisibleText(ship.lastOperation.reason || t('app.review.shipPromoted', 'Last operation'), t('app.review.shipPromoted', 'Last operation'))}</span>
									{ship.lastOperation.runId ? <span className="mono">{shortId(ship.lastOperation.runId)}</span> : null}
								</div>
							) : null}
								<div className="inline">
									<button
										className="button primary"
										type="button"
										disabled={!ship.reasonRecorded || Boolean(ship.busyId)}
										onClick={() => void ship.run(shipOp.operation, liveShipItem.runId ?? '', shipOp.kind)}
									>
										{shipOp.operation === 'promote' ? t('app.review.promote', 'Promote branch') : t('app.review.createPr', 'Create PR')}
									</button>
								</div>
							</>
							) : null}
						</div>
					) : null}
				</div>
			</Drawer>
		</>
	);
}
