/**
 * Motion hooks that drive the dashboard's enter animations and honor reduced-motion.
 *
 * Both hooks read the OS `prefers-reduced-motion` setting and become no-ops when it
 * is set, so the staggered page-enter effect never fights a user's accessibility choice.
 */
import { useEffect, useRef } from 'react';

/** Mirrors the OS reduced-motion preference onto `<html data-motion>` and `window.__aidoMotionReduced`. */
export function useMotionPreference() {
	useEffect(() => {
		const media = window.matchMedia('(prefers-reduced-motion: reduce)');
		const apply = () => {
			document.documentElement.dataset.motion = media.matches ? 'reduced' : 'full';
			window.__aidoMotionReduced = media.matches;
		};
		apply();
		media.addEventListener('change', apply);
		return () => media.removeEventListener('change', apply);
	}, []);
}

/**
 * Returns a ref whose `[data-motion-item]` descendants fade/slide in on each `dependency`
 * change (e.g. route key), then clears the inline styles so CSS regains control.
 */
export function usePageMotion(dependency: string) {
	const scope = useRef<HTMLElement | null>(null);
	useEffect(() => {
		const root = scope.current;
		if (!root || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
		const items = Array.from(root.querySelectorAll<HTMLElement>('[data-motion-item]'));
		const originalStyles = items.map((item) => ({
			item,
			opacity: item.style.opacity,
			transform: item.style.transform,
			transition: item.style.transition,
		}));
		items.forEach((item) => {
			item.style.opacity = '0';
			item.style.transform = 'translateY(10px)';
			item.style.transition = 'none';
		});
		const frame = window.requestAnimationFrame(() => {
			items.forEach((item, index) => {
				const delay = index * 25;
				item.style.transition = [
					`opacity 180ms ease-out ${delay}ms`,
					`transform 240ms cubic-bezier(0.22, 1, 0.36, 1) ${delay}ms`,
				].join(', ');
				item.style.opacity = '1';
				item.style.transform = 'translateY(0)';
			});
		});
		const cleanup = window.setTimeout(
			() => {
				items.forEach((item) => {
					item.style.transition = '';
					item.style.transform = '';
					item.style.opacity = '';
				});
			},
			240 + Math.max(items.length - 1, 0) * 25,
		);
		return () => {
			window.cancelAnimationFrame(frame);
			window.clearTimeout(cleanup);
			originalStyles.forEach(({ item, opacity, transform, transition }) => {
				item.style.opacity = opacity;
				item.style.transform = transform;
				item.style.transition = transition;
			});
		};
	}, [dependency]);
	return scope;
}

declare global {
	interface Window {
		__aidoMotionReduced?: boolean;
	}
}
