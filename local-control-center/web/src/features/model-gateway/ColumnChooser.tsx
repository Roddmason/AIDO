/**
 * Column chooser for the Model Gateway tables: keeps the primary view narrow (a handful of
 * columns) while letting operators reveal advanced columns on demand. The chosen set persists
 * per table in localStorage (best-effort, like ExplorerPanel) so a workspace keeps its layout.
 * Advanced columns default to hidden.
 * @author Rodrigo Mason
 */
import { useCallback, useState } from 'react';

import { useI18n } from '../../i18n/I18nProvider';

export type AdvancedColumn = { key: string; label: string };

function storageKey(tableId: string) {
	return `aido:model-gateway:cols:${tableId}`;
}

function readVisible(tableId: string): Set<string> {
	try {
		const raw = window.localStorage.getItem(storageKey(tableId));
		const parsed = raw ? JSON.parse(raw) : null;
		return Array.isArray(parsed) ? new Set(parsed.map(String)) : new Set();
	} catch {
		return new Set();
	}
}

function persistVisible(tableId: string, visible: Set<string>) {
	try {
		window.localStorage.setItem(storageKey(tableId), JSON.stringify([...visible]));
	} catch {}
}

/** Owns which advanced columns are revealed for one table (persisted, advanced-hidden by default). */
export function useColumnVisibility(tableId: string) {
	const [visible, setVisible] = useState<Set<string>>(() => readVisible(tableId));
	const toggle = useCallback(
		(key: string) => {
			setVisible((current) => {
				const next = new Set(current);
				if (next.has(key)) next.delete(key);
				else next.add(key);
				persistVisible(tableId, next);
				return next;
			});
		},
		[tableId],
	);
	return { visible, toggle };
}

/**
 * A small disclosure listing the advanced columns as checkboxes. Checked columns are appended to
 * the table's primary columns by the caller; the primary set is always visible.
 */
export function ColumnChooser({
	advanced,
	visible,
	onToggle,
}: {
	advanced: AdvancedColumn[];
	visible: Set<string>;
	onToggle: (key: string) => void;
}) {
	const { t } = useI18n();
	if (!advanced.length) return null;
	return (
		<details className="column-chooser">
			<summary>{t('app.modelGateway.columns.chooser', 'Columns')}</summary>
			<fieldset
				className="column-chooser-options"
				aria-label={t('app.modelGateway.columns.chooser', 'Columns')}
				// Reset the <fieldset> user-agent chrome so the flex box matches the prior <div>.
				style={{ margin: 0, padding: 0, border: 0, minInlineSize: 0 }}
			>
				{advanced.map((column) => {
					const id = `col-${column.key}`;
					return (
						<label className="checkbox-row" htmlFor={id} key={column.key}>
							<input
								id={id}
								type="checkbox"
								checked={visible.has(column.key)}
								onChange={() => onToggle(column.key)}
							/>
							{column.label}
						</label>
					);
				})}
			</fieldset>
		</details>
	);
}
