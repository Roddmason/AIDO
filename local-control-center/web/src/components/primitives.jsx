import React, { useId, useState } from 'react';

export function Button({ children, variant = 'primary', type = 'button', ...props }) {
	return (
		<button className="button" data-variant={variant} type={type} {...props}>
			{children}
		</button>
	);
}

export function IconButton({ label, children, variant = 'ghost', ...props }) {
	return (
		<button className="icon-button" data-variant={variant} type="button" aria-label={label} title={label} {...props}>
			{children}
		</button>
	);
}

export function Badge({ children, tone, status }) {
	return (
		<span className="badge" data-tone={tone} data-status={status}>
			{status ? <StatusDot status={status} /> : null}
			{children}
		</span>
	);
}

export function StatusDot({ status = 'queued' }) {
	return <span className="status-dot" data-status={status} aria-hidden="true" />;
}

export function Surface({ title, kicker, actions, children, tone = 'raised', className = '' }) {
	return (
		<section className={`surface ${className}`.trim()} data-tone={tone}>
			{title || kicker || actions ? (
				<header className="surface-header">
					<div className="surface-title">
						{kicker ? <p className="section-kicker">{kicker}</p> : null}
						{title ? <h2>{title}</h2> : null}
					</div>
					{actions ? <div className="command-bar-group">{actions}</div> : null}
				</header>
			) : null}
			<div className="surface-body">{children}</div>
		</section>
	);
}

export function CommandBar({ children, meta }) {
	return (
		<div className="command-bar">
			<div className="command-bar-group">{children}</div>
			{meta ? <div className="command-bar-group">{meta}</div> : null}
		</div>
	);
}

export function DataTable({ columns, rows, empty }) {
	if (!rows.length) {
		return empty || <EmptyState title="No records" body="The backend returned an empty collection for this surface." />;
	}
	return (
		<div className="data-table-wrap">
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
						<tr key={row.id || row.key || index} data-live-row>
							{columns.map((column) => (
								<td key={column.key}>{column.render ? column.render(row) : row[column.key]}</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}

export function Timeline({ items, empty }) {
	if (!items.length) {
		return empty || <EmptyState title="No events" body="Events will appear here as jobs and workers mutate state." />;
	}
	return (
		<div className="timeline">
			{items.map((item) => (
				<div className="timeline-item" key={item.id || `${item.type}-${item.createdAt}`}>
					<span className="timeline-mark">
						<StatusDot status={item.status || item.type || 'queued'} />
					</span>
					<div className="timeline-content">
						<strong>{item.type || item.title}</strong>
						<span className="muted mono">{item.createdAt || item.time}</span>
						{item.body ? <span className="subtle">{item.body}</span> : null}
					</div>
				</div>
			))}
		</div>
	);
}

export function SplitPane({ primary, secondary }) {
	return (
		<div className="split-pane">
			<div>{primary}</div>
			<div>{secondary}</div>
		</div>
	);
}

export function EmptyState({ title, body, action }) {
	return (
		<div className="empty-state">
			<strong>{title}</strong>
			{body ? <p className="muted">{body}</p> : null}
			{action ? <div>{action}</div> : null}
		</div>
	);
}

export function ConfirmAction({ children, confirmLabel = 'Confirm', onConfirm, variant = 'secondary', disabled }) {
	const [confirming, setConfirming] = useState(false);
	const labelId = useId();

	if (!confirming) {
		return (
			<Button variant={variant} disabled={disabled} onClick={() => setConfirming(true)}>
				{children}
			</Button>
		);
	}

	return (
		<span className="confirm-inline" role="group" aria-labelledby={labelId}>
			<span id={labelId} className="muted">
				{confirmLabel}?
			</span>
			<Button
				variant={variant}
				disabled={disabled}
				onClick={async () => {
					await onConfirm();
					setConfirming(false);
				}}
			>
				Yes
			</Button>
			<Button variant="ghost" onClick={() => setConfirming(false)}>
				No
			</Button>
		</span>
	);
}
