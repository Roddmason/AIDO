/**
 * Presentational primitives not (yet) part of the typed component library: the status
 * dot, content surface, page header, data table and progress bar.
 *
 * The library-equivalent primitives live in `components/ui` and are re-exported here so
 * existing `import { Badge, EmptyState, Skeleton, Drawer, Modal } from '../primitives'`
 * sites keep working against a single implementation while pages migrate to `ui` directly.
 * @author Rodrigo Mason
 */
import { m, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';

import { EASE_OUT } from '../motion/variants';

export { Dialog as Modal, Drawer, EmptyState, Skeleton, StatusChip as Badge } from './ui';

/** Crossfade breve al cambiar de tono: keyed por tono para reanimar opacity sin tocar el color (CSS). */
const TONE_CHANGE = {
	initial: { opacity: 0.55 },
	animate: { opacity: 1, transition: { duration: 0.2, ease: EASE_OUT } },
} as const;

export function StatusDot({
	tone = 'info',
}: {
	tone?: 'ok' | 'warn' | 'danger' | 'info' | 'pending';
}) {
	const prefersReducedMotion = useReducedMotion();
	return (
		<m.span
			key={tone}
			className="status-dot"
			data-tone={tone}
			aria-hidden="true"
			variants={TONE_CHANGE}
			initial={prefersReducedMotion ? false : 'initial'}
			animate="animate"
		/>
	);
}

/** Card-like content section; participates in page enter animations via `data-motion-item`. */
export function Surface({
	title,
	children,
	flat = false,
}: {
	title?: string;
	children: ReactNode;
	flat?: boolean;
}) {
	return (
		<section className={`surface${flat ? ' flat' : ''}`} data-motion-item>
			{title ? <h2 className="surface-title">{title}</h2> : null}
			{children}
		</section>
	);
}

export function PageHeader({
	kicker,
	title,
	summary,
}: {
	kicker: string;
	title: string;
	summary: string;
}) {
	return (
		<header className="page-header" data-motion-item>
			<div className="page-kicker">{kicker}</div>
			<h1 className="page-title">{title}</h1>
			<p className="page-summary">{summary}</p>
		</header>
	);
}

/** Generic column-driven table that renders `empty` when there are no rows; keys by row `id`. */
export function DataTable<T>({
	columns,
	rows,
	empty,
	caption,
}: {
	columns: Array<{ key: string; label: string; render: (row: T) => ReactNode }>;
	rows: T[];
	empty: ReactNode;
	caption?: string;
}) {
	if (!rows.length) return <>{empty}</>;
	return (
		<div className="table-wrap">
			<table className="data-table">
				{caption ? <caption className="sr-only">{caption}</caption> : null}
				<thead>
					<tr>
						{columns.map((column) => (
							<th key={column.key} scope="col">
								{column.label}
							</th>
						))}
					</tr>
				</thead>
				<tbody>
					{rows.map((row, index) => (
						<tr key={String((row as { id?: unknown }).id ?? index)}>
							{columns.map((column) => (
								<td key={column.key} data-label={column.label}>
									{column.render(row)}
								</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}

/**
 * Animated progress bar that eases its fill width to `value` (0–100, clamped).
 *
 * Width is animated declaratively via `m.div`; reduced-motion is honored by
 * `MotionConfig reducedMotion="user"` (the easing collapses to an instant set).
 * Exposes the WAI-ARIA `progressbar` role with current/min/max values.
 */
export function Progress({
	value,
	label,
	className,
}: {
	/** Completion percentage; clamped to the 0–100 range. */
	value: number;
	/** Accessible name for the progress bar. */
	label: string;
	className?: string;
}) {
	const clamped = Math.min(100, Math.max(0, value));
	return (
		<div
			className={className ? `progress ${className}` : 'progress'}
			role="progressbar"
			aria-label={label}
			aria-valuenow={clamped}
			aria-valuemin={0}
			aria-valuemax={100}
		>
			<m.div
				className="progress-fill"
				initial={false}
				animate={{ width: `${clamped}%` }}
				transition={{ duration: 0.3, ease: EASE_OUT }}
			/>
		</div>
	);
}
