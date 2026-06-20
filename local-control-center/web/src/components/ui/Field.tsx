/**
 * Shared form-field chrome: label + control + help/error, with the id/aria wiring done
 * once so every field reports `aria-describedby`, `aria-invalid` and an `role="alert"`
 * error message uniformly. Field controls (TextField/TextArea/SelectField) render their
 * native element through the `children` render-prop using the supplied wiring.
 */

import type { ReactNode } from 'react';
import { useId } from 'react';

import { cn } from './cn';

/** Wiring handed to a field control so it associates itself with the label/help/error. */
export interface FieldControl {
	id: string;
	describedBy: string | undefined;
	invalid: boolean;
}

export interface FieldProps {
	label: ReactNode;
	/** Help text shown under the control when there is no error. */
	help?: ReactNode;
	/** Error message; when set the control is marked invalid and the help is replaced. */
	error?: ReactNode;
	required?: boolean;
	className?: string;
	children: (control: FieldControl) => ReactNode;
}

export function Field({ label, help, error, required, className, children }: FieldProps) {
	const id = useId();
	const helpId = `${id}-help`;
	const errorId = `${id}-error`;
	const describedBy = cn(help && !error ? helpId : '', error ? errorId : '') || undefined;
	return (
		<div className={cn('field', className)}>
			<label className="field-label" htmlFor={id}>
				{label}
				{required ? <span aria-hidden="true"> *</span> : null}
			</label>
			{children({ id, describedBy, invalid: Boolean(error) })}
			{help && !error ? (
				<span className="field-help" id={helpId}>
					{help}
				</span>
			) : null}
			{error ? (
				<span className="field-error" id={errorId} role="alert">
					{error}
				</span>
			) : null}
		</div>
	);
}
