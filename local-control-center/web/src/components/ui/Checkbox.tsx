/**
 * Labelled checkbox row (`.checkbox-row`). The native `<input type="checkbox">` is wrapped
 * by its `<label>` so the whole row toggles it; ref and native props (checked, onChange,
 * disabled) pass straight through.
 * @author Rodrigo Mason
 */

import type { InputHTMLAttributes, ReactNode } from 'react';
import { forwardRef, useId } from 'react';

import { cn } from './cn';

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'id'> {
	label: ReactNode;
	help?: ReactNode;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
	{ label, help, className, ...rest },
	ref,
) {
	const id = useId();
	const helpId = `${id}-help`;
	return (
		<label className={cn('checkbox-row', className)} htmlFor={id}>
			<input
				ref={ref}
				id={id}
				type="checkbox"
				aria-describedby={help ? helpId : undefined}
				{...rest}
			/>
			<span>
				{label}
				{help ? (
					<span className="field-help" id={helpId}>
						{help}
					</span>
				) : null}
			</span>
		</label>
	);
});
