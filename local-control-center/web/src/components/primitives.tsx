import type { ReactNode } from 'react';

export function Badge({ children, tone }: { children: ReactNode; tone?: 'ok' | 'warn' | 'danger' | 'info' }) {
	return (
		<span className="badge" data-tone={tone}>
			{children}
		</span>
	);
}

export function StatusDot({ tone = 'info' }: { tone?: 'ok' | 'warn' | 'danger' | 'info' }) {
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
}: {
	columns: Array<{ key: string; label: string; render: (row: T) => ReactNode }>;
	rows: T[];
	empty: ReactNode;
}) {
	if (!rows.length) return <>{empty}</>;
	return (
		<div className="table-wrap">
			<table className="data-table">
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
						<tr key={index}>
							{columns.map((column) => (
								<td key={column.key}>{column.render(row)}</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
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
	if (!open) return null;
	return (
		<div className="drawer-layer" role="presentation">
			<button className="drawer-scrim" type="button" aria-label={`Close ${label}`} onClick={onClose} />
			<aside className="drawer-panel" role="dialog" aria-modal="true" aria-label={label}>
				<div className="drawer-header">
					<h2>{label}</h2>
					<button className="icon-button" type="button" aria-label={`Close ${label}`} onClick={onClose}>
						×
					</button>
				</div>
				{children}
			</aside>
		</div>
	);
}
