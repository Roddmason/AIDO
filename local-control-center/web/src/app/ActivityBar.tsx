/**
 * Primary icon rail of the IDE shell: top-level area switcher and explorer toggle.
 */
import { PanelLeft } from 'lucide-react';
import { m } from 'motion/react';

import { useI18n } from '../i18n/I18nProvider';
import { INDICATOR_TRANSITION } from '../motion/variants';
import type { AreaId, PageId } from './navigation';
import { AREAS, pickLabel } from './navigation';

/** Shared-layout id of the decorative pill that slides to the active area button. */
const ACTIVE_INDICATOR_LAYOUT_ID = 'activitybar-active';

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
		<nav
			className="activity-bar"
			role="navigation"
			aria-label={t('app.global.primaryNavigation', 'Primary navigation')}
		>
			<div className="brand-orb" aria-hidden="true">
				<span />
			</div>
			<div className="activity-bar-nav">
				{AREAS.map((area) => {
					const Icon = area.icon;
					const label = pickLabel(area.label, language);
					const isActive = activeArea === area.id;
					return (
						<button
							key={area.id}
							className="activity-bar-item"
							type="button"
							aria-label={label}
							title={label}
							aria-current={isActive ? 'page' : undefined}
							onClick={() => onNavigate(area.leadPage)}
							style={{ position: 'relative' }}
						>
							{isActive ? (
								<m.span
									layoutId={ACTIVE_INDICATOR_LAYOUT_ID}
									aria-hidden="true"
									transition={INDICATOR_TRANSITION}
									style={{
										position: 'absolute',
										left: 0,
										top: '50%',
										width: 3,
										height: '1.25rem',
										marginTop: '-0.625rem',
										borderRadius: 'var(--radius-pill)',
										background: 'var(--color-accent)',
									}}
								/>
							) : null}
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
