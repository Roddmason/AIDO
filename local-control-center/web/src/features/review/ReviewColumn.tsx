/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { Badge, EmptyState, StatusDot } from '../../components/primitives';
import { COLUMN_TONE } from './model';
import type { ReviewColumn as ReviewColumnId, ReviewItem } from './model';
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
	onOpenReview,
	onOpenDetail,
}: {
	column: ReviewColumnId;
	items: ReviewItem[];
	meta: { title: string; emptyTitle: string; emptyBody: string };
	selectedActionId: string;
	onOpenReview: (item: ReviewItem, trigger: HTMLElement | null) => void;
	onOpenDetail: (item: ReviewItem, trigger: HTMLElement | null) => void;
}) {
	const headerId = `review-col-${column}`;
	return (
		<section
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
							onOpenReview={onOpenReview}
							onOpenDetail={onOpenDetail}
						/>
					))
				)}
			</div>
		</section>
	);
}
