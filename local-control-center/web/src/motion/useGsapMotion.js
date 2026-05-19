import { useLayoutEffect, useRef } from 'react';
import { gsap } from 'gsap';

function reducedMotionEnabled() {
	return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function useMotionPreference() {
	useLayoutEffect(() => {
		if (typeof window === 'undefined') return undefined;
		const media = window.matchMedia('(prefers-reduced-motion: reduce)');
		const apply = () => {
			document.documentElement.dataset.motion = media.matches ? 'reduced' : 'full';
			window.__lccMotionReduced = media.matches;
		};
		apply();
		media.addEventListener('change', apply);
		return () => media.removeEventListener('change', apply);
	}, []);
}

export function useSectionMotion(dependency) {
	const ref = useRef(null);

	useLayoutEffect(() => {
		if (!ref.current || reducedMotionEnabled()) return undefined;
		const context = gsap.context(() => {
			const timeline = gsap.timeline({
				defaults: { duration: 0.34, ease: 'power3.out', clearProps: 'transform,opacity,visibility' },
			});
			timeline.addLabel('enter', 0);
			timeline.from('[data-motion-item]', { y: 12, autoAlpha: 0, stagger: 0.035 }, 'enter');
		}, ref);
		return () => context.revert();
	}, [dependency]);

	return ref;
}

export function useLiveListMotion(dependency) {
	const ref = useRef(null);

	useLayoutEffect(() => {
		if (!ref.current || reducedMotionEnabled()) return undefined;
		const context = gsap.context(() => {
			gsap.from('[data-live-row]', {
				y: 8,
				autoAlpha: 0,
				duration: 0.24,
				ease: 'power2.out',
				stagger: 0.018,
				clearProps: 'transform,opacity,visibility',
			});
		}, ref);
		return () => context.revert();
	}, [dependency]);

	return ref;
}
