/**
 * Error boundary around a lazily loaded subtree (the active route, the Settings body, a local-runtime
 * panel), so one broken view degrades to an error state instead of blanking the whole shell; callers
 * can frame that state for the surface it replaces. The recovery it offers
 * depends on what failed:
 * - A render error offers Retry, which remounts the subtree via a changing key.
 * - A failed chunk download offers a page reload. A remount cannot recover it: `React.lazy` keeps the
 *   rejected import and rethrows it on every render without calling the import again, so only a full
 *   page load requests the chunk again. The usual cause is a dashboard rebuild while the tab was open
 *   (its old hashed chunks are gone), and the reload also picks up the new build. It stays an operator
 *   action: reloading automatically on a chunk that keeps failing would loop forever.
 * Reports a fixed correlated signal; never sends the exception or form contents.
 * @author Rodrigo Mason
 */

import type { ReactNode } from 'react';
import { Component, Fragment } from 'react';
import { getHandshake } from '../api/client';

import { Button, ErrorState } from '../components/ui';
import { useI18n } from '../i18n/I18nProvider';

interface RouteErrorBoundaryProps {
	children: ReactNode;
	title: string;
	body: string;
	retryLabel: string;
	/** Frames the error state for the surface it replaces, e.g. a dialog's padded content column. */
	wrapFallback?: (fallback: ReactNode) => ReactNode;
}

interface RouteErrorBoundaryState {
	hasError: boolean;
	/** The error was a failed chunk download, which only a full page load recovers. */
	chunkLoadFailed: boolean;
	retryKey: number;
}

/** How Chromium, Firefox and Safari word a failed dynamic import: the TypeError carries no code. */
const CHUNK_LOAD_ERROR_MESSAGES = [
	'Failed to fetch dynamically imported module',
	'error loading dynamically imported module',
	'Importing a module script failed',
];

function isChunkLoadError(error: unknown) {
	return (
		error instanceof TypeError &&
		CHUNK_LOAD_ERROR_MESSAGES.some((message) => error.message.includes(message))
	);
}

/** Resolves its own copy: a stale or missing chunk reads the same wherever the boundary sits. */
function ChunkLoadErrorState({ title }: { title: string }) {
	const { t } = useI18n();
	return (
		<ErrorState
			title={title}
			body={t(
				'app.route.chunkLoadErrorBody',
				'The code for this view could not be downloaded, usually because AIDO was updated while this tab was open. Reload the page to get the latest version.',
			)}
			action={
				<Button variant="primary" onClick={() => window.location.reload()}>
					{t('app.route.reloadPage', 'Reload page')}
				</Button>
			}
		/>
	);
}

export class RouteErrorBoundary extends Component<
	RouteErrorBoundaryProps,
	RouteErrorBoundaryState
> {
	state: RouteErrorBoundaryState = { hasError: false, chunkLoadFailed: false, retryKey: 0 };

	static getDerivedStateFromError(error: unknown): Partial<RouteErrorBoundaryState> {
		return { hasError: true, chunkLoadFailed: isChunkLoadError(error) };
	}

	componentDidCatch() {
		const correlationId = `corr-ui-${crypto.randomUUID()}`;
		const signal = AbortSignal.timeout(3000);
		void getHandshake(signal)
			.then(({ token }) =>
				fetch('/api/v1/telemetry/ui-error', {
					method: 'POST',
					signal,
					headers: { 'X-Local-Control-Token': token, 'X-Correlation-ID': correlationId },
				}),
			)
			.then((response) => {
				if (!response.ok) throw new Error('diagnostic delivery rejected');
			})
			.catch(() => console.warn({ event: 'ui.diagnostics.degraded', requestId: correlationId }));
	}

	private readonly handleRetry = () => {
		this.setState((prev) => ({ hasError: false, retryKey: prev.retryKey + 1 }));
	};

	render() {
		if (this.state.hasError) {
			const fallback = this.state.chunkLoadFailed ? (
				<ChunkLoadErrorState title={this.props.title} />
			) : (
				<ErrorState
					title={this.props.title}
					body={this.props.body}
					action={
						<Button variant="primary" onClick={this.handleRetry}>
							{this.props.retryLabel}
						</Button>
					}
				/>
			);
			return this.props.wrapFallback ? this.props.wrapFallback(fallback) : fallback;
		}
		return <Fragment key={this.state.retryKey}>{this.props.children}</Fragment>;
	}
}
