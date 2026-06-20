/**
 * Hover/focus tooltip anchored to a single focusable trigger. The trigger is cloned to
 * carry `aria-describedby` (preserving any existing one) so the bubble is announced; the
 * bubble itself is `role="tooltip"` and removed from the a11y tree while hidden.
 *
 * Note: the trigger's own hover/focus handlers are overridden, so wrap simple triggers
 * (a Button/IconButton/link) rather than elements that need their own pointer handlers.
 */

import type { ReactElement } from 'react';
import { cloneElement, useId, useState } from 'react';

import { cn } from './cn';

export interface TooltipProps {
	/** Tooltip text; wired to the trigger via aria-describedby while open. */
	label: string;
	/** A single focusable trigger element. */
	children: ReactElement;
	className?: string;
}

export function Tooltip({ label, children, className }: TooltipProps) {
	const id = useId();
	const [open, setOpen] = useState(false);
	const show = () => setOpen(true);
	const hide = () => setOpen(false);

	const existingDescribedBy = (children.props as { 'aria-describedby'?: string })[
		'aria-describedby'
	];
	const trigger = cloneElement(children, {
		'aria-describedby': cn(existingDescribedBy, open ? id : '') || undefined,
		onMouseEnter: show,
		onMouseLeave: hide,
		onFocus: show,
		onBlur: hide,
	});

	return (
		<span className={cn('ui-tooltip-anchor', className)}>
			{trigger}
			<span className="ui-tooltip" role="tooltip" id={id} hidden={!open}>
				{label}
			</span>
		</span>
	);
}
