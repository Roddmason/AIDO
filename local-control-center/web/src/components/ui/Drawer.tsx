/**
 * Side-anchored modal dialog: portal + scrim + focus trap (WAI-ARIA dialog), closing on
 * Escape or scrim click. Same contract as {@link Dialog} but slides in from the edge.
 * @author Rodrigo Mason
 */

import type { ReactNode } from 'react';
import { useRef } from 'react';
import { createPortal } from 'react-dom';

import { useI18n } from '../../i18n/I18nProvider';
import { cn } from './cn';
import { IconButton } from './IconButton';
import { useDialogEscape } from './useDialogEscape';
import { useDialogFocus } from './useDialogFocus';

export interface DrawerProps {
	open: boolean;
	onClose: () => void;
	/** Accessible drawer name and visible heading. */
	label: string;
	children: ReactNode;
	className?: string;
}

export function Drawer({ open, onClose, label, children, className }: DrawerProps) {
	const panelRef = useRef<HTMLDivElement>(null);
	const { t } = useI18n();
	useDialogFocus(open, panelRef);
	useDialogEscape(open, onClose);
	if (!open) return null;
	const closeLabel = `${t('app.global.close', 'Close')} ${label}`;
	return createPortal(
		<div className="drawer-layer" role="presentation">
			<button className="drawer-scrim" type="button" aria-label={closeLabel} onClick={onClose} />
			<div
				ref={panelRef}
				tabIndex={-1}
				className={cn('drawer-panel', className)}
				role="dialog"
				aria-modal="true"
				aria-label={label}
			>
				<div className="drawer-header">
					<h2>{label}</h2>
					<IconButton aria-label={closeLabel} onClick={onClose}>
						×
					</IconButton>
				</div>
				{children}
			</div>
		</div>,
		document.body,
	);
}
