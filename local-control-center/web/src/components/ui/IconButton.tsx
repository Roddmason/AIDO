/**
 * Icon-only `<button>` (the design-system `.icon-button`). An accessible name is
 * REQUIRED via `aria-label`, since there is no visible text. Forwards its ref and
 * passes native button props through; `loading` mirrors {@link Button}.
 */

import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { forwardRef } from 'react';

import { cn } from './cn';

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
	/** Required: the button has no visible text. */
	'aria-label': string;
	children: ReactNode;
	loading?: boolean;
	/** Use the accent treatment for primary affordances. */
	emphasis?: 'default' | 'primary';
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
	{ emphasis = 'default', loading = false, className, children, disabled, type, ...rest },
	ref,
) {
	return (
		<button
			ref={ref}
			type={type ?? 'button'}
			className={cn('icon-button', emphasis === 'primary' && 'primary', className)}
			disabled={disabled || loading}
			aria-busy={loading || undefined}
			{...rest}
		>
			{loading ? <span className="ui-spinner" aria-hidden="true" /> : children}
		</button>
	);
});
