/**
 * Single review-board card plus the pure status guard that decides its ship CTA.
 * Renders project, run health, QA verdict, risk and diff/evidence refs, and picks
 * one contextual primary action (Review / Promote / Create PR / View evidence)
 * from the item's lifecycle state.
 */

import { AlertTriangle, FileCheck2, GitBranch, ShieldAlert } from 'lucide-react';
import { m } from 'motion/react';
import type { CSSProperties, KeyboardEvent } from 'react';
import { useRef } from 'react';

import { Badge, StatusDot } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { shortId, toneForStatus } from '../../lib/format';
import type { PatchWorkflowKind, ReviewItem } from './model';
import { patchWorkflowKind, riskTone } from './model';
import type { ShipOperation } from './useShipOperations';

/**
 * Shared-layout accent emitted by the active card AND by the open drawer panel.
 * Motion morphs this thin top edge between the two for a fluid card→detail
 * expansion. Anchored inline so it never touches the design-system CSS; under
 * `MotionConfig reducedMotion="user"` the layout move collapses to a no-op.
 */
const SHARED_ACCENT_STYLE: CSSProperties = {
	position: 'absolute',
	left: 'var(--space-3)',
	right: 'var(--space-3)',
	top: 0,
	height: '2px',
	borderRadius: 'var(--radius-full)',
	background: 'var(--color-accent, var(--color-text-primary))',
	pointerEvents: 'none',
};
const SHARED_ACCENT_TRANSITION = { type: 'spring', stiffness: 420, damping: 36 } as const;

/**
 * The shared-layout accent for the review expansion: a thin edge bar tagged with
 * a stable `layoutId` (`review-<item key>`). Rendered both by the active card and
 * by its open drawer panel so Motion morphs it between the two; renders only when
 * active so a single `layoutId` pair (card ↔ drawer) is matched at a time.
 */
export function ReviewSharedAccent({ itemKey, active }: { itemKey: string; active: boolean }) {
	if (!active) return null;
	return (
		<m.span
			layoutId={`review-${itemKey}`}
			aria-hidden="true"
			style={SHARED_ACCENT_STYLE}
			transition={SHARED_ACCENT_TRANSITION}
		/>
	);
}

/**
 * A shippable board item: a reviewed patch-workflow run whose status allows the
 * next ship step. Promote when approved (or a prior promotion failed); create a
 * PR once promoted to a branch. Status guards mirror the Jobs queue contract.
 */
export function shipOperationFor(
	item: ReviewItem,
): { operation: ShipOperation; kind: PatchWorkflowKind } | null {
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

/**
 * One review-board card: project, run health, QA verdict, risk and diff/evidence
 * refs, with the contextual primary action (Review, Promote branch, Create PR or
 * View evidence). Keyboard-activatable when the card can be decided.
 */
export function ReviewCard({
	item,
	selected,
	activeDetail,
	onOpenReview,
	onOpenDetail,
}: {
	item: ReviewItem;
	selected: boolean;
	activeDetail: boolean;
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
			{item.riskLevel === 'critical' || item.riskLevel === 'high' ? (
				<ShieldAlert size={14} aria-hidden="true" />
			) : (
				<AlertTriangle size={14} aria-hidden="true" />
			)}
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
			<ReviewSharedAccent itemKey={item.key} active={selected || activeDetail} />
			<div className="card-header">
				<h3 id={titleId} className="card-title">
					{item.projectName}
				</h3>
				{riskBadge}
			</div>
			<div className="card-meta">
				<StatusDot tone={toneForStatus(item.runStatus ?? undefined)} />
				<span className="mono">{shortId(item.runId ?? item.actionId)}</span>
				{item.qaVerdict ? (
					<Badge tone={toneForStatus(item.qaVerdict)}>QA {item.qaVerdict}</Badge>
				) : null}
				{item.runStatus ? (
					<Badge tone={toneForStatus(item.runStatus)}>{item.runStatus}</Badge>
				) : null}
			</div>
			<p className="card-body review-card-body">{item.runLabel}</p>
			<div className="inline review-card-actions">
				<span className="review-card-refs">
					{item.hasDiff ? (
						<span className="inline review-ref">
							<GitBranch size={14} aria-hidden="true" />
							diff
						</span>
					) : null}
					{item.hasEvidence ? (
						<span className="inline review-ref">
							<FileCheck2 size={14} aria-hidden="true" />
							{t('app.review.copy.19', 'evidence')}
						</span>
					) : null}
					{item.source === 'decided_action' && item.decidedBy ? (
						<span className="review-decided mono">
							{t('app.review.copy.21', 'by')} {item.decidedBy}
						</span>
					) : null}
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
						{shipOp.operation === 'promote'
							? t('app.review.promote', 'Promote branch')
							: t('app.review.createPr', 'Create PR')}
					</button>
				) : item.hasEvidence ? (
					<button
						ref={shipTriggerRef}
						className="button review-evidence-link"
						type="button"
						aria-label={`${t('app.workbench.timeline.viewEvidence', 'View evidence')}: ${item.runLabel} · ${item.projectName}`}
						onClick={() => onOpenDetail(item, shipTriggerRef.current)}
					>
						{t('app.workbench.timeline.viewEvidence', 'View evidence')}
					</button>
				) : null}
			</div>
		</article>
	);
}
