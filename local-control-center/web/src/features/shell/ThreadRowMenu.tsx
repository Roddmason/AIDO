/**
 * Contextual menu popover for one sidebar thread row (WAI-ARIA menu pattern).
 *
 * Controlled by the parent: it renders an already-open menu into a body portal, positioned at the
 * right-click coordinates when given or anchored to the trigger element otherwise, clamped to the
 * viewport. Focus lands on the first enabled item; arrow keys/Home/End move it, Escape or an outside
 * press closes and restores focus to the anchor. Disabled items stay focusable with `aria-disabled`
 * plus a visible note so blockers (e.g. "stop the run first") are perceivable, not hidden.
 * @author Rodrigo Mason
 */
import type { LucideIcon } from 'lucide-react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { Fragment, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export type ThreadMenuItem = {
	id: string;
	label: string;
	icon: LucideIcon;
	onSelect: () => void;
	tone?: 'default' | 'danger';
	disabled?: boolean;
	/** Visible reason shown under a disabled item (the blocker), never a silent lockout. */
	note?: string;
	/** Draws a separator above this item (groups destructive actions apart). */
	separated?: boolean;
};

type ThreadRowMenuProps = {
	/** Accessible name of the menu (e.g. "Thread actions"). */
	label: string;
	items: ThreadMenuItem[];
	/** Trigger element: position fallback and focus-restore target on close. */
	anchor: HTMLElement;
	/** Pointer coordinates when opened via right-click; null anchors to the trigger. */
	at: { x: number; y: number } | null;
	onClose: () => void;
};

const VIEWPORT_MARGIN = 8;

/** Renders the open menu; the parent mounts/unmounts it to open/close. */
export function ThreadRowMenu({ label, items, anchor, at, onClose }: ThreadRowMenuProps) {
	const menuRef = useRef<HTMLDivElement>(null);
	const [position, setPosition] = useState<{ top: number; left: number } | null>(null);

	useLayoutEffect(() => {
		const menu = menuRef.current;
		if (!menu) return;
		const rect = menu.getBoundingClientRect();
		const base = at ?? {
			x: anchor.getBoundingClientRect().left,
			y: anchor.getBoundingClientRect().bottom + 2,
		};
		const left = Math.min(
			Math.max(base.x, VIEWPORT_MARGIN),
			window.innerWidth - rect.width - VIEWPORT_MARGIN,
		);
		const top = Math.min(
			Math.max(base.y, VIEWPORT_MARGIN),
			window.innerHeight - rect.height - VIEWPORT_MARGIN,
		);
		setPosition({ top, left });
		const firstEnabled = menu.querySelector<HTMLElement>(
			'[role="menuitem"]:not([aria-disabled="true"])',
		);
		(firstEnabled ?? menu).focus();
	}, [anchor, at]);

	// Keeps the listeners effect mount-once while `onClose` identity changes across renders.
	const onCloseRef = useRef(onClose);
	onCloseRef.current = onClose;

	useLayoutEffect(() => {
		const close = () => onCloseRef.current();
		const closeOnOutsidePress = (event: PointerEvent) => {
			if (menuRef.current?.contains(event.target as Node)) return;
			// Presses on the trigger are left to its own click handler, which toggles the menu
			// closed; closing here too would make that same click immediately reopen it.
			if (anchor.contains(event.target as Node)) return;
			close();
		};
		window.addEventListener('pointerdown', closeOnOutsidePress, true);
		window.addEventListener('resize', close);
		window.addEventListener('blur', close);
		return () => {
			window.removeEventListener('pointerdown', closeOnOutsidePress, true);
			window.removeEventListener('resize', close);
			window.removeEventListener('blur', close);
		};
	}, [anchor]);

	const closeAndRefocus = () => {
		onClose();
		anchor.focus();
	};

	const moveFocus = (direction: 1 | -1) => {
		const menu = menuRef.current;
		if (!menu) return;
		const focusable = Array.from(menu.querySelectorAll<HTMLElement>('[role="menuitem"]'));
		if (!focusable.length) return;
		const current = focusable.indexOf(document.activeElement as HTMLElement);
		const next = (current + direction + focusable.length) % focusable.length;
		focusable[next]?.focus();
	};

	const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
		if (event.key === 'Escape') {
			event.stopPropagation();
			closeAndRefocus();
			return;
		}
		if (event.key === 'Tab') {
			closeAndRefocus();
			return;
		}
		if (event.key === 'ArrowDown') {
			event.preventDefault();
			moveFocus(1);
			return;
		}
		if (event.key === 'ArrowUp') {
			event.preventDefault();
			moveFocus(-1);
			return;
		}
		if (event.key === 'Home' || event.key === 'End') {
			event.preventDefault();
			const menu = menuRef.current;
			const focusable = menu
				? Array.from(menu.querySelectorAll<HTMLElement>('[role="menuitem"]'))
				: [];
			(event.key === 'Home' ? focusable[0] : focusable[focusable.length - 1])?.focus();
		}
	};

	return createPortal(
		<div
			ref={menuRef}
			className="thread-menu"
			role="menu"
			aria-label={label}
			tabIndex={-1}
			style={position ? { top: position.top, left: position.left } : { visibility: 'hidden' }}
			onKeyDown={handleKeyDown}
			onContextMenu={(event) => event.preventDefault()}
		>
			{items.map((item) => {
				const Icon = item.icon;
				return (
					<Fragment key={item.id}>
						{item.separated ? <hr className="thread-menu-separator" /> : null}
						<button
							type="button"
							role="menuitem"
							className="thread-menu-item"
							data-tone={item.tone ?? 'default'}
							aria-disabled={item.disabled || undefined}
							onClick={() => {
								if (item.disabled) return;
								onClose();
								item.onSelect();
							}}
						>
							<Icon aria-hidden="true" size={14} />
							<span className="thread-menu-item-body">
								<span className="thread-menu-item-label">{item.label}</span>
								{item.disabled && item.note ? (
									<span className="thread-menu-note">{item.note}</span>
								) : null}
							</span>
						</button>
					</Fragment>
				);
			})}
		</div>,
		document.body,
	);
}
