/**
 * Quick-action registry shared by the command palette and the global keyboard
 * layer: one declarative list whose `shortcut` fields are the single source for
 * both the visible chips and the dispatch, so no binding can drift or go dead.
 * @author Rodrigo Mason
 */

import type { LucideProps } from 'lucide-react';
import {
	ClipboardCheck,
	Code2,
	FileCheck2,
	FolderPlus,
	Network,
	Plus,
	RefreshCw,
	Settings as SettingsIcon,
} from 'lucide-react';
import type { ComponentType } from 'react';
import { useMemo } from 'react';
import type { Project, RuntimeProviders } from '../api/types';
import type { WorkspaceMode } from '../features/workspace/useProjectDiscovery';
import type { PageId } from './navigation';

export type CommandGroupId = 'navigate' | 'actions' | 'runtime';

type IconComponent = ComponentType<LucideProps>;

/** A single quick action surfaced in the command palette. This shape is the
 *  single source of truth for BOTH the palette UI (label, hint, chip, disabled
 *  reason) AND the App-level global keyboard dispatch, so a `shortcut` declared
 *  here is always wired and never a dead chip. */
export interface CommandAction {
	id: string;
	group: CommandGroupId;
	label: string;
	hint: string;
	keywords?: string[];
	icon?: IconComponent;
	/** Modifier-then-key tokens, e.g. ['Ctrl', 'Alt', 'A']. Rendered as chips and matched by matchesShortcut. */
	shortcut?: string[];
	/** Short text shown as a count pill (e.g. pending approvals). */
	badge?: string;
	disabled?: boolean;
	disabledReason?: string;
	run: () => void;
}

const MODIFIER_TOKENS = new Set(['ctrl', 'alt', 'shift', 'cmd', 'meta']);

/** Pure matcher comparing a keyboard event against a shortcut definition.
 *  Falls back to `event.code` for letter keys so AltGr / layout remaps that
 *  mutate `event.key` still resolve to the intended binding. */
export function matchesShortcut(event: KeyboardEvent, shortcut: string[]): boolean {
	const tokens = shortcut.map((token) => token.toLowerCase());
	const wanted = new Set(tokens);
	const key = tokens.find((token) => !MODIFIER_TOKENS.has(token));
	if (!key) return false;
	const wantMeta = wanted.has('cmd') || wanted.has('meta');
	if (
		event.ctrlKey !== wanted.has('ctrl') ||
		event.altKey !== wanted.has('alt') ||
		event.shiftKey !== wanted.has('shift') ||
		event.metaKey !== wantMeta
	) {
		return false;
	}
	if (event.key.toLowerCase() === key) return true;
	return key.length === 1 && /[a-z]/.test(key) && event.code === `Key${key.toUpperCase()}`;
}

export interface CommandActionDeps {
	navigateTo: (page: PageId) => void;
	openWorkspaceDialog: (mode: WorkspaceMode) => void;
	/** Opens the Settings modal at an optional section. */
	openSettings: (section?: string) => void;
	onOpenApprovals: () => void;
	refresh: (silent?: boolean) => Promise<void> | void;
	selectedProject: Project | null;
	runtimeProviders: RuntimeProviders | null;
	pendingReviewCount: number;
	evidenceCount: number;
	connected: boolean;
	close: () => void;
}

/** Builds the live, grouped quick-action set. Disabled flags and reasons are
 *  derived from control-plane state so the palette and the keyboard dispatch
 *  stay in sync. */
export function useCommandActions(deps: CommandActionDeps): CommandAction[] {
	const {
		navigateTo,
		openWorkspaceDialog,
		openSettings,
		onOpenApprovals,
		refresh,
		selectedProject,
		runtimeProviders,
		pendingReviewCount,
		evidenceCount,
		connected,
		close,
	} = deps;

	return useMemo<CommandAction[]>(() => {
		const withClose = (dispatch: () => void) => () => {
			dispatch();
			close();
		};
		return [
			{
				id: 'go-workbench',
				group: 'navigate',
				label: 'Go to Workbench',
				hint: 'Open the workspace and task composer',
				keywords: ['ide', 'editor', 'workspace'],
				icon: Code2,
				run: withClose(() => navigateTo('workbench')),
			},
			{
				id: 'open-evidence',
				group: 'navigate',
				label: 'Open evidence',
				hint: 'Inspect evidence packages and QA results',
				keywords: ['qa', 'artifacts', 'tests', 'verification'],
				icon: FileCheck2,
				shortcut: ['Ctrl', 'Alt', 'V'],
				disabled: evidenceCount === 0,
				disabledReason: 'No evidence yet',
				run: withClose(() => navigateTo('evidence')),
			},
			{
				id: 'open-settings',
				group: 'navigate',
				label: 'Open settings group',
				hint: 'Project, runtime, memory and preference settings',
				keywords: ['preferences', 'configuration', 'options'],
				icon: SettingsIcon,
				run: withClose(() => openSettings()),
			},
			{
				id: 'open-folder',
				group: 'actions',
				label: 'Open folder',
				hint: 'Open a local project folder as a workspace',
				keywords: ['workspace', 'project', 'directory', 'import'],
				icon: FolderPlus,
				shortcut: ['Ctrl', 'Alt', 'O'],
				run: withClose(() => openWorkspaceDialog('open_folder')),
			},
			{
				id: 'new-task',
				group: 'actions',
				label: 'New task',
				hint: 'Compose an issue-to-patch task for the selected project',
				keywords: ['compose', 'run', 'prompt', 'issue', 'patch'],
				icon: Plus,
				disabled: !selectedProject,
				disabledReason: 'Open a project first',
				run: withClose(() => navigateTo('workbench')),
			},
			{
				id: 'review-approvals',
				group: 'actions',
				label: 'Review pending approvals',
				hint: 'Open the per-action approvals awaiting your decision',
				keywords: ['gate', 'permission', 'request', 'approve'],
				icon: ClipboardCheck,
				shortcut: ['Ctrl', 'Alt', 'A'],
				badge: pendingReviewCount > 0 ? String(pendingReviewCount) : undefined,
				disabled: pendingReviewCount === 0,
				disabledReason: 'No pending approvals',
				run: withClose(() => onOpenApprovals()),
			},
			{
				id: 'configure-runtime',
				group: 'runtime',
				label: 'Configure runtime',
				hint: 'Open the Model Gateway to tune providers and policies',
				keywords: ['models', 'providers', 'gateway', 'policy'],
				icon: Network,
				disabled: !runtimeProviders,
				disabledReason: 'Runtime providers not discovered yet',
				run: withClose(() => navigateTo('models')),
			},
			{
				id: 'refresh-runtime',
				group: 'runtime',
				label: 'Refresh runtime health',
				hint: 'Re-fetch control plane state and runtime providers',
				keywords: ['health', 'reload', 'reconnect', 'status'],
				icon: RefreshCw,
				shortcut: ['Ctrl', 'Alt', 'R'],
				disabled: !connected,
				disabledReason: 'Control plane offline',
				run: withClose(() => {
					void refresh(true);
				}),
			},
		];
	}, [
		navigateTo,
		openWorkspaceDialog,
		openSettings,
		onOpenApprovals,
		refresh,
		selectedProject,
		runtimeProviders,
		pendingReviewCount,
		evidenceCount,
		connected,
		close,
	]);
}
