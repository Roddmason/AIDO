/**
 * Suspense fallback for lazily-loaded routes: a low-chrome skeleton that fills the
 * content frame while a page chunk downloads, so navigation never flashes empty. The
 * container carries the `status`/`aria-busy` announcement; the bars are decorative.
 */
import { Skeleton } from '../components/ui';
import { useI18n } from '../i18n/I18nProvider';

export function RouteSkeleton() {
	const { t } = useI18n();
	return (
		<div
			className="route-skeleton"
			role="status"
			aria-busy="true"
			aria-label={t('app.route.loading', 'Loading view')}
		>
			<Skeleton className="route-skeleton-title" />
			<Skeleton className="route-skeleton-block" />
			<Skeleton className="route-skeleton-block" />
		</div>
	);
}
