/**
 * Keyboard-accessible controls to reorder the workbench columns. The toolbar keeps a fixed order
 * (and uses aria-disabled at the edges) so focus stays on the pressed button while the columns move.
 * @author AIDO contributors
 */
import { ArrowLeft, ArrowRight, Columns3 } from 'lucide-react';
import { useState } from 'react';
import { useI18n } from '../../i18n/I18nProvider';
import { DEFAULT_COLUMN_ORDER, type WorkbenchColumnId } from './useColumnOrder';

export function WorkbenchColumnControls({
	order,
	onMove,
}: {
	order: readonly WorkbenchColumnId[];
	onMove: (id: WorkbenchColumnId, direction: -1 | 1) => number | null;
}) {
	const { t } = useI18n();
	const [announcement, setAnnouncement] = useState('');
	const names: Record<WorkbenchColumnId, string> = {
		explorer: t('app.workbench.columns.explorer', 'Explorer'),
		conversation: t('app.workbench.columns.conversation', 'Conversation'),
		status: t('app.workbench.columns.status', 'Runtime status'),
	};

	const move = (id: WorkbenchColumnId, direction: -1 | 1) => {
		// aria-disabled buttons still fire: at an edge, announce the unchanged position instead of moving.
		const position = onMove(id, direction) ?? order.indexOf(id) + 1;
		setAnnouncement(
			`${names[id]}: ${t('app.workbench.columns.position', 'position')} ${position}/${order.length}`,
		);
	};

	// Collapsed by default so the reorder controls add no visual noise or tab stops until requested.
	return (
		<details className="workbench-column-order">
			<summary className="button">
				<Columns3 aria-hidden="true" size={14} /> {t('app.workbench.columns.group', 'Column order')}
			</summary>
			<div
				className="inline"
				role="toolbar"
				aria-label={t('app.workbench.columns.group', 'Column order')}
			>
				{DEFAULT_COLUMN_ORDER.map((id) => {
					const index = order.indexOf(id);
					return (
						<span key={id} className="inline">
							<span className="muted" aria-hidden="true">
								{names[id]}
							</span>
							<button
								className="button"
								type="button"
								aria-label={`${t('app.workbench.columns.moveLeft', 'Move left')}: ${names[id]}`}
								aria-disabled={index === 0}
								onClick={() => move(id, -1)}
							>
								<ArrowLeft aria-hidden="true" size={14} />
							</button>
							<button
								className="button"
								type="button"
								aria-label={`${t('app.workbench.columns.moveRight', 'Move right')}: ${names[id]}`}
								aria-disabled={index === order.length - 1}
								onClick={() => move(id, 1)}
							>
								<ArrowRight aria-hidden="true" size={14} />
							</button>
						</span>
					);
				})}
			</div>
			<span className="sr-only" role="status" aria-live="polite">
				{announcement}
			</span>
		</details>
	);
}
