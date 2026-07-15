/**
 * Tabla genérica dirigida por columnas: renderiza `empty` cuando no hay filas, keyea por
 * `row.id` (fallback índice), expone caption sr-only y celdas con `data-label` para el
 * layout responsivo.
 * @author Rodrigo Mason
 */
import type { ReactNode } from 'react';

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
