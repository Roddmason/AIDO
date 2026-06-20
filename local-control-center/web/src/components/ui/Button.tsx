/**
 * Native `<button>` with typed variants and a uniform loading state.
 *
 * Wraps the design-system `.button` classes — it does NOT reinvent a button, so the
 * native type/disabled/onClick/form semantics pass straight through. `loading` shows a
 * busy spinner, sets `aria-busy`, and disables interaction without shifting layout.
 */

import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { forwardRef } from 'react';

import { cn } from './cn';

export type ButtonVariant = 'primary' | 'secondary' | 'danger';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
	variant?: ButtonVariant;
	loading?: boolean;
	/** Leading visual (decorative); give the button a text label or `aria-label`. */
	icon?: ReactNode;
}

const VARIANT_CLASS: Record<ButtonVariant, string | false> = {
	primary: 'primary',
	secondary: false,
	danger: 'danger',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
	{ variant = 'secondary', loading = false, icon, className, children, disabled, type, ...rest },
	ref,
) {
	return (
		<button
			ref={ref}
			// Default to type="button" so a Button inside a form never submits by accident.
			type={type ?? 'button'}
			className={cn('button', VARIANT_CLASS[variant], className)}
			disabled={disabled || loading}
			aria-busy={loading || undefined}
			{...rest}
		>
			{loading ? <span className="ui-spinner" aria-hidden="true" /> : icon}
			{children}
		</button>
	);
});
