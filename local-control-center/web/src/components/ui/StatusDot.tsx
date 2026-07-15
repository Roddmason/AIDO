/**
 * Punto de estado tonal (ok/warn/danger/info/pending) coloreado vía CSS `data-tone`,
 * con un crossfade breve de opacidad al cambiar de tono (keyed por tono) que respeta
 * prefers-reduced-motion.
 * @author Rodrigo Mason
 */
import { m, useReducedMotion } from 'motion/react';

import { EASE_OUT } from '../../motion/variants';

/** Crossfade breve al cambiar de tono: keyed por tono para reanimar opacity sin tocar el color (CSS). */
const TONE_CHANGE = {
	initial: { opacity: 0.55 },
	animate: { opacity: 1, transition: { duration: 0.2, ease: EASE_OUT } },
} as const;

export function StatusDot({
	tone = 'info',
}: {
	tone?: 'ok' | 'warn' | 'danger' | 'info' | 'pending';
}) {
	const prefersReducedMotion = useReducedMotion();
	return (
		<m.span
			key={tone}
			className="status-dot"
			data-tone={tone}
			aria-hidden="true"
			variants={TONE_CHANGE}
			initial={prefersReducedMotion ? false : 'initial'}
			animate="animate"
		/>
	);
}
