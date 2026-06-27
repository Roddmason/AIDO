/**
 * Danger-toned failure block (sibling of {@link EmptyState}) for failed loads or actions.
 * Renders as an `alert` so assistive tech announces it; pass a retry affordance via `action`.
 * @author Rodrigo Mason
 */
import type { ReactNode } from 'react';

import { cn } from './cn';

export interface ErrorStateProps {
	title: string;
	body: string;
	/** Optional recovery affordance (e.g. a retry Button). */
	action?: ReactNode;
	className?: string;
}

export function ErrorState({ title, body, action, className }: ErrorStateProps) {
	return (
		<div className={cn('error-state', className)} role="alert">
			<strong>{title}</strong>
			<span>{body}</span>
			{action}
		</div>
	);
}
