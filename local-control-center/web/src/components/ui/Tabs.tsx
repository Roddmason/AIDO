/**
 * Generic accessible tablist: roving-tabindex keyboard nav (Arrow/Home/End) and the
 * WAI-ARIA tab/tabpanel wiring. The caller owns the active tab id and renders the active
 * panel as children. (Workbench keeps its motion-rich WorkbenchTabs; this is the plain
 * primitive for everything else.)
 */

import type { KeyboardEvent, ReactNode } from 'react';
import { useRef } from 'react';

import { cn } from './cn';

export interface TabItem {
	id: string;
	label: ReactNode;
}

export interface TabsProps {
	tabs: TabItem[];
	activeTab: string;
	onChange: (id: string) => void;
	/** Accessible name for the tablist. */
	label: string;
	/** Content of the active tab's panel. */
	children: ReactNode;
	/** Namespace for the generated tab/panel ids. */
	idBase?: string;
	className?: string;
}

export function Tabs({
	tabs,
	activeTab,
	onChange,
	label,
	children,
	idBase = 'tabs',
	className,
}: TabsProps) {
	const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

	const focusTab = (id: string) => {
		onChange(id);
		tabRefs.current[id]?.focus();
	};

	const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
		const index = tabs.findIndex((tab) => tab.id === activeTab);
		if (index < 0) return;
		if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
			event.preventDefault();
			const direction = event.key === 'ArrowRight' ? 1 : -1;
			focusTab(tabs[(index + direction + tabs.length) % tabs.length].id);
		} else if (event.key === 'Home') {
			event.preventDefault();
			focusTab(tabs[0].id);
		} else if (event.key === 'End') {
			event.preventDefault();
			focusTab(tabs[tabs.length - 1].id);
		}
	};

	return (
		<div className={cn('tabs-root', className)}>
			<div
				className="tabs"
				role="tablist"
				aria-label={label}
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
							id={`${idBase}-tab-${tab.id}`}
							aria-selected={selected}
							aria-controls={`${idBase}-panel-${tab.id}`}
							tabIndex={selected ? 0 : -1}
							onClick={() => onChange(tab.id)}
						>
							{tab.label}
						</button>
					);
				})}
			</div>
			<section
				role="tabpanel"
				id={`${idBase}-panel-${activeTab}`}
				aria-labelledby={`${idBase}-tab-${activeTab}`}
				tabIndex={0}
			>
				{children}
			</section>
		</div>
	);
}
