/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { PanelLeft } from 'lucide-react';

import { AREAS, pickLabel } from './navigation';
import type { AreaId, PageId } from './navigation';

export function ActivityBar({
	activeArea,
	language,
	onNavigate,
	explorerCollapsed,
	onToggleExplorer,
}: {
	activeArea: AreaId;
	language: string;
	onNavigate: (page: PageId) => void;
	explorerCollapsed: boolean;
	onToggleExplorer: () => void;
}) {
	const toggleLabel = explorerCollapsed
		? (language === 'es' ? 'Mostrar explorador' : 'Show explorer')
		: (language === 'es' ? 'Ocultar explorador' : 'Hide explorer');

	return (
		<nav className="activity-bar" role="navigation" aria-label="Primary navigation">
			<div className="brand-orb" aria-hidden="true"><span /></div>
			<div className="activity-bar-nav">
				{AREAS.map((area) => {
					const Icon = area.icon;
					const label = pickLabel(area.label, language);
					return (
						<button
							key={area.id}
							className="activity-bar-item"
							type="button"
							aria-label={label}
							title={label}
							aria-current={activeArea === area.id ? 'page' : undefined}
							onClick={() => onNavigate(area.leadPage)}
						>
							<Icon aria-hidden="true" size={20} />
						</button>
					);
				})}
			</div>
			<button
				className="activity-bar-item"
				type="button"
				aria-label={toggleLabel}
				title={toggleLabel}
				aria-pressed={!explorerCollapsed}
				onClick={onToggleExplorer}
			>
				<PanelLeft aria-hidden="true" size={20} />
			</button>
		</nav>
	);
}
