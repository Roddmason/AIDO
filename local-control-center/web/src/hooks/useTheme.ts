/**
 * Dark/light theme state, persisted to localStorage and applied to `<html>`.
 *
 * Centralizes theme reads/writes so the pre-paint bootstrap and the runtime toggle
 * share one storage key and one DOM attribute, keeping `color-scheme` consistent.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useState } from 'react';

export type Theme = 'dark' | 'light';

const THEME_STORAGE_KEY = 'aido:theme';

/**
 * Theme is dark-first by design; light is an opt-in override wired through
 * `:root[data-theme="light"]` in tokens.css. The choice persists in
 * localStorage and is applied to <html> so native form controls and scrollbars
 * follow via `color-scheme`.
 */
function readStoredTheme(): Theme {
	try {
		return window.localStorage.getItem(THEME_STORAGE_KEY) === 'light' ? 'light' : 'dark';
	} catch {
		return 'dark';
	}
}

function applyTheme(theme: Theme) {
	document.documentElement.dataset.theme = theme;
}

function persistTheme(theme: Theme) {
	try {
		window.localStorage.setItem(THEME_STORAGE_KEY, theme);
	} catch {}
}

/** Applies the persisted theme to <html> before React renders, avoiding a flash. */
export function applyStoredTheme() {
	applyTheme(readStoredTheme());
}

/** Exposes the current theme and a toggle that persists the new choice and updates `<html>`. */
export function useTheme() {
	const [theme, setThemeState] = useState<Theme>(readStoredTheme);

	useEffect(() => {
		applyTheme(theme);
	}, [theme]);

	const toggleTheme = useCallback(() => {
		setThemeState((current) => {
			const next: Theme = current === 'dark' ? 'light' : 'dark';
			persistTheme(next);
			return next;
		});
	}, []);

	return { theme, toggleTheme };
}
