/**
 * Top-level review board page: builds and groups the four decision lanes from the
 * overview, and owns the two drawers (approve/reject and ship-a-run) plus their
 * focus handling. Board state lives here; lanes, cards and the decision/ship UI
 * are delegated to child components and the review hooks.
 */
import { useMemo, useRef, useState } from 'react';

import type { ActionRequest, Overview } from '../../api/types';
import { Disclosure } from '../../components/Disclosure';
import {
	Badge,
	DataTable,
	Drawer,
	EmptyState,
	PageHeader,
	Surface,
} from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { shortId, toneForStatus } from '../../lib/format';
import { redactVisibleText } from '../../lib/redaction';
import { ApprovalDecisionPanel } from './ApprovalDecisionPanel';
import type { ReviewColumn as ReviewColumnId, ReviewItem } from './model';
import {
	buildReviewItems,
	groupReviewItems,
	REVIEW_COLUMNS,
	requiresPatchEvidenceGate,
} from './model';
import { shipOperationFor } from './ReviewCard';
import { ReviewColumn } from './ReviewColumn';
import { useArtifactPreview } from './useArtifactPreview';
import { useReviewDecision } from './useReviewDecision';
import { useShipOperations } from './useShipOperations';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;
type Refresh = (silent?: boolean) => Promise<void>;

