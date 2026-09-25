/**
 * Code-split points for the local runtimes UI: the setup wizard, the discovery list and the Local
 * endpoints panel live in their own chunks, downloaded when the Providers & CLI section renders instead
 * of with the initial bundle. `DeferredLocalRuntime` wraps each one so a slow chunk shows a skeleton and
 * a failed download degrades to a retryable error inside Settings instead of blanking the dialog; the
 * wizard passes `placeholder={false}` because it renders nothing while closed.
 * @author Rodrigo Mason
 */

import type { ReactNode } from 'react';
import { lazy, Suspense } from 'react';

import { RouteErrorBoundary } from '../../app/RouteErrorBoundary';
import { Skeleton } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';

export const LocalRuntimeWizard = lazy(() =>
	import('./LocalRuntimeWizard').then((module) => ({ default: module.LocalRuntimeWizard })),
);
export const LocalRuntimeDiscovery = lazy(() =>
	import('./LocalRuntimeDiscovery').then((module) => ({ default: module.LocalRuntimeDiscovery })),
);
export const LocalEndpointsPanel = lazy(() =>
	import('./LocalEndpointsPanel').then((module) => ({ default: module.LocalEndpointsPanel })),
);

interface DeferredLocalRuntimeProps {
	children: ReactNode;
	placeholder?: boolean;
}

export function DeferredLocalRuntime({ children, placeholder = true }: DeferredLocalRuntimeProps) {
	const { t } = useI18n();
	return (
		<RouteErrorBoundary
			title={t('app.route.loadErrorTitle', 'This view could not be loaded')}
			body={t(
				'app.route.loadErrorBody',
				'Something went wrong opening this page. Retry or pick another view.',
			)}
			retryLabel={t('app.route.retry', 'Retry')}
		>
			<Suspense
				fallback={
					placeholder ? (
						<Skeleton
							className="route-skeleton-block"
							label={t('app.route.loading', 'Loading view')}
						/>
					) : null
				}
			>
				{children}
			</Suspense>
		</RouteErrorBoundary>
	);
}
