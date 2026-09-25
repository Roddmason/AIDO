/**
 * Shell of the Settings modal: the Dialog opens synchronously while its body (the section
 * navigator plus every section panel) lives in its own chunk, downloaded the first time the
 * dialog opens instead of with the initial bundle. A slow chunk shows a loading skeleton and a
 * failed download shows the route error state, both inside the same dialog, so its focus,
 * Escape and close keep working while the body is missing.
 * @author Rodrigo Mason
 */

import type { ReactNode } from 'react';
import { lazy, Suspense } from 'react';

import { RouteErrorBoundary } from '../../app/RouteErrorBoundary';
import { Dialog } from '../../components/ui/Dialog';
import { Skeleton } from '../../components/ui/Skeleton';
import { useI18n } from '../../i18n/I18nProvider';
import type { SettingsModalProps } from './SettingsModal';

const SettingsModalBody = lazy(() =>
	import('./SettingsModal').then((module) => ({ default: module.SettingsModalBody })),
);

/** Pads a loading or error state like the body's content column while the body is unavailable. */
function PendingBody({ children }: { children: ReactNode }) {
	return <div className="settings-content">{children}</div>;
}

export function SettingsDialog(props: SettingsModalProps) {
	const { t } = useI18n();
	return (
		<Dialog
			open={props.open}
			onClose={props.onClose}
			label={t('app.settings.title', 'Settings')}
			className="settings-modal"
		>
			<RouteErrorBoundary
				title={t('app.route.loadErrorTitle', 'This view could not be loaded')}
				body={t(
					'app.route.loadErrorBody',
					'Something went wrong opening this page. Retry or pick another view.',
				)}
				retryLabel={t('app.route.retry', 'Retry')}
				wrapFallback={(fallback) => <PendingBody>{fallback}</PendingBody>}
			>
				<Suspense
					fallback={
						<PendingBody>
							<Skeleton
								className="settings-skeleton"
								label={t('app.settings.loading', 'Loading settings...')}
							/>
							<Skeleton className="settings-skeleton" />
							<Skeleton className="settings-skeleton" />
						</PendingBody>
					}
				>
					<SettingsModalBody {...props} />
				</Suspense>
			</RouteErrorBoundary>
		</Dialog>
	);
}
