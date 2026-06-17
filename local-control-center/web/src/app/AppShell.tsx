/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useState } from 'react';
import type { ReactNode, Ref } from 'react';

import type { Overview, Project, RuntimeProviders } from '../api/types';
import { ActivityBar } from './ActivityBar';
import { ExplorerPanel } from './ExplorerPanel';
import { InspectorPanel } from './InspectorPanel';
import { StatusBar } from './StatusBar';
import { WorkbenchHeader } from './WorkbenchHeader';
import type { AreaId, PageId } from './navigation';

type LanguageOption = { code: string; name: string; nativeName: string; enabled: boolean };

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
	contentRef,
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
	contentRef: Ref<HTMLElement>;
	children: ReactNode;
}) {
	const [explorerCollapsed, setExplorerCollapsed] = useState(false);
	const [inspectorOpen, setInspectorOpen] = useState(false);

	return (
		<div className="app-shell-ide" data-explorer={explorerCollapsed ? 'false' : 'true'} data-inspector={inspectorOpen ? 'true' : 'false'}>
			<span className="console-grid" aria-hidden="true" />

			<ActivityBar
				activeArea={area}
				language={language}
				onNavigate={navigateTo}
				explorerCollapsed={explorerCollapsed}
				onToggleExplorer={() => setExplorerCollapsed((value) => !value)}
			/>

			{explorerCollapsed ? null : (
				<ExplorerPanel
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
				<section ref={contentRef} className="content-frame motion-scope" aria-live="polite">
					{children}
				</section>
			</main>

			{inspectorOpen ? (
				<InspectorPanel
					overview={overview}
					selectedProject={selectedProject}
					onClose={() => setInspectorOpen(false)}
				/>
			) : null}

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
