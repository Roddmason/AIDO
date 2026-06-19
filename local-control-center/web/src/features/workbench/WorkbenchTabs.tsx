/**
 * Tablist + panel chrome for the Workbench center column, with ARIA roles and roving-tabindex
 * keyboard navigation (Arrow/Home/End). The caller owns the active tab and renders panel content.
 */

import type { KeyboardEvent, ReactNode } from 'react';
import { useRef } from 'react';

import { Badge } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';

export type WorkbenchTabId = 'task' | 'timeline' | 'diff' | 'evidence' | 'logs';
/** Descriptor the page passes per tab; `count` renders an optional badge (e.g. pending items). */
type WorkbenchTabDef = { id: WorkbenchTabId; label: string; count?: number };

/** Accessible tablist whose selected panel wraps `children`; selection is fully controlled. */
export function WorkbenchTabs({
	tabs,
	activeTab,
	onChangeTab,
	children,
}: {
	tabs: WorkbenchTabDef[];
	activeTab: WorkbenchTabId;
	onChangeTab: (tab: WorkbenchTabId) => void;
	children: ReactNode;
}) {
	const { t } = useI18n();
	const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

	const focusTab = (tabId: WorkbenchTabId) => {
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
						>
							<span>{tab.label}</span>
							{typeof tab.count === 'number' && tab.count > 0 ? (
								<Badge tone={selected ? 'info' : undefined}>{tab.count}</Badge>
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
				{children}
			</section>
		</div>
	);
}
