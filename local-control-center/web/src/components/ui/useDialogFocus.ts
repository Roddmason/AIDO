/**
 * WAI-ARIA dialog focus management shared by {@link Dialog} and {@link Drawer}:
 * move focus into the panel on open, keep Tab cycling inside it, and restore focus to
 * the previously focused element on close. Escape handling stays with the caller.
 * @author Rodrigo Mason
 */

import type { RefObject } from 'react';
import { useEffect } from 'react';

const DIALOG_FOCUSABLE =
	'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useDialogFocus(open: boolean, panelRef: RefObject<HTMLElement | null>) {
	useEffect(() => {
		if (!open) return undefined;
		const panel = panelRef.current;
		const previouslyFocused = document.activeElement as HTMLElement | null;
		const focusable = () =>
			Array.from(panel?.querySelectorAll<HTMLElement>(DIALOG_FOCUSABLE) ?? []);
		(focusable()[0] ?? panel)?.focus();
		const onKeyDown = (event: KeyboardEvent) => {
			if (event.key !== 'Tab') return;
			const items = focusable();
			if (!items.length) {
				event.preventDefault();
				panel?.focus();
				return;
			}
			const first = items[0];
			const last = items[items.length - 1];
			const active = document.activeElement;
			if (event.shiftKey && (active === first || active === panel)) {
				event.preventDefault();
				last.focus();
			} else if (!event.shiftKey && active === last) {
				event.preventDefault();
				first.focus();
			}
		};
		document.addEventListener('keydown', onKeyDown, true);
		return () => {
			document.removeEventListener('keydown', onKeyDown, true);
			previouslyFocused?.focus?.();
		};
	}, [open, panelRef]);
}
