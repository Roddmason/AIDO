/**
 * Labelled multi-line input. Same {@link Field} chrome and ref/native passthrough as
 * {@link TextField}; styled with the `.textarea` surface.
 */

import type { ReactNode, TextareaHTMLAttributes } from 'react';
import { forwardRef } from 'react';

import { cn } from './cn';
import { Field } from './Field';

export interface TextAreaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'id'> {
	label: ReactNode;
	help?: ReactNode;
	error?: ReactNode;
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
	{ label, help, error, required, className, ...rest },
	ref,
) {
	return (
		<Field label={label} help={help} error={error} required={required}>
			{({ id, describedBy, invalid }) => (
				<textarea
					ref={ref}
					id={id}
					className={cn('textarea', className)}
					aria-describedby={describedBy}
					aria-invalid={invalid || undefined}
					required={required}
					{...rest}
				/>
			)}
		</Field>
	);
});
