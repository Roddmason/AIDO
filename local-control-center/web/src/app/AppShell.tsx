/**
 * IDE shell layout that frames every page with the standard chrome.
 *
 * On desktop the explorer, workbench, optional bottom dock and inspector are laid
 * out as resizable/collapsible panes (react-resizable-panels) whose sizes persist
 * in versioned localStorage; below the breakpoint the same chrome falls back to a
 * stacked single-column layout that flows naturally on narrow/touch screens.
 *
 * Owns the panes' collapse state, the activity-rail / header toggles and the
 * Ctrl/Cmd+B (explorer), Ctrl/Cmd+Shift+B (inspector) and Ctrl/Cmd+J (bottom dock)
 * shortcuts. Everything else (routing, data, selection) arrives via props.
 */

import type { ReactNode } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { LayoutStorage } from 'react-resizable-panels';
import { Group, Panel, Separator, useDefaultLayout, usePanelRef } from 'react-resizable-panels';

import type { Overview, Project, RuntimeProviders } from '../api/types';
import { ShellSidebar } from '../features/shell/ShellSidebar';
import type { WorkspaceMode } from '../features/workspace/useProjectDiscovery';
import { useIsDesktopLayout } from '../hooks/useIsDesktopLayout';
import { BottomPanel } from './BottomPanel';
import { InspectorPanel } from './InspectorPanel';
import { MenuBar } from './MenuBar';
import type { AreaId, PageId } from './navigation';
import type { Mutate } from './routes';
import { StatusBar } from './StatusBar';

type LanguageOption = { code: string; name: string; nativeName: string; enabled: boolean };

// Defensive localStorage adapter: persistence is best-effort and must never throw
// in restricted browser contexts (private mode, disabled storage).
const LAYOUT_STORAGE: LayoutStorage = {
	getItem: (key) => {
		try {
			return window.localStorage.getItem(key);
		} catch {
			return null;
		}
	},
	setItem: (key, value) => {
		try {
			window.localStorage.setItem(key, value);
		} catch {
			// localStorage is optional in restricted browser contexts.
		}
	},
};

// Versioned layout keys: bump the suffix when the pane structure changes so a stale
// saved layout can never reference panels that no longer exist.
const HORIZONTAL_LAYOUT_ID = 'aido:ide-shell:v1';
const VERTICAL_LAYOUT_ID = 'aido:ide-center:v1';

// A collapsible pane reports ~0% of its group while it sits at collapsedSize (0).
const COLLAPSED_PERCENTAGE = 0.5;

// Drag/hit target around each separator: larger for touch (coarse) than mouse (fine).
const RESIZE_HIT_TARGET = { coarse: 24, fine: 10 } as const;

/**
 * Frames the active page with the IDE chrome and renders it through `children`.
 * Pane sizes and collapse are local UI state (persisted for the resizable layout);
 * routing, data and selection are supplied by the App container via props.
 */
