/**
 * Labelled native `<select>`. Pass `<option>`s as children. Same {@link Field} chrome,
 * ref forwarding and native passthrough as {@link TextField}.
 * @author Rodrigo Mason
 */

import type { ReactNode, SelectHTMLAttributes } from 'react';
import { forwardRef } from 'react';

import { cn } from './cn';
import { Field } from './Field';

export interface SelectFieldProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> {
	label: ReactNode;
	help?: ReactNode;
	error?: ReactNode;
	children: ReactNode;
}

export const SelectField = forwardRef<HTMLSelectElement, SelectFieldProps>(function SelectField(
	{ label, help, error, required, className, children, ...rest },
	ref,
) {
	return (
		<Field label={label} help={help} error={error} required={required}>
			{({ id, describedBy, invalid }) => (
				<select
					ref={ref}
					id={id}
					className={cn('select', className)}
					aria-describedby={describedBy}
					aria-invalid={invalid || undefined}
					required={required}
					{...rest}
				>
					{children}
				</select>
			)}
		</Field>
	);
});
