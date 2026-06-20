/**
 * Comfortable/compact UI density, persisted to localStorage and applied to `<html>`.
 *
 * Mirrors the theme mechanism: one storage key and the `data-density` attribute drive
 * the `:root[data-density="compact"]` control-sizing overrides in tokens.css, so the
 * pre-paint bootstrap and the runtime toggle stay in sync. Comfortable is the default
 * (touch-safe); compact tightens controls for information-dense, mouse-first sessions.
 */
import { useCallback, useEffect, useState } from 'react';

export type Density = 'comfortable' | 'compact';

const DENSITY_STORAGE_KEY = 'aido:density';

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
	} catch {
		// localStorage is optional in restricted browser contexts.
	}
}

/** Applies the persisted density to <html> before React renders, avoiding a flash. */
export function applyStoredDensity() {
	applyDensity(readStoredDensity());
}

/** Exposes the current density and a toggle that persists the new choice and updates `<html>`. */
export function useDensity() {
	const [density, setDensityState] = useState<Density>(readStoredDensity);

	useEffect(() => {
		applyDensity(density);
	}, [density]);

	const toggleDensity = useCallback(() => {
		setDensityState((current) => {
			const next: Density = current === 'comfortable' ? 'compact' : 'comfortable';
			persistDensity(next);
			return next;
		});
	}, []);

	return { density, toggleDensity };
}
