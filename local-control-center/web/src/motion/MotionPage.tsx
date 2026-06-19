/**
 * Envoltura declarativa de la página activa: funde/desliza la ruta al entrar y salir.
 * Úsala dentro de un `AnimatePresence` keyed por ruta; el fallback reduced-motion lo aporta
 * `MotionConfig reducedMotion="user"` (deja solo opacity). Reemplaza al antiguo `usePageMotion`.
 */

import { m } from 'motion/react';
import type { ReactNode } from 'react';

import { pageTransition } from './variants';

/** Contenedor animado de una página; el cambio de `key` en AnimatePresence dispara enter/exit. */
export function MotionPage({ children }: { children: ReactNode }) {
	return (
		<m.div
			className="motion-page"
			variants={pageTransition}
			initial="initial"
			animate="animate"
			exit="exit"
		>
			{children}
		</m.div>
	);
}
