/**
 * Hook de preferencia de movimiento: refleja `prefers-reduced-motion` del SO en
 * `<html data-motion>` para que CSS y los tests puedan reaccionar.
 *
 * La animación es declarativa (Motion + `MotionConfig reducedMotion="user"` y las variantes de
 * `motion/variants.ts`). Aquí NO hay manipulación imperativa de estilos, ni `requestAnimationFrame`,
 * ni estado global en `window`: solo se publica un atributo de estado en `<html>`.
 */
import { useEffect } from 'react';

/** Refleja la preferencia de reduced-motion del SO en `<html data-motion="reduced|full">`. */
export function useMotionPreference() {
	useEffect(() => {
		const media = window.matchMedia('(prefers-reduced-motion: reduce)');
		const apply = () => {
			document.documentElement.dataset.motion = media.matches ? 'reduced' : 'full';
		};
		apply();
		media.addEventListener('change', apply);
		return () => media.removeEventListener('change', apply);
	}, []);
}
