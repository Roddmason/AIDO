/**
 * Shared loading, error and no-thread states plus the resource gate and section title used by every Thread Inspector tab, so each panel renders the same honest skeleton/error/empty chrome.
 * @author Rodrigo Mason
 */
import type { ReactNode } from 'react';
import { Button, EmptyState, ErrorState, Skeleton } from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';
import type { InspectorResource } from '../useThreadInspectorData';

export function PanelSkeleton() {
	const { t } = useI18n();
	return (
		<div className="thread-inspector-loading">
			<Skeleton
				className="thread-inspector-skeleton-row"
				label={t('app.threads.inspector.loading', 'Loading inspector data')}
			/>
			<Skeleton className="thread-inspector-skeleton-row" />
			<Skeleton className="thread-inspector-skeleton-row" />
			<Skeleton className="thread-inspector-skeleton-row" />
		</div>
	);
}

export function PanelError({ onRetry }: { onRetry: () => void }) {
	const { t } = useI18n();
	return (
		<ErrorState
			title={t('app.threads.inspector.errorTitle', 'Could not load this view')}
			body={t('app.threads.inspector.errorBody', 'The request failed. Retry to fetch it again.')}
			action={
				<Button variant="secondary" onClick={onRetry}>
					{t('app.threads.retry', 'Retry')}
				</Button>
			}
		/>
	);
}

export function NoThreadState() {
	const { t } = useI18n();
	return (
		<EmptyState
			title={t('app.threads.inspector.noThreadTitle', 'No live thread')}
			body={t('app.threads.inspector.noThreadBody', 'Create or select a thread to inspect it.')}
		/>
	);
}

/** Renders skeleton/error/success for one lazily loaded resource; children receive the data. */
export function ResourceGate<T>({
	resource,
	children,
}: {
	resource: InspectorResource<T>;
	children: (data: T) => ReactNode;
}) {
	if (resource.error) return <PanelError onRetry={resource.reload} />;
	if (!resource.data) return <PanelSkeleton />;
	return <>{children(resource.data)}</>;
}

export function SectionTitle({ children }: { children: ReactNode }) {
	return <h4 className="thread-inspector-section-title">{children}</h4>;
}
