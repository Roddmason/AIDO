/**
 * Hook de preferencia de movimiento: combina `prefers-reduced-motion` del SO con un
 * override explícito del usuario (localStorage `aido:motion`: 'system' | 'reduced') y
 * refleja la preferencia efectiva en `<html data-motion>` para que CSS y los tests
 * puedan reaccionar. `MotionProvider` mapea el mismo override a `MotionConfig`, así
 * "reducido" apaga de verdad las animaciones declarativas, no solo un atributo.
 *
 * La animación es declarativa (Motion + variantes de `motion/variants.ts`). Aquí NO hay
 * manipulación imperativa de estilos ni `requestAnimationFrame`: solo estado publicado en
 * `<html>` y un evento de sincronización entre instancias del hook.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useState } from 'react';

export type MotionSetting = 'system' | 'reduced';

const MOTION_STORAGE_KEY = 'aido:motion';
const MOTION_CHANGE_EVENT = 'aido:motion-setting';

function readStoredMotionSetting(): MotionSetting {
	try {
		return window.localStorage.getItem(MOTION_STORAGE_KEY) === 'reduced' ? 'reduced' : 'system';
	} catch {
		return 'system';
	}
}

function persistMotionSetting(setting: MotionSetting) {
	try {
		window.localStorage.setItem(MOTION_STORAGE_KEY, setting);
	} catch {}
	window.dispatchEvent(new Event(MOTION_CHANGE_EVENT));
}

/** Current user override plus a setter; instances stay in sync via a window event. */
export function useMotionSetting() {
	const [motion, setMotionState] = useState<MotionSetting>(readStoredMotionSetting);

	useEffect(() => {
		const sync = () => setMotionState(readStoredMotionSetting());
		window.addEventListener(MOTION_CHANGE_EVENT, sync);
		return () => window.removeEventListener(MOTION_CHANGE_EVENT, sync);
	}, []);

	const setMotion = useCallback((next: MotionSetting) => {
		persistMotionSetting(next);
		setMotionState(next);
	}, []);

	return { motion, setMotion };
}

/** Refleja la preferencia efectiva (override o SO) en `<html data-motion="reduced|full">`. */
export function useMotionPreference() {
	useEffect(() => {
		const media = window.matchMedia('(prefers-reduced-motion: reduce)');
		const apply = () => {
			const reduced = readStoredMotionSetting() === 'reduced' || media.matches;
			document.documentElement.dataset.motion = reduced ? 'reduced' : 'full';
		};
		apply();
		media.addEventListener('change', apply);
		window.addEventListener(MOTION_CHANGE_EVENT, apply);
		return () => {
			media.removeEventListener('change', apply);
			window.removeEventListener(MOTION_CHANGE_EVENT, apply);
		};
	}, []);
}
