/**
 * Home gallery card for a review awaiting a human decision, with risk-driven
 * badge tone so urgency reads at a glance. One of the four masonry card kinds.
 * @author Rodrigo Mason
 */
import { ArrowRight, ClipboardCheck } from 'lucide-react';

import { StatusChip as Badge } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { CardHead, HomeCard } from './HomeCardShell';
import type { HomeReview } from './homeModel';

type Tone = 'ok' | 'warn' | 'danger' | 'info';

/** Maps a risk level to a badge tone. */
function riskTone(level: string): Tone {
	if (level === 'critical' || level === 'high') return 'danger';
	if (level === 'medium') return 'warn';
	return 'info';
}

/**
 * Gallery card for a pending review — an action awaiting a human decision.
 * Leads with a human headline; the specific action and command are secondary
 * detail. Opens the review board on click.
 */
export function ReviewCard({ request, onOpen }: { request: HomeReview; onOpen: () => void }) {
	const { t } = useI18n();
	const reviewLabel = t('app.home.review', 'Review');

	return (
		<HomeCard kind="review" onClick={onOpen} ariaLabel={`${reviewLabel}: ${request.actionType}`}>
			<CardHead
				icon={<ClipboardCheck size={15} aria-hidden="true" />}
				label={t('app.home.kindReview', 'Review')}
				badge={<Badge tone={riskTone(request.riskLevel)}>{request.riskLevel}</Badge>}
			/>
			<span className="home-card-title">{t('app.home.reviewNeeded', 'Pending review')}</span>
			<span className="home-card-meta">
				<span className="home-chip mono">{request.actionType}</span>
			</span>
			{request.command ? <span className="home-card-path mono">{request.command}</span> : null}
			<span className="home-card-cta">
				{reviewLabel}
				<ArrowRight size={14} aria-hidden="true" />
			</span>
		</HomeCard>
	);
}
