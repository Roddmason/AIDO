/**
 * Centered modal dialog: portal + scrim + focus trap (WAI-ARIA dialog), closing on
 * Escape or scrim click. Animates in with a scrim fade and a panel scale-from-0.96 +
 * fade; exits faster (scale-down + fade). Under `MotionConfig reducedMotion="user"`
 * transforms are suppressed and only the opacity fade remains. Renders nothing when
 * unmounted by AnimatePresence after the exit animation completes.
 */

import type { Variants } from 'motion/react';
import { AnimatePresence, m } from 'motion/react';
import type { ReactNode } from 'react';
import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

import { useI18n } from '../../i18n/I18nProvider';
import { EASE_OUT } from '../../motion/variants';
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

/** Scrim fades in over --duration-normal; exits faster. */
const scrimVariants: Variants = {
	initial: { opacity: 0 },
	animate: { opacity: 1, transition: { duration: 0.18, ease: 'easeOut' } },
	exit: { opacity: 0, transition: { duration: 0.13, ease: 'easeIn' } },
};

/**
 * Panel: scale from 0.96 + fade on enter; scale-down + faster fade on exit.
 * Under `MotionConfig reducedMotion="user"` the scale collapses to a no-op
 * and only the opacity transition remains (accessible plain fade).
 */
const panelVariants: Variants = {
	initial: { opacity: 0, scale: 0.96 },
	animate: { opacity: 1, scale: 1, transition: { duration: 0.2, ease: EASE_OUT } },
	exit: { opacity: 0, scale: 0.97, transition: { duration: 0.13, ease: 'easeIn' } },
};

function DialogContent({ open, onClose, label, children, className }: DialogProps) {
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
	const closeLabel = `${t('app.global.close', 'Close')} ${label}`;
	return (
		<m.div
			className="modal-layer"
			role="presentation"
			variants={scrimVariants}
			initial="initial"
			animate="animate"
			exit="exit"
		>
			<button className="drawer-scrim" type="button" aria-label={closeLabel} onClick={onClose} />
			<m.div
				ref={panelRef}
				tabIndex={-1}
				className={cn('modal-panel', className)}
				role="dialog"
				aria-modal="true"
				aria-label={label}
				variants={panelVariants}
				initial="initial"
				animate="animate"
				exit="exit"
			>
				<div className="drawer-header">
					<h2>{label}</h2>
					<IconButton aria-label={closeLabel} onClick={onClose}>
						×
					</IconButton>
				</div>
				{children}
			</m.div>
		</m.div>
	);
}

export function Dialog({ open, onClose, label, children, className }: DialogProps) {
	return createPortal(
		<AnimatePresence>
			{open ? (
				<DialogContent
					key="dialog"
					open={open}
					onClose={onClose}
					label={label}
					className={className}
				>
					{children}
				</DialogContent>
			) : null}
		</AnimatePresence>,
		document.body,
	);
}
