/**
 * Loading placeholder with a subtle opacity shimmer. The loop is gated by
 * `useReducedMotion`: under prefers-reduced-motion it stays on the static `idle` opacity.
 * Pass `label` to expose it as a `status` region; otherwise it is `aria-hidden`.
 */
import { m, useReducedMotion } from 'motion/react';

import { skeletonShimmer } from '../../motion/variants';
import { cn } from './cn';

export interface SkeletonProps {
	className?: string;
	/** Optional accessible status label; without it the placeholder is decorative. */
	label?: string;
}

export function Skeleton({ className, label }: SkeletonProps) {
	const prefersReducedMotion = useReducedMotion();
	return (
		<m.div
			className={cn('skeleton', className)}
			variants={skeletonShimmer}
			initial="idle"
			animate={prefersReducedMotion ? 'idle' : 'loading'}
			role={label ? 'status' : undefined}
			aria-label={label}
			aria-hidden={label ? undefined : true}
		/>
	);
}