export function AppShell({
	area,
	language,
	languages,
	t,
	navigateTo,
	onChangeLanguage,
	overview,
	runtimeProviders,
	selectedProject,
	selectedRunId,
	token,
	mutate,
	onClearRun,
	selectedSessionId,
	onSelectSession,
	connected,
	onSelectProject,
	onCreateProject,
	onOpenWorkspaceDialog,
	onOpenCommandPalette,
	onOpenApprovals,
	onOpenEvents,
	onRefresh,
	children,
}: {
	area: AreaId;
	language: string;
	languages: LanguageOption[];
	t: (key: string, fallback?: string) => string;
	navigateTo: (page: PageId, runId?: string) => void;
	onChangeLanguage: (code: string) => void;
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	selectedProject: Project | null;
	/** The deep-linked workflow run shown in the Inspector, or null for the project summary. */
	selectedRunId: string | null;
	token: string;
	mutate: Mutate;
	onClearRun: () => void;
	/** Shell-owned active session selection, threaded to the ShellSidebar for the `threads` area. */
	selectedSessionId: string;
	onSelectSession: (sessionId: string) => void;
	connected: boolean;
	onSelectProject: (projectId: string) => void;
	onCreateProject: () => void;
	onOpenWorkspaceDialog: (mode: WorkspaceMode) => void;
	onOpenCommandPalette: () => void;
	onOpenApprovals: () => void;
	onOpenEvents: () => void;
	onRefresh: () => void;
	children: ReactNode;
}) {
	const isDesktop = useIsDesktopLayout();

	const explorerPanelRef = usePanelRef();
	const inspectorPanelRef = usePanelRef();
	const bottomPanelRef = usePanelRef();
	// Toggle controls live outside the panes; focusing them on collapse keeps
	// keyboard focus off a pane that has just shrunk to zero width/height.
	const explorerToggleRef = useRef<HTMLButtonElement>(null);
	const inspectorToggleRef = useRef<HTMLButtonElement>(null);
	const bottomToggleRef = useRef<HTMLButtonElement>(null);

	const [explorerCollapsed, setExplorerCollapsed] = useState(false);
	const [inspectorCollapsed, setInspectorCollapsed] = useState(true);
	const [bottomCollapsed, setBottomCollapsed] = useState(true);

	const horizontalLayout = useDefaultLayout({
		id: HORIZONTAL_LAYOUT_ID,
		storage: LAYOUT_STORAGE,
		panelIds: ['explorer', 'center', 'inspector'],
	});
	const verticalLayout = useDefaultLayout({
		id: VERTICAL_LAYOUT_ID,
		storage: LAYOUT_STORAGE,
		panelIds: ['workbench', 'bottom'],
	});

	const toggleExplorer = useCallback(() => {
		const handle = explorerPanelRef.current;
		if (handle) {
			if (handle.isCollapsed()) handle.expand();
			else handle.collapse();
		} else {
			setExplorerCollapsed((collapsed) => !collapsed);
		}
		explorerToggleRef.current?.focus();
	}, [explorerPanelRef]);

	const toggleInspector = useCallback(() => {
		const handle = inspectorPanelRef.current;
		if (handle) {
			if (handle.isCollapsed()) handle.expand();
			else handle.collapse();
		} else {
			setInspectorCollapsed((collapsed) => !collapsed);
		}
		inspectorToggleRef.current?.focus();
	}, [inspectorPanelRef]);

	// Expand-only reveal for the Inspector: unlike toggleInspector it never collapses and never
	// moves focus, so revealing the run detail can't fight the resize-driven collapse state or
	// steal focus from the deep link / Explorer action that triggered it.
	const expandInspector = useCallback(() => {
		const handle = inspectorPanelRef.current;
		if (handle) {
			if (handle.isCollapsed()) handle.expand();
		} else {
			setInspectorCollapsed(false);
		}
	}, [inspectorPanelRef]);

	// Reveal the Inspector whenever a run becomes selected — on a deep-link load and on every
	// Explorer "open run". Keyed on selectedRunId only, so a manual collapse stays collapsed
	// until the next selection change.
	useEffect(() => {
		if (selectedRunId) expandInspector();
	}, [selectedRunId, expandInspector]);

	const toggleBottom = useCallback(() => {
		const handle = bottomPanelRef.current;
		if (handle) {
			if (handle.isCollapsed()) handle.expand();
			else handle.collapse();
		} else {
			setBottomCollapsed((collapsed) => !collapsed);
		}
		bottomToggleRef.current?.focus();
	}, [bottomPanelRef]);

	useEffect(() => {
		const onKeyDown = (event: KeyboardEvent) => {
			if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
			const target = event.target as HTMLElement | null;
			if (
				target?.isContentEditable ||
				target?.tagName === 'INPUT' ||
				target?.tagName === 'TEXTAREA' ||
				target?.tagName === 'SELECT'
			) {
				return;
			}
			if (event.code === 'KeyB') {
				event.preventDefault();
				if (event.shiftKey) toggleInspector();
				else toggleExplorer();
			} else if (event.code === 'KeyJ' && !event.shiftKey) {
				event.preventDefault();
				toggleBottom();
			}
		};
		window.addEventListener('keydown', onKeyDown);
		return () => window.removeEventListener('keydown', onKeyDown);
	}, [toggleExplorer, toggleInspector, toggleBottom]);

	// One persistent Projects sidebar everywhere (Codex-style): the same workspace → thread navigator on
	// every route, toggled only by Ctrl/Cmd+B — it never swaps per area. Selecting a thread (or starting
	// a new one) navigates to the thread/loop shell so the chat opens in the center.
	const explorer = (
		<ShellSidebar
			overview={overview}
			selectedProjectId={selectedProject?.id ?? ''}
			selectedSessionId={selectedSessionId}
			onSelectProject={onSelectProject}
			onSelectSession={(sessionId) => {
				onSelectSession(sessionId);
				navigateTo('threads');
			}}
			onCreateProject={onCreateProject}
			navigateTo={navigateTo}
		/>
	);

	const workbench = (
		<main className="workbench main-area">
			<section className="content-frame" aria-live="polite">
				{children}
			</section>
		</main>
	);

	const inspector = (
		<InspectorPanel
			overview={overview}
			selectedProject={selectedProject}
			selectedRunId={selectedRunId}
			token={token}
			mutate={mutate}
			onClose={toggleInspector}
			onClearRun={onClearRun}
			showLoops={area === 'threads'}
		/>
	);

	const bottomDock = <BottomPanel onClose={toggleBottom} />;

	const statusBar = (
		<StatusBar
			overview={overview}
			runtimeProviders={runtimeProviders}
			selectedProject={selectedProject}
			connected={connected}
			language={language}
			languages={languages}
			onChangeLanguage={onChangeLanguage}
			t={t}
		/>
	);

	const menuBar = (
		<MenuBar
			navigateTo={navigateTo}
			onOpenWorkspaceDialog={onOpenWorkspaceDialog}
			onOpenCommandPalette={onOpenCommandPalette}
			onOpenApprovals={onOpenApprovals}
			onOpenEvents={onOpenEvents}
			onRefresh={onRefresh}
			onToggleExplorer={toggleExplorer}
			onToggleInspector={toggleInspector}
			onToggleBottom={toggleBottom}
		/>
	);

	if (!isDesktop) {
		return (
			<div className="app-shell-ide app-shell-ide--stacked">
				{menuBar}
				{explorerCollapsed ? null : explorer}
				{workbench}
				{bottomCollapsed ? null : bottomDock}
				{inspectorCollapsed ? null : inspector}
				{statusBar}
			</div>
		);
	}

	return (
		<div className="app-shell-ide">
			{menuBar}
			<div className="ide-body">
				<Group
					id={HORIZONTAL_LAYOUT_ID}
					className="ide-panes"
					orientation="horizontal"
					defaultLayout={horizontalLayout.defaultLayout}
					onLayoutChanged={horizontalLayout.onLayoutChanged}
					resizeTargetMinimumSize={RESIZE_HIT_TARGET}
				>
					<Panel
						id="explorer"
						className={explorerCollapsed ? 'ide-pane is-collapsed' : 'ide-pane'}
						collapsible
						collapsedSize={0}
						minSize="14rem"
						maxSize="30rem"
						defaultSize="18rem"
						panelRef={explorerPanelRef}
						onResize={(size) => setExplorerCollapsed(size.asPercentage <= COLLAPSED_PERCENTAGE)}
					>
						{explorer}
					</Panel>
					<Separator
						className="ide-separator"
						aria-label={t('app.shell.resizeExplorer', 'Resize explorer panel')}
					/>
					<Panel id="center" className="ide-pane" minSize="24rem">
						<Group
							id={VERTICAL_LAYOUT_ID}
							className="ide-center"
							orientation="vertical"
							defaultLayout={verticalLayout.defaultLayout}
							onLayoutChanged={verticalLayout.onLayoutChanged}
							resizeTargetMinimumSize={RESIZE_HIT_TARGET}
						>
							<Panel id="workbench" className="ide-pane" minSize="10rem">
								{workbench}
							</Panel>
							<Separator
								className="ide-separator ide-separator--horizontal"
								aria-label={t('app.shell.resizeBottomPanel', 'Resize bottom panel')}
							/>
							<Panel
								id="bottom"
								className={bottomCollapsed ? 'ide-pane is-collapsed' : 'ide-pane'}
								collapsible
								collapsedSize={0}
								minSize="6rem"
								maxSize="70%"
								defaultSize={0}
								panelRef={bottomPanelRef}
								onResize={(size) => setBottomCollapsed(size.asPercentage <= COLLAPSED_PERCENTAGE)}
							>
								{bottomDock}
							</Panel>
						</Group>
					</Panel>
					<Separator
						className="ide-separator"
						aria-label={t('app.shell.resizeInspector', 'Resize inspector panel')}
					/>
					<Panel
						id="inspector"
						className={inspectorCollapsed ? 'ide-pane is-collapsed' : 'ide-pane'}
						collapsible
						collapsedSize={0}
						minSize="15rem"
						maxSize="32rem"
						defaultSize={0}
						panelRef={inspectorPanelRef}
						onResize={(size) => setInspectorCollapsed(size.asPercentage <= COLLAPSED_PERCENTAGE)}
					>
						{inspector}
					</Panel>
				</Group>
			</div>
			{statusBar}
		</div>
	);
}
