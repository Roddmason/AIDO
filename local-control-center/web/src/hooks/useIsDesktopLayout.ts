/**
 * Tracks whether the viewport is wide enough for the desktop IDE layout (the
 * resizable multi-pane shell). Below the breakpoint the shell falls back to a
 * stacked single-column layout that flows naturally on narrow/touch screens.
 *
 * The initial value is read synchronously from `matchMedia`, so the correct
 * layout renders on the first paint and the shell does not shift after mount.
 */
import { useEffect, useState } from 'react';

// Mirrors the design-system `min-width: 981px` breakpoint that switches the
// shell between the stacked layout and the resizable desktop layout.
const DESKTOP_QUERY = '(min-width: 981px)';

function readDesktopMatch(): boolean {
	if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return true;
	return window.matchMedia(DESKTOP_QUERY).matches;
}

/** `true` when the viewport qualifies for the resizable desktop IDE layout. */
export function useIsDesktopLayout(): boolean {
	const [isDesktop, setIsDesktop] = useState(readDesktopMatch);
	useEffect(() => {
		if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
		const query = window.matchMedia(DESKTOP_QUERY);
		const onChange = () => setIsDesktop(query.matches);
		query.addEventListener('change', onChange);
		return () => query.removeEventListener('change', onChange);
	}, []);
	return isDesktop;
}
