/**
 * Neutral empty-state block: title + body, with an optional call-to-action. Same
 * `{title, body}` contract as the legacy primitive so callers migrate by import swap.
 * @author AIDO contributors
 */
import type { ReactNode } from 'react';

import { cn } from './cn';

export interface EmptyStateProps {
	title: string;
	body: string;
	/** Optional call-to-action (e.g. a Button). */
	action?: ReactNode;
	/** `compact` drops the card padding for dense lanes: dashed hairline, small muted text. */
	variant?: 'default' | 'compact';
	className?: string;
}

export function EmptyState({
	title,
	body,
	action,
	variant = 'default',
	className,
}: EmptyStateProps) {
	return (
		<div className={cn('empty-state', variant === 'compact' && 'empty-state--compact', className)}>
			<strong>{title}</strong>
			<span>{body}</span>
			{action}
		</div>
	);
}
