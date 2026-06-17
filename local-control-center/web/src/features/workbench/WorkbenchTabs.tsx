/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useRef } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';

import { Badge } from '../../components/primitives';

export type WorkbenchTabId = 'task' | 'timeline' | 'diff' | 'evidence' | 'logs';
export type WorkbenchTabDef = { id: WorkbenchTabId; label: string; count?: number };

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
			<div className="tabs" role="tablist" aria-label="Workbench views" aria-orientation="horizontal" onKeyDown={onKeyDown}>
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
							{typeof tab.count === 'number' && tab.count > 0 ? <Badge tone={selected ? 'info' : undefined}>{tab.count}</Badge> : null}
						</button>
					);
				})}
			</div>
			<section role="tabpanel" id={`wb-panel-${activeTab}`} aria-labelledby={`wb-tab-${activeTab}`} tabIndex={0} className="workbench-tabpanel">
				{children}
			</section>
		</div>
	);
}
