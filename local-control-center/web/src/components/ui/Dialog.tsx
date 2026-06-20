/**
 * Centered modal dialog: portal + scrim + focus trap (WAI-ARIA dialog), closing on
 * Escape or scrim click. Renders nothing when `open` is false. The heading doubles as
 * the accessible name; pass body content as children.
 */

import type { ReactNode } from 'react';
import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

import { useI18n } from '../../i18n/I18nProvider';
import { cn } from './cn';
import { IconButton } from './IconButton';
import { useDialogFocus } from './useDialogFocus';

export interface DialogProps {
	open: boolean;
	onClose: () => void;
	/** Accessible dialog name and visible heading. */
	label: string;
	children: ReactNode;
	className?: string;
}

export function Dialog({ open, onClose, label, children, className }: DialogProps) {
	const panelRef = useRef<HTMLDivElement>(null);
	const { t } = useI18n();
	useDialogFocus(open, panelRef);
	useEffect(() => {
		if (!open) return undefined;
		const closeOnEscape = (event: KeyboardEvent) => {
			if (event.key === 'Escape') onClose();
		};
		window.addEventListener('keydown', closeOnEscape);
		return () => window.removeEventListener('keydown', closeOnEscape);
	}, [open, onClose]);
	if (!open) return null;
	const closeLabel = `${t('app.global.close', 'Close')} ${label}`;
	return createPortal(
		<div className="modal-layer" role="presentation">
			<button className="drawer-scrim" type="button" aria-label={closeLabel} onClick={onClose} />
			<div
				ref={panelRef}
				tabIndex={-1}
				className={cn('modal-panel', className)}
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
