/**
 * Global keyboard layer for the shell: wires Escape, the command-palette toggle
 * and the Ctrl+Alt command-action shortcuts to a single window-level listener.
 * Keeps keyboard navigation working regardless of which page is mounted.
 * @author Rodrigo Mason
 */

import type { RefObject } from 'react';
import { useLayoutEffect } from 'react';
import { hasOpenModalLayer } from '../components/ui/useDialogEscape';
import type { CommandAction } from './commandActions';
import { matchesShortcut } from './commandActions';
import type { AppRoute } from './routing';

/**
 * Installs the shell's global keyboard layer on `window` (capture phase):
 * Escape closes overlays, Ctrl/Cmd+K toggles the command palette, and
 * Ctrl+Alt+<key> dispatches command-action shortcuts plus Ctrl+Alt+E (events)
 * and Ctrl+Alt+W (workflows). Escape and Ctrl/Cmd+K fire even inside inputs;
 * the Ctrl+Alt bindings are suppressed while an editable element is focused.
 * Callbacks must be stable (memoized) so the listener subscribes once.
 */
export function useShellShortcuts({
	commandActionsRef,
	navigateTo,
	onEscape,
	onToggleCommandPalette,
	onOpenEvents,
}: {
	commandActionsRef: RefObject<CommandAction[]>;
	navigateTo: (route: AppRoute) => void;
	onEscape: () => void;
	onToggleCommandPalette: () => void;
	onOpenEvents: () => void;
}): void {
	useLayoutEffect(() => {
		const onKeyDown = (event: KeyboardEvent) => {
			const target = event.target as HTMLElement | null;
			const editableTarget =
				target?.isContentEditable ||
				target?.tagName === 'INPUT' ||
				target?.tagName === 'TEXTAREA' ||
				target?.tagName === 'SELECT';
			// While a Dialog/Drawer overlay is open it owns Escape (LIFO, topmost only);
			// yield so the shell's close-overlays broom never dismisses the layer beneath.
			if (event.key === 'Escape' && !hasOpenModalLayer()) {
				onEscape();
			}
			if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
				event.preventDefault();
				onToggleCommandPalette();
				return;
			}
			if (editableTarget) return;
			if (event.ctrlKey && event.altKey) {
				for (const action of commandActionsRef.current ?? []) {
					if (action.shortcut && !action.disabled && matchesShortcut(event, action.shortcut)) {
						event.preventDefault();
						action.run();
						return;
					}
				}
				const key = event.key.toLowerCase();
				const code = event.code;
				if (key === 'e' || code === 'KeyE') {
					event.preventDefault();
					onOpenEvents();
				}
				if (key === 'w' || code === 'KeyW') {
					event.preventDefault();
					navigateTo('workflows');
				}
			}
		};
		window.addEventListener('keydown', onKeyDown, true);
		return () => window.removeEventListener('keydown', onKeyDown, true);
	}, [commandActionsRef, navigateTo, onEscape, onToggleCommandPalette, onOpenEvents]);
}
