/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
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
	} catch {
		// localStorage is optional in restricted browser contexts.
	}
}

/** Applies the persisted theme to <html> before React renders, avoiding a flash. */
export function applyStoredTheme() {
	applyTheme(readStoredTheme());
}

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
