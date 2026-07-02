/**
 * Providers de Motion para el árbol completo: `LazyMotion domMax strict` (API ligera `m.*`)
 * más `MotionConfig` cuyo `reducedMotion` sigue el override del usuario — 'always' cuando la
 * preferencia guardada es "reducido", 'user' (media query del SO) en caso contrario. Así el
 * control de movimiento de Appearance gobierna de verdad las animaciones declarativas.
 * @author Rodrigo Mason
 */
import { domMax, LazyMotion, MotionConfig } from 'motion/react';
import type { ReactNode } from 'react';

import { useMotionSetting } from './useControlMotion';

/** Envuelve la app con LazyMotion + MotionConfig reactivo a la preferencia de movimiento. */
export function MotionProvider({ children }: { children: ReactNode }) {
	const { motion } = useMotionSetting();
	return (
		<LazyMotion features={domMax} strict>
			<MotionConfig reducedMotion={motion === 'reduced' ? 'always' : 'user'}>
				{children}
			</MotionConfig>
		</LazyMotion>
	);
}
