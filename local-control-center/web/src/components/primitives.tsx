/**
 * Shared presentational UI primitives reused across the control-center pages.
 *
 * Badges, surfaces, tables and the dialog/drawer pair live here so layout, motion
 * hooks (`data-motion-item`) and the accessible-dialog focus trap stay consistent
 * everywhere instead of being re-implemented per feature.
 */

import { m, useReducedMotion } from 'motion/react';
import type { ReactNode, RefObject } from 'react';
import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

import { useI18n } from '../i18n/I18nProvider';
import { EASE_OUT, skeletonShimmer } from '../motion/variants';

/** Crossfade breve al cambiar de tono: keyed por tono para reanimar opacity sin tocar el color (CSS). */
const TONE_CHANGE = {
	initial: { opacity: 0.55 },
	animate: { opacity: 1, transition: { duration: 0.2, ease: EASE_OUT } },
} as const;

export function Badge({
	children,
	tone,
}: {
	children: ReactNode;
	tone?: 'ok' | 'warn' | 'danger' | 'info' | 'pending';
}) {
	// Under prefers-reduced-motion the tone crossfade is suppressed (opacity is not a
	// positional key, so MotionConfig does not neutralize it on its own — gate the enter).
	const prefersReducedMotion = useReducedMotion();
	return (
		<m.span
			key={tone ?? 'default'}
			className="badge"
			data-tone={tone}
			variants={TONE_CHANGE}
			initial={prefersReducedMotion ? false : 'initial'}
			animate="animate"
		>
			{children}
		</m.span>
	);
}

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

export function EmptyState({ title, body }: { title: string; body: string }) {
	return (
		<div className="empty-state">
			<strong>{title}</strong>
			<span>{body}</span>
		</div>
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

const DIALOG_FOCUSABLE =
	'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Modal dialog focus management for Drawer/Modal (WAI-ARIA dialog pattern):
 * move focus into the panel on open, keep Tab cycling inside it, and restore
 * focus to the previously focused element on close. Escape handling stays in the
 * caller's own effect so closing behaviour is unchanged.
 */
function useDialogFocus(open: boolean, panelRef: RefObject<HTMLElement | null>) {
	useEffect(() => {
		if (!open) return undefined;
		const panel = panelRef.current;
		const previouslyFocused = document.activeElement as HTMLElement | null;
		const focusable = () =>
			Array.from(panel?.querySelectorAll<HTMLElement>(DIALOG_FOCUSABLE) ?? []);
		(focusable()[0] ?? panel)?.focus();
		const onKeyDown = (event: KeyboardEvent) => {
			if (event.key !== 'Tab') return;
			const items = focusable();
			if (!items.length) {
				event.preventDefault();
				panel?.focus();
				return;
			}
			const first = items[0];
			const last = items[items.length - 1];
			const active = document.activeElement;
			if (event.shiftKey && (active === first || active === panel)) {
				event.preventDefault();
				last.focus();
			} else if (!event.shiftKey && active === last) {
				event.preventDefault();
				first.focus();
			}
		};
		document.addEventListener('keydown', onKeyDown, true);
		return () => {
			document.removeEventListener('keydown', onKeyDown, true);
			previouslyFocused?.focus?.();
		};
	}, [open, panelRef]);
}

/** Side-anchored modal dialog: portal + scrim + focus trap, closes on Escape or scrim click. */
export function Drawer({
	children,
	label,
	open,
	onClose,
}: {
	children: ReactNode;
	label: string;
	open: boolean;
	onClose: () => void;
}) {
	const panelRef = useRef<HTMLDivElement>(null);
	const { t } = useI18n();
	useDialogFocus(open, panelRef);
	useEffect(() => {
		if (!open) return undefined;
		const closeOnEscape = (event: KeyboardEvent) => {
			if (event.key === 'Escape') onClose();
		};
		window.addEventListener('keydown', closeOnEscape);
		return () => window.removeEventListener('keydown', closeOnEscape);
	}, [open, onClose]);
	if (!open) return null;
	return createPortal(
		<div className="drawer-layer" role="presentation">
			<button
				className="drawer-scrim"
				type="button"
				aria-label={`${t('app.global.close', 'Close')} ${label}`}
				onClick={onClose}
			/>
			<div
				ref={panelRef}
				tabIndex={-1}
				className="drawer-panel"
				role="dialog"
				aria-modal="true"
				aria-label={label}
			>
				<div className="drawer-header">
					<h2>{label}</h2>
					<button
						className="icon-button"
						type="button"
						aria-label={`${t('app.global.close', 'Close')} ${label}`}
						onClick={onClose}
					>
						×
					</button>
				</div>
				{children}
			</div>
		</div>,
		document.body,
	);
}

/**
 * Loading placeholder with a subtle opacity shimmer ({@link skeletonShimmer}).
 *
 * The shimmer loop is GATED by {@link useReducedMotion}: under prefers-reduced-motion
 * it stays on the static `idle` opacity (no autoplay). Reduced-motion is otherwise
 * handled globally by `MotionConfig reducedMotion="user"`; this gate only stops the loop.
 */
export function Skeleton({
	className,
	label,
}: {
	className?: string;
	/** Optional accessible status label; falls back to `aria-hidden` decorative placeholder. */
	label?: string;
}) {
	const prefersReducedMotion = useReducedMotion();
	return (
		<m.div
			className={className ? `skeleton ${className}` : 'skeleton'}
			variants={skeletonShimmer}
			initial="idle"
			animate={prefersReducedMotion ? 'idle' : 'loading'}
			role={label ? 'status' : undefined}
			aria-label={label}
			aria-hidden={label ? undefined : true}
		/>
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

/** Centered variant of {@link Drawer} for confirmations; same focus trap and close behaviour. */
export function Modal({
	children,
	label,
	open,
	onClose,
}: {
	children: ReactNode;
	label: string;
	open: boolean;
	onClose: () => void;
}) {
	const panelRef = useRef<HTMLDivElement>(null);
	const { t } = useI18n();
	useDialogFocus(open, panelRef);
	useEffect(() => {
		if (!open) return undefined;
		const closeOnEscape = (event: KeyboardEvent) => {
			if (event.key === 'Escape') onClose();
		};
		window.addEventListener('keydown', closeOnEscape);
		return () => window.removeEventListener('keydown', closeOnEscape);
	}, [open, onClose]);
	if (!open) return null;
	return createPortal(
		<div className="modal-layer" role="presentation">
			<button
				className="drawer-scrim"
				type="button"
				aria-label={`${t('app.global.close', 'Close')} ${label}`}
				onClick={onClose}
			/>
			<div
				ref={panelRef}
				tabIndex={-1}
				className="modal-panel"
				role="dialog"
				aria-modal="true"
				aria-label={label}
			>
				<div className="drawer-header">
					<h2>{label}</h2>
					<button
						className="icon-button"
						type="button"
						aria-label={`${t('app.global.close', 'Close')} ${label}`}
						onClick={onClose}
					>
						×
					</button>
				</div>
				{children}
			</div>
		</div>,
		document.body,
	);
}
