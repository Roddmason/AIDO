/**
 * IDE shell layout that frames every page with the standard chrome.
 *
 * Lays out the activity rail, explorer, header, content slot, inspector and status
 * bar, and owns the local collapse/open state of the explorer and inspector panels.
 */

import { AnimatePresence } from 'motion/react';
import type { ReactNode } from 'react';
import { useState } from 'react';

import type { Overview, Project, RuntimeProviders } from '../api/types';
import { ActivityBar } from './ActivityBar';
import { ExplorerPanel } from './ExplorerPanel';
import { InspectorPanel } from './InspectorPanel';
import type { AreaId, PageId } from './navigation';
import { StatusBar } from './StatusBar';
import { WorkbenchHeader } from './WorkbenchHeader';

type LanguageOption = { code: string; name: string; nativeName: string; enabled: boolean };

/**
 * Frames the active page with the IDE chrome and renders it through `children`.
 * Explorer collapse and inspector visibility are local UI state; everything else
 * (routing, data, selection) is supplied by the App container via props.
 */
export function AppShell({
	area,
	page,
	language,
	languages,
	t,
	navigateTo,
	onChangeLanguage,
	overview,
	runtimeProviders,
	selectedProject,
	connected,
	onSelectProject,
	onCreateProject,
	onOpenCommandPalette,
	onOpenApprovals,
	onOpenEvents,
	onRefresh,
	headerKicker,
	headerTitle,
	children,
}: {
	area: AreaId;
	page: PageId;
	language: string;
	languages: LanguageOption[];
	t: (key: string, fallback?: string) => string;
	navigateTo: (page: PageId) => void;
	onChangeLanguage: (code: string) => void;
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	selectedProject: Project | null;
	connected: boolean;
	onSelectProject: (projectId: string) => void;
	onCreateProject: () => void;
	onOpenCommandPalette: () => void;
	onOpenApprovals: () => void;
	onOpenEvents: () => void;
	onRefresh: () => void;
	headerKicker: string;
	headerTitle: string;
	children: ReactNode;
}) {
	const [explorerCollapsed, setExplorerCollapsed] = useState(false);
	const [inspectorOpen, setInspectorOpen] = useState(false);

	return (
		<div
			className="app-shell-ide"
			data-explorer={explorerCollapsed ? 'false' : 'true'}
			data-inspector={inspectorOpen ? 'true' : 'false'}
		>
			<span className="console-grid" aria-hidden="true" />

			<ActivityBar
				activeArea={area}
				language={language}
				onNavigate={navigateTo}
				explorerCollapsed={explorerCollapsed}
				onToggleExplorer={() => setExplorerCollapsed((value) => !value)}
			/>

			<AnimatePresence mode="popLayout" initial={false}>
				{explorerCollapsed ? null : (
					<ExplorerPanel
						key="explorer"
						activeArea={area}
						page={page}
						language={language}
						overview={overview}
						selectedProject={selectedProject}
						onNavigate={navigateTo}
						onSelectProject={onSelectProject}
						onCreateProject={onCreateProject}
					/>
				)}
			</AnimatePresence>

			<main className="workbench main-area">
				<WorkbenchHeader
					kicker={headerKicker}
					title={headerTitle}
					language={language}
					languages={languages}
					onChangeLanguage={onChangeLanguage}
					t={t}
					onOpenCommandPalette={onOpenCommandPalette}
					onOpenApprovals={onOpenApprovals}
					onOpenEvents={onOpenEvents}
					onRefresh={onRefresh}
					inspectorOpen={inspectorOpen}
					onToggleInspector={() => setInspectorOpen((value) => !value)}
				/>
				<section className="content-frame" aria-live="polite">
					{children}
				</section>
			</main>

			<AnimatePresence mode="popLayout" initial={false}>
				{inspectorOpen ? (
					<InspectorPanel
						key="inspector"
						overview={overview}
						selectedProject={selectedProject}
						onClose={() => setInspectorOpen(false)}
					/>
				) : null}
			</AnimatePresence>

			<StatusBar
				overview={overview}
				runtimeProviders={runtimeProviders}
				selectedProject={selectedProject}
				connected={connected}
				language={language}
				t={t}
			/>
		</div>
	);
}
