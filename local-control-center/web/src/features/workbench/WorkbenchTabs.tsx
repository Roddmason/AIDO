/**
 * Tablist + panel chrome for the Workbench center column, with ARIA roles and roving-tabindex
 * keyboard navigation (Arrow/Home/End). The caller owns the active tab and renders panel content.
 * @author Rodrigo Mason
 */

import { AnimatePresence, m } from 'motion/react';
import type { CSSProperties, KeyboardEvent, ReactNode } from 'react';
import { useRef } from 'react';

import { Badge } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { crossfade } from '../../motion/variants';

/**
 * Subrayado deslizante compartido entre tabs: el `layoutId` hace que Motion lo anime
 * de un tab al otro al cambiar la selección. El anclaje `relative` del botón vive inline
 * para no tocar el CSS del design-system. Bajo reduced-motion el desplazamiento cae a no-op.
 */
const TAB_INDICATOR_STYLE: CSSProperties = {
	position: 'absolute',
	left: 'var(--space-2)',
	right: 'var(--space-2)',
	bottom: '2px',
	height: '2px',
	borderRadius: 'var(--radius-full)',
	background: 'var(--color-text-primary)',
};
const TAB_INDICATOR_TRANSITION = { type: 'spring', stiffness: 480, damping: 38 } as const;
const TAB_ANCHOR_STYLE: CSSProperties = { position: 'relative' };

/** Descriptor the page passes per tab; `count` renders an optional badge (e.g. pending items). */
export type WorkbenchTabDef<Id extends string = string> = { id: Id; label: string; count?: number };

/** Accessible tablist whose selected panel wraps `children`; selection is fully controlled.
 *  Generic over the tab id so the same chrome hosts the workbench review tabs and the product-loop
 *  sections without losing exhaustiveness at the call site. */
export function WorkbenchTabs<Id extends string = string>({
	tabs,
	activeTab,
	onChangeTab,
	children,
}: {
	tabs: WorkbenchTabDef<Id>[];
	activeTab: Id;
	onChangeTab: (tab: Id) => void;
	children: ReactNode;
}) {
	const { t } = useI18n();
	const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

	const focusTab = (tabId: Id) => {
		onChangeTab(tabId);
		tabRefs.current[tabId]?.focus();
	};

	const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
		const index = tabs.findIndex((tab) => tab.id === activeTab);
		if (index < 0) return;
		if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
			event.preventDefault();
			const direction = event.key === 'ArrowRight' ? 1 : -1;
			const next = (index + direction + tabs.length) % tabs.length;
			focusTab(tabs[next].id);
		} else if (event.key === 'Home') {
			event.preventDefault();
			focusTab(tabs[0].id);
		} else if (event.key === 'End') {
			event.preventDefault();
			focusTab(tabs[tabs.length - 1].id);
		}
	};

	return (
		<div className="workbench-tabs">
			<div
				className="tabs"
				role="tablist"
				aria-label={t('app.workbench.tabs.aria', 'Workbench views')}
				aria-orientation="horizontal"
				onKeyDown={onKeyDown}
			>
				{tabs.map((tab) => {
					const selected = tab.id === activeTab;
					return (
						<button
							key={tab.id}
							ref={(node) => {
								tabRefs.current[tab.id] = node;
							}}
							className="button"
							type="button"
							role="tab"
							id={`wb-tab-${tab.id}`}
							aria-selected={selected}
							aria-controls={`wb-panel-${tab.id}`}
							tabIndex={selected ? 0 : -1}
							onClick={() => onChangeTab(tab.id)}
							style={TAB_ANCHOR_STYLE}
						>
							<span>{tab.label}</span>
							{typeof tab.count === 'number' && tab.count > 0 ? (
								<Badge tone={selected ? 'info' : undefined}>{tab.count}</Badge>
							) : null}
							{selected ? (
								<m.span
									layoutId="workbench-tab-indicator"
									aria-hidden="true"
									style={TAB_INDICATOR_STYLE}
									transition={TAB_INDICATOR_TRANSITION}
								/>
							) : null}
						</button>
					);
				})}
			</div>
			<section
				role="tabpanel"
				id={`wb-panel-${activeTab}`}
				aria-labelledby={`wb-tab-${activeTab}`}
				tabIndex={0}
				className="workbench-tabpanel"
			>
				<AnimatePresence mode="popLayout">
					<m.div
						key={activeTab}
						variants={crossfade}
						initial="initial"
						animate="animate"
						exit="exit"
					>
						{children}
					</m.div>
				</AnimatePresence>
			</section>
		</div>
	);
}
