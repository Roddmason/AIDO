/**
 * Comfortable/compact UI density, persisted to localStorage and applied to `<html>`.
 *
 * Mirrors the theme mechanism: one storage key and the `data-density` attribute drive
 * the `:root[data-density="compact"]` control-sizing overrides in tokens.css, so the
 * pre-paint bootstrap and the runtime toggle stay in sync. Comfortable is the default
 * (touch-safe); compact tightens controls for information-dense, mouse-first sessions.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useState } from 'react';

export type Density = 'comfortable' | 'compact';

const DENSITY_STORAGE_KEY = 'aido:density';
const DENSITY_CHANGE_EVENT = 'aido:density-setting';

function readStoredDensity(): Density {
	try {
		return window.localStorage.getItem(DENSITY_STORAGE_KEY) === 'compact'
			? 'compact'
			: 'comfortable';
	} catch {
		return 'comfortable';
	}
}

function applyDensity(density: Density) {
	document.documentElement.dataset.density = density;
}

function persistDensity(density: Density) {
	try {
		window.localStorage.setItem(DENSITY_STORAGE_KEY, density);
	} catch {}
	window.dispatchEvent(new Event(DENSITY_CHANGE_EVENT));
}

/** Applies the persisted density to <html> before React renders, avoiding a flash. */
export function applyStoredDensity() {
	applyDensity(readStoredDensity());
}

/** Exposes the current density, a direct setter, and a toggle; both persist and update `<html>`.
 *  Instances stay in sync via a window event (StatusBar, MenuBar and Settings share state). */
export function useDensity() {
	const [density, setDensityState] = useState<Density>(readStoredDensity);

	useEffect(() => {
		applyDensity(density);
	}, [density]);

	useEffect(() => {
		const sync = () => setDensityState(readStoredDensity());
		window.addEventListener(DENSITY_CHANGE_EVENT, sync);
		return () => window.removeEventListener(DENSITY_CHANGE_EVENT, sync);
	}, []);

	const setDensity = useCallback((next: Density) => {
		persistDensity(next);
		setDensityState(next);
	}, []);

	const toggleDensity = useCallback(() => {
		// Reads storage (not stale state) and persists outside the updater so the
		// cross-instance sync event never fires mid-render.
		const next: Density = readStoredDensity() === 'comfortable' ? 'compact' : 'comfortable';
		persistDensity(next);
		setDensityState(next);
	}, []);

	return { density, setDensity, toggleDensity };
}
