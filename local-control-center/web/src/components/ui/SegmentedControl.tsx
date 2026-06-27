/**
 * Single-select segmented control (WAI-ARIA radiogroup): one option is checked at a time,
 * with roving-tabindex Arrow/Home/End navigation. Generic over the option value type.
 * @author Rodrigo Mason
 */

import type { KeyboardEvent, ReactNode } from 'react';
import { useRef } from 'react';

import { cn } from './cn';

export interface SegmentedOption<T extends string> {
	value: T;
	label: ReactNode;
}

export interface SegmentedControlProps<T extends string> {
	options: SegmentedOption<T>[];
	value: T;
	onChange: (value: T) => void;
	/** Accessible name for the group. */
	label: string;
	/** Disable the whole control. */
	disabled?: boolean;
	className?: string;
}

export function SegmentedControl<T extends string>({
	options,
	value,
	onChange,
	label,
	disabled = false,
	className,
}: SegmentedControlProps<T>) {
	const optionRefs = useRef<Record<string, HTMLButtonElement | null>>({});

	const select = (next: T) => {
		onChange(next);
		optionRefs.current[next]?.focus();
	};

	const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
		if (disabled) return;
		const index = options.findIndex((option) => option.value === value);
		if (index < 0) return;
		if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
			event.preventDefault();
			select(options[(index + 1) % options.length].value);
		} else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
			event.preventDefault();
			select(options[(index - 1 + options.length) % options.length].value);
		} else if (event.key === 'Home') {
			event.preventDefault();
			select(options[0].value);
		} else if (event.key === 'End') {
			event.preventDefault();
			select(options[options.length - 1].value);
		}
	};

	return (
		<div
			className={cn('segmented', className)}
			role="radiogroup"
			aria-label={label}
			onKeyDown={onKeyDown}
		>
			{options.map((option) => {
				const checked = option.value === value;
				return (
					<button
						key={option.value}
						ref={(node) => {
							optionRefs.current[option.value] = node;
						}}
						className="segmented-option"
						type="button"
						role="radio"
						aria-checked={checked}
						tabIndex={checked ? 0 : -1}
						disabled={disabled}
						onClick={() => onChange(option.value)}
					>
						{option.label}
					</button>
				);
			})}
		</div>
	);
}
