/**
 * Labelled text input. Wraps the native `<input class="input">` in {@link Field} so the
 * label, help and error wiring is uniform; forwards its ref and passes every native input
 * prop through (type, value, onChange, placeholder, autoComplete, …).
 * @author Rodrigo Mason
 */

import type { InputHTMLAttributes, ReactNode } from 'react';
import { forwardRef } from 'react';

import { cn } from './cn';
import { Field } from './Field';

export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
	label: ReactNode;
	help?: ReactNode;
	error?: ReactNode;
}

export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(function TextField(
	{ label, help, error, required, className, type, ...rest },
	ref,
) {
	return (
		<Field label={label} help={help} error={error} required={required}>
			{({ id, describedBy, invalid }) => (
				<input
					ref={ref}
					id={id}
					type={type ?? 'text'}
					className={cn('input', className)}
					aria-describedby={describedBy}
					aria-invalid={invalid || undefined}
					required={required}
					{...rest}
				/>
			)}
		</Field>
	);
});
