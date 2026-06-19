/**
 * Envoltura declarativa de un panel: transición de montaje (opacity + leve desplazamiento).
 * Pensada para envolver una `Surface`/panel del IDE; el fallback reduced-motion lo da
 * `MotionConfig reducedMotion="user"`.
 */

import { m } from 'motion/react';
import type { ReactNode } from 'react';

import { panelTransition } from './variants';

/** Panel con transición de entrada declarativa; `className` se reenvía al contenedor. */
export function MotionPanel({ children, className }: { children: ReactNode; className?: string }) {
	return (
		<m.div
			className={className}
			variants={panelTransition}
			initial="initial"
			animate="animate"
			exit="exit"
		>
			{children}
		</m.div>
	);
}
