/**
 * One presentational lane of the review board — header, live count, and a stack
 * of cards or an empty state. Stateless: the page passes the grouped items and
 * the localized copy, and the column just renders and forwards open callbacks.
 * @author Rodrigo Mason
 */
import { Badge, EmptyState, StatusDot } from '../../components/primitives';
import type { ReviewColumn as ReviewColumnId, ReviewItem } from './model';
import { COLUMN_TONE } from './model';
import { ReviewCard } from './ReviewCard';

/**
 * One board lane (Needs review / Blocked / Ready / Done): a toned header with a
 * live count and a scrollable stack of {@link ReviewCard}s, or an empty state.
 * Copy is passed in so the page owns the single localized column vocabulary.
 */
export function ReviewColumn({
	column,
	items,
	meta,
	selectedActionId,
	activeDetailKey,
	onOpenReview,
	onOpenDetail,
}: {
	column: ReviewColumnId;
	items: ReviewItem[];
	meta: { title: string; emptyTitle: string; emptyBody: string };
	selectedActionId: string;
	activeDetailKey: string;
	onOpenReview: (item: ReviewItem, trigger: HTMLElement | null) => void;
	onOpenDetail: (item: ReviewItem, trigger: HTMLElement | null) => void;
}) {
	const headerId = `review-col-${column}`;
	return (
		<section
			className="review-column"
			data-focal={column === 'needs_review' ? 'true' : undefined}
			data-motion-item
			aria-labelledby={headerId}
		>
			<header className="review-column-header">
				<StatusDot tone={COLUMN_TONE[column]} />
				<h2 id={headerId} className="review-column-title">
					{meta.title}
				</h2>
				<span className="review-column-count">
					<Badge tone={items.length ? COLUMN_TONE[column] : undefined}>{items.length}</Badge>
				</span>
			</header>
			<div className="review-column-scroll">
				{items.length === 0 ? (
					<EmptyState title={meta.emptyTitle} body={meta.emptyBody} />
				) : (
					items.map((item) => (
						<ReviewCard
							key={item.key}
							item={item}
							selected={item.actionId === selectedActionId && Boolean(selectedActionId)}
							activeDetail={item.key === activeDetailKey && Boolean(activeDetailKey)}
							onOpenReview={onOpenReview}
							onOpenDetail={onOpenDetail}
						/>
					))
				)}
			</div>
		</section>
	);
}
