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
const THEME_CHANGE_EVENT = 'aido:theme-setting';

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
	window.dispatchEvent(new Event(THEME_CHANGE_EVENT));
}

/** Applies the persisted theme to <html> before React renders, avoiding a flash. */
export function applyStoredTheme() {
	applyTheme(readStoredTheme());
}

/** Exposes the current theme, a direct setter, and a toggle; both persist and update `<html>`.
 *  Instances stay in sync via a window event (StatusBar, MenuBar and Settings share state). */
export function useTheme() {
	const [theme, setThemeState] = useState<Theme>(readStoredTheme);

	useEffect(() => {
		applyTheme(theme);
	}, [theme]);

	useEffect(() => {
		const sync = () => setThemeState(readStoredTheme());
		window.addEventListener(THEME_CHANGE_EVENT, sync);
		return () => window.removeEventListener(THEME_CHANGE_EVENT, sync);
	}, []);

	const setTheme = useCallback((next: Theme) => {
		persistTheme(next);
		setThemeState(next);
	}, []);

	const toggleTheme = useCallback(() => {
		// Reads storage (not stale state) and persists outside the updater so the
		// cross-instance sync event never fires mid-render.
		const next: Theme = readStoredTheme() === 'dark' ? 'light' : 'dark';
		persistTheme(next);
		setThemeState(next);
	}, []);

	return { theme, setTheme, toggleTheme };
}
