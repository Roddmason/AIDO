/**
 * Small status pill (`.badge`) keyed by tone. On a tone change it briefly crossfades
 * (opacity only); the crossfade is suppressed under prefers-reduced-motion since opacity
 * is not a positional key that `MotionConfig` neutralizes on its own.
 * @author Rodrigo Mason
 */
import { m, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';

import { EASE_OUT } from '../../motion/variants';
import { cn } from './cn';

export type StatusTone = 'ok' | 'warn' | 'danger' | 'info' | 'pending';

const TONE_CHANGE = {
	initial: { opacity: 0.55 },
	animate: { opacity: 1, transition: { duration: 0.2, ease: EASE_OUT } },
} as const;

export interface StatusChipProps {
	children: ReactNode;
	tone?: StatusTone;
	className?: string;
}

export function StatusChip({ children, tone, className }: StatusChipProps) {
	const prefersReducedMotion = useReducedMotion();
	return (
		<m.span
			key={tone ?? 'default'}
			className={cn('badge', className)}
			data-tone={tone}
			variants={TONE_CHANGE}
			initial={prefersReducedMotion ? false : 'initial'}
			animate="animate"
		>
			{children}
		</m.span>
	);
}
