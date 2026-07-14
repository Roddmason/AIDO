/**
 * Variantes de animación declarativas compartidas por las primitivas Motion del shell.
 *
 * Cada variante usa transforms pequeños + opacity, de modo que bajo `MotionConfig
 * reducedMotion="user"` el motor descarta el transform y deja solo el fundido (fallback
 * accesible). No contienen autoplay decorativo salvo `statusPulse`, que el consumidor
 * debe condicionar con `useReducedMotion`.
 * @author Rodrigo Mason
 */
import type { Variants } from 'motion/react';

export const EASE_OUT: [number, number, number, number] = [0.22, 1, 0.36, 1];

/** Entrada/salida de página al cambiar de ruta (dentro de AnimatePresence, keyed por ruta). */
export const pageTransition: Variants = {
	initial: { opacity: 0, y: 8 },
	animate: { opacity: 1, y: 0, transition: { duration: 0.22, ease: EASE_OUT } },
	exit: { opacity: 0, y: -6, transition: { duration: 0.14, ease: 'easeIn' } },
};

/** Montaje de un panel (Surface/inspector). */
export const panelTransition: Variants = {
	initial: { opacity: 0, y: 6 },
	animate: { opacity: 1, y: 0, transition: { duration: 0.2, ease: EASE_OUT } },
	exit: { opacity: 0, transition: { duration: 0.12 } },
};

/** Montaje de una tarjeta; sirve como variante por-ítem dentro de `listStagger`. */
export const cardTransition: Variants = {
	initial: { opacity: 0, y: 10 },
	animate: { opacity: 1, y: 0, transition: { duration: 0.22, ease: EASE_OUT } },
	exit: { opacity: 0, y: 6, transition: { duration: 0.12 } },
};

/** Diálogos/overlays: escala sutil + opacity. */
export const dialogTransition: Variants = {
	initial: { opacity: 0, scale: 0.98 },
	animate: { opacity: 1, scale: 1, transition: { duration: 0.18, ease: EASE_OUT } },
	exit: { opacity: 0, scale: 0.98, transition: { duration: 0.12 } },
};

/** Contenedor de stagger: sus hijos (cardTransition) entran en secuencia. */
export const listStagger: Variants = {
	initial: {},
	animate: { transition: { staggerChildren: 0.04, delayChildren: 0.02 } },
	exit: {},
};

/**
 * Pulso de estado decorativo (solo opacity). El consumidor DEBE condicionar `animate="pulse"`
 * con `useReducedMotion()` para no autoreproducir bajo prefers-reduced-motion.
 */
export const statusPulse: Variants = {
	idle: { opacity: 1 },
	pulse: {
		opacity: [1, 0.55, 1],
		transition: { duration: 1.6, repeat: Number.POSITIVE_INFINITY, ease: 'easeInOut' },
	},
};

/** Crossfade de contenido (p.ej. paneles de tabs dentro de AnimatePresence keyed por tab). */
export const crossfade: Variants = {
	initial: { opacity: 0 },
	animate: { opacity: 1, transition: { duration: 0.16, ease: EASE_OUT } },
	exit: { opacity: 0, transition: { duration: 0.12 } },
};

/** Salida del intake al crear el hilo: se hunde con una leve rotación 3D antes de desvanecerse. */
export const threadIntakeExit: Variants = {
	initial: { opacity: 0, y: 6 },
	animate: { opacity: 1, y: 0, transition: { duration: 0.2, ease: EASE_OUT } },
	exit: {
		opacity: 0,
		scale: 0.94,
		y: -14,
		rotateX: 8,
		transition: { duration: 0.4, ease: EASE_OUT },
	},
};

/** Entrada del hilo activo justo después de crearlo: se asienta desde una leve inclinación 3D. */
export const threadLiveEnter: Variants = {
	initial: { opacity: 0, scale: 0.98, y: 14, rotateX: -6 },
	animate: {
		opacity: 1,
		scale: 1,
		y: 0,
		rotateX: 0,
		transition: { type: 'spring', stiffness: 170, damping: 22, delay: 0.12 },
	},
	exit: { opacity: 0, transition: { duration: 0.12 } },
};

/** Shimmer de skeleton (solo opacity). El consumidor DEBE condicionar el loop con `useReducedMotion`. */
export const skeletonShimmer: Variants = {
	idle: { opacity: 0.6 },
	loading: {
		opacity: [0.45, 0.85, 0.45],
		transition: { duration: 1.2, repeat: Number.POSITIVE_INFINITY, ease: 'easeInOut' },
	},
};

export const HOVER_LIFT = { y: -2 } as const;
export const TAP_SCALE = { scale: 0.98 } as const;
export const HOVER_TAP_TRANSITION = { type: 'spring', stiffness: 400, damping: 30 } as const;
