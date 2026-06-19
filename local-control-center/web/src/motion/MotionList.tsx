/**
 * Lista con entrada escalonada declarativa: el contenedor (`listStagger`) secuencia la
 * entrada de cada `MotionListItem` (`cardTransition`). Sustituye el stagger imperativo por
 * `[data-motion-item]`; el fallback reduced-motion lo aporta `MotionConfig reducedMotion="user"`.
 */

import { m } from 'motion/react';
import type { ReactNode } from 'react';

import { cardTransition, listStagger } from './variants';

/** Contenedor de stagger; envuelve a varios `MotionListItem`. */
export function MotionList({ children, className }: { children: ReactNode; className?: string }) {
	return (
		<m.div className={className} variants={listStagger} initial="initial" animate="animate">
			{children}
		</m.div>
	);
}

/** Ítem de `MotionList`; hereda el timing del contenedor y aporta la variante de tarjeta. */
export function MotionListItem({
	children,
	className,
}: {
	children: ReactNode;
	className?: string;
}) {
	return (
		<m.div className={className} variants={cardTransition}>
			{children}
		</m.div>
	);
}