/**
 * Review board: an evidence-first inbox that groups every human decision into
 * four lanes (Needs review / Blocked / Ready / Done). Owns board state and the
 * approval + ship lifecycles, delegating the lane, card and approve/reject UI to
 * {@link ReviewColumn}, {@link ReviewCard} and {@link ApprovalDecisionPanel}.
 */
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
		() => (shipItemKey ? (items.find((item) => item.key === shipItemKey) ?? null) : null),
		[items, shipItemKey],
	);
	const shipOp = liveShipItem ? shipOperationFor(liveShipItem) : null;
	const detailEvidence = useMemo(() => {
		if (!liveShipItem) return [];
		const pkgId = liveShipItem.evidencePackageId;
		const runId = liveShipItem.runId;
		return overview.evidencePackages.filter(
			(record) =>
				(pkgId && String(record.id ?? '') === pkgId) ||
				(runId && String(record.workflowRunId ?? '') === runId),
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

	const columnCopy: Record<
		ReviewColumnId,
		{ title: string; emptyTitle: string; emptyBody: string }
	> = {
		needs_review: {
			title: t('app.review.copy.4', 'Needs review'),
			emptyTitle: t('app.review.copy.5', 'Nothing waiting on you'),
			emptyBody: t(
				'app.review.copy.6',
				'Runs land here when QA produces evidence and a sensitive action needs a human decision.',
			),
		},
		blocked: {
			title: t('app.review.copy.7', 'Blocked'),
			emptyTitle: t('app.review.copy.8', 'No blocked runs'),
			emptyBody: t(
				'app.review.copy.9',
				'A run appears here when QA fails, a runtime is unavailable, or a decision was rejected.',
			),
		},
		ready: {
			title: t('app.review.copy.10', 'Ready'),
			emptyTitle: t('app.review.copy.11', 'Nothing ready to ship'),
			emptyBody: t(
				'app.review.copy.12',
				'Approved runs wait here for branch promotion or PR creation.',
			),
		},
		done: {
			title: t('app.review.colDoneTitle', 'Done'),
			emptyTitle: t('app.review.copy.14', 'No completed runs yet'),
			emptyBody: t(
				'app.review.copy.15',
				'Runs move here once a PR is created, the workflow completes, or a request is decided.',
			),
		},
	};

	return (
		<>
			<PageHeader
				kicker={t('app.review.copy.0', 'Review inbox')}
				title={t('app.review.title', 'Review')}
				summary={t(
					'app.review.copy.2',
					'One inbox for every human decision: pending approvals, QA failures, security blockers, evidence-ready runs, approvals for integration and recent decisions. Cards reflect server state — open one to approve or reject with full evidence.',
				)}
			/>
			<section
				className="review-board"
				role="region"
				aria-label={t('app.review.copy.3', 'Review board')}
			>
				{REVIEW_COLUMNS.map((column) => (
					<ReviewColumn
						key={column}
						column={column}
						items={columns[column]}
						meta={columnCopy[column]}
						selectedActionId={selectedActionId}
						onOpenReview={openReview}
						onOpenDetail={openDetail}
					/>
				))}
			</section>

			<Drawer
				label={t('ui.static.action.request.review.832a6e57', 'Action request review')}
				open={Boolean(selectedAction)}
				onClose={closeReview}
			>
				<div className="drawer-body">
					{selectedAction ? (
						<ApprovalDecisionPanel
							selectedAction={selectedAction}
							decision={decision}
							artifactPreview={artifactPreview}
							requiresGate={requiresGate}
							onApprove={() => void submitDecision('approve')}
							onReject={() => void submitDecision('reject')}
						/>
					) : null}
				</div>
			</Drawer>

			<Drawer
				label={t('app.review.shipDrawerLabel', 'Run detail')}
				open={Boolean(liveShipItem)}
				onClose={closeDetail}
			>
				<div className="drawer-body">
					{liveShipItem ? (
						<div className="stack">
							<div className="inline">
								<Badge tone={toneForStatus(liveShipItem.runStatus ?? undefined)}>
									{liveShipItem.runStatus}
								</Badge>
								{liveShipItem.workflowKind ? <Badge>{liveShipItem.workflowKind}</Badge> : null}
								<span className="mono">{shortId(liveShipItem.runId)}</span>
							</div>
							<p className="card-body">{liveShipItem.runLabel}</p>
							<Surface title={t('app.review.evidenceSectionTitle', 'Linked evidence')} flat>
								<DataTable
									rows={detailEvidence}
									empty={
										<EmptyState
											title={t(
												'ui.static.no.linked.evidence.packages.97fa8274',
												'No linked evidence packages',
											)}
											body={t(
												'app.review.noLinkedEvidenceRun',
												'This run did not record evidence package references.',
											)}
										/>
									}
									columns={[
										{
											key: 'id',
											label: t('ui.static.evidence.7ea014de', 'Evidence'),
											render: (row) => <span className="mono">{String(row.id ?? '')}</span>,
										},
										{
											key: 'verdict',
											label: 'QA',
											render: (row) => (
												<Badge tone={toneForStatus(String(row.qaVerdict ?? ''))}>
													{String(row.qaVerdict ?? '')}
												</Badge>
											),
										},
										{
											key: 'source',
											label: t('ui.static.source.6da13add', 'Source'),
											render: (row) => (
												<span className="mono">
													{String(row.evidenceSource ?? 'operator_attested')}
												</span>
											),
										},
										{
											key: 'task',
											label: t('ui.static.task.7bb0ddf9', 'Task'),
											render: (row) => String(row.taskId ?? ''),
										},
									]}
								/>
								<a className="settings-console-link" href="#evidence">
									{t('app.review.openFullEvidence', 'Open full evidence')}
								</a>
							</Surface>
							{shipOp ? (
								<>
									{/* Ship operations: promote / create PR (shippable runs only) */}
									<div className="field">
										<label htmlFor="review-ship-reason">
											{t('app.review.shipReasonLabel', 'Workflow operation reason')}
										</label>
										<textarea
											id="review-ship-reason"
											className="textarea"
											value={ship.reason}
											onChange={(event) => {
												ship.setReason(event.target.value);
												ship.setError('');
											}}
										/>
										<div className="field-help">
											{t(
												'app.review.shipReasonHelp',
												'Branch promotion and PR creation stay blocked until a reason is recorded.',
											)}
										</div>
									</div>
									<Disclosure title={t('app.review.shipAdvanced', 'Advanced options')}>
										<div className="field">
											<label htmlFor="review-ship-branch">
												{t(
													'ui.static.promotion.branch.optional.1a9de010',
													'Promotion branch (optional)',
												)}
											</label>
											<input
												id="review-ship-branch"
												className="input"
												value={ship.branchName}
												onChange={(event) => ship.setBranchName(event.target.value)}
											/>
										</div>
										<div className="field">
											<label htmlFor="review-ship-pr-title">
												{t('app.review.shipPrTitleLabel', 'PR title (optional)')}
											</label>
											<input
												id="review-ship-pr-title"
												className="input"
												value={ship.pullRequestTitle}
												onChange={(event) => ship.setPullRequestTitle(event.target.value)}
											/>
										</div>
										<div className="field">
											<label htmlFor="review-ship-pr-base">
												{t(
													'ui.static.pr.base.branch.optional.a00b34a5',
													'PR base branch (optional)',
												)}
											</label>
											<input
												id="review-ship-pr-base"
												className="input"
												value={ship.pullRequestBaseBranch}
												onChange={(event) => ship.setPullRequestBaseBranch(event.target.value)}
											/>
										</div>
									</Disclosure>
									{ship.error ? (
										<div className="form-error" role="alert">
											{ship.error}
										</div>
									) : null}
									{ship.lastOperation ? (
										<div className="inline" role="status">
											<Badge tone={toneForStatus(ship.lastOperation.status)}>
												{ship.lastOperation.status}
											</Badge>
											<span>
												{redactVisibleText(
													ship.lastOperation.reason ||
														t('app.review.shipPromoted', 'Last operation'),
													t('app.review.shipPromoted', 'Last operation'),
												)}
											</span>
											{ship.lastOperation.runId ? (
												<span className="mono">{shortId(ship.lastOperation.runId)}</span>
											) : null}
										</div>
									) : null}
									<div className="inline">
										<button
											className="button primary"
											type="button"
											disabled={!ship.reasonRecorded || Boolean(ship.busyId)}
											onClick={() =>
												void ship.run(shipOp.operation, liveShipItem.runId ?? '', shipOp.kind)
											}
										>
											{shipOp.operation === 'promote'
												? t('app.review.promote', 'Promote branch')
												: t('app.review.createPr', 'Create PR')}
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
