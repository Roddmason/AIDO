/**
 * Layered Escape dismissal shared by {@link Dialog} and {@link Drawer}. Each open
 * overlay pushes its close handler onto a module-level LIFO stack; a single shared
 * window `keydown` listener routes one Escape press to the topmost handler only and
 * calls `stopImmediatePropagation()`, so backing out of a nested overlay dismisses
 * exactly one layer instead of collapsing every open modal at once (WCAG 2.2 layered
 * dismiss). Single-overlay behaviour is unchanged. {@link hasOpenModalLayer} lets the
 * shell keyboard layer defer its own Escape handling to the topmost overlay so its
 * capture-phase "close overlays" broom never dismisses the layer beneath.
 * @author Rodrigo Mason
 */

import { useEffect, useRef } from 'react';

type CloseHandler = () => void;

/** Open overlays in open order; the last entry is the topmost, dismissed first. */
const closeHandlers: CloseHandler[] = [];

function handleWindowKeydown(event: KeyboardEvent) {
	if (event.key !== 'Escape') return;
	const topmost = closeHandlers[closeHandlers.length - 1];
	if (!topmost) return;
	// Only the most-recently-opened overlay consumes the press; stop any sibling
	// window listener so a single Escape never dismisses more than one layer.
	event.stopImmediatePropagation();
	topmost();
}

function pushCloseHandler(handler: CloseHandler) {
	if (closeHandlers.length === 0) {
		window.addEventListener('keydown', handleWindowKeydown);
	}
	closeHandlers.push(handler);
}

function popCloseHandler(handler: CloseHandler) {
	const index = closeHandlers.lastIndexOf(handler);
	if (index !== -1) closeHandlers.splice(index, 1);
	if (closeHandlers.length === 0) {
		window.removeEventListener('keydown', handleWindowKeydown);
	}
}

/** True while any Dialog/Drawer overlay is open and owns Escape dismissal. */
export function hasOpenModalLayer(): boolean {
	return closeHandlers.length > 0;
}

/**
 * Registers `onClose` on the shared LIFO Escape stack while `open`. The stack entry
 * keeps its open-order slot for the overlay's whole lifetime, so a parent re-render
 * (which may hand down a fresh `onClose` identity) never reorders the layers; the
 * latest `onClose` is always invoked through a ref.
 */
export function useDialogEscape(open: boolean, onClose: () => void) {
	const onCloseRef = useRef(onClose);
	onCloseRef.current = onClose;
	useEffect(() => {
		if (!open) return undefined;
		const handler: CloseHandler = () => onCloseRef.current();
		pushCloseHandler(handler);
		return () => popCloseHandler(handler);
	}, [open]);
}
