/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { ReactNode, RefObject } from 'react';

import { useI18n } from '../i18n/I18nProvider';

export function Badge({ children, tone }: { children: ReactNode; tone?: 'ok' | 'warn' | 'danger' | 'info' | 'pending' }) {
	return (
		<span className="badge" data-tone={tone}>
			{children}
		</span>
	);
}

export function StatusDot({ tone = 'info' }: { tone?: 'ok' | 'warn' | 'danger' | 'info' | 'pending' }) {
	return <span className="status-dot" data-tone={tone} aria-hidden="true" />;
}

export function Surface({ title, children, flat = false }: { title?: string; children: ReactNode; flat?: boolean }) {
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

export function PageHeader({ kicker, title, summary }: { kicker: string; title: string; summary: string }) {
	return (
		<header className="page-header" data-motion-item>
			<div className="page-kicker">{kicker}</div>
			<h1 className="page-title">{title}</h1>
			<p className="page-summary">{summary}</p>
		</header>
	);
}

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
								<td key={column.key} data-label={column.label}>{column.render(row)}</td>
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
		const focusable = () => Array.from(panel?.querySelectorAll<HTMLElement>(DIALOG_FOCUSABLE) ?? []);
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
			<button className="drawer-scrim" type="button" aria-label={`${t('app.global.close', 'Close')} ${label}`} onClick={onClose} />
			<div ref={panelRef} tabIndex={-1} className="drawer-panel" role="dialog" aria-modal="true" aria-label={label}>
				<div className="drawer-header">
					<h2>{label}</h2>
					<button className="icon-button" type="button" aria-label={`${t('app.global.close', 'Close')} ${label}`} onClick={onClose}>
						×
					</button>
				</div>
				{children}
			</div>
		</div>,
		document.body,
	);
}

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
			<button className="drawer-scrim" type="button" aria-label={`${t('app.global.close', 'Close')} ${label}`} onClick={onClose} />
			<div ref={panelRef} tabIndex={-1} className="modal-panel" role="dialog" aria-modal="true" aria-label={label}>
				<div className="drawer-header">
					<h2>{label}</h2>
					<button className="icon-button" type="button" aria-label={`${t('app.global.close', 'Close')} ${label}`} onClick={onClose}>
						×
					</button>
				</div>
				{children}
			</div>
		</div>,
		document.body,
	);
}
