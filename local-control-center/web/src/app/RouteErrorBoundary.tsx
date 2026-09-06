/**
 * Error boundary around the active route: catches render failures and lazy-chunk load
 * errors so one broken page (or a failed chunk download over a flaky network) degrades
 * to a retryable message instead of blanking the whole shell. Retry remounts the subtree
 * (via a changing key) so a previously-rejected `React.lazy` import is attempted again.
 * Reports a fixed correlated signal; never sends the exception or form contents.
 * @author Rodrigo Mason
 */

import type { ReactNode } from 'react';
import { Component, Fragment } from 'react';
import { getHandshake } from '../api/client';

import { Button, ErrorState } from '../components/ui';

interface RouteErrorBoundaryProps {
	children: ReactNode;
	title: string;
	body: string;
	retryLabel: string;
}

interface RouteErrorBoundaryState {
	hasError: boolean;
	retryKey: number;
}

export class RouteErrorBoundary extends Component<
	RouteErrorBoundaryProps,
	RouteErrorBoundaryState
> {
	state: RouteErrorBoundaryState = { hasError: false, retryKey: 0 };

	static getDerivedStateFromError(): Partial<RouteErrorBoundaryState> {
		return { hasError: true };
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
			return (
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
		}
		return <Fragment key={this.state.retryKey}>{this.props.children}</Fragment>;
	}
}
