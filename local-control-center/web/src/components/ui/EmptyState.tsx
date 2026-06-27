/**
 * Neutral empty-state block: title + body, with an optional call-to-action. Same
 * `{title, body}` contract as the legacy primitive so callers migrate by import swap.
 * @author Rodrigo Mason
 */
import type { ReactNode } from 'react';

import { cn } from './cn';

export interface EmptyStateProps {
	title: string;
	body: string;
	/** Optional call-to-action (e.g. a Button). */
	action?: ReactNode;
	className?: string;
}

export function EmptyState({ title, body, action, className }: EmptyStateProps) {
	return (
		<div className={cn('empty-state', className)}>
			<strong>{title}</strong>
			<span>{body}</span>
			{action}
		</div>
	);
}
