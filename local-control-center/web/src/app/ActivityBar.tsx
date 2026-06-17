/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { PanelLeft } from 'lucide-react';

import { useI18n } from '../i18n/I18nProvider';
import { AREAS, pickLabel } from './navigation';
import type { AreaId, PageId } from './navigation';

/**
 * Narrow icon rail (primary navigation): one button per top-level area that
 * routes to the area's lead page and marks the active one, plus a toggle to
 * collapse/expand the ExplorerPanel.
 */
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
	const { t } = useI18n();
	const toggleLabel = explorerCollapsed
		? t('app.activityBar.showExplorer', 'Show explorer')
		: t('app.activityBar.hideExplorer', 'Hide explorer');

	return (
		<nav className="activity-bar" role="navigation" aria-label={t('app.global.primaryNavigation', 'Primary navigation')}>
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
