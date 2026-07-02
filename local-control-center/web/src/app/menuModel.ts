/**
 * Declarative model for the desktop-style top menu bar: the File/Edit/View/Help
 * menus and their items as data (bilingual catalog key + English fallback +
 * optional shortcut hint), so MenuBar renders and dispatches from a single
 * source — mirroring navigation.ts and productLoopModel.ts. Pure: holds no React
 * and no callbacks; MenuBar maps each item's `command` to a handler.
 * @author Rodrigo Mason
 */

import type { PageId } from './navigation';

/** Every action a menu item can dispatch. MenuBar owns the command → handler map. */
export type MenuCommandId =
	| 'open-folder'
	| 'new-workspace'
	| 'new-task'
	| 'refresh'
	| 'command-palette'
	| 'approvals'
	| 'events'
	| 'toggle-theme'
	| 'toggle-density'
	| 'set-lang-en'
	| 'set-lang-es'
	| 'toggle-explorer'
	| 'toggle-inspector'
	| 'toggle-bottom'
	| 'go-threads'
	| 'go-home'
	| 'go-workbench'
	| 'go-review'
	| 'go-memory'
	| 'go-settings'
	| 'keyboard-shortcuts'
	| 'about';

/** The five top-level menus. */
export type MenuId = 'file' | 'edit' | 'view' | 'go' | 'help';

/** A menu entry: an actionable command or a non-interactive separator rule. */
export type MenuItem =
	| {
			kind: 'command';
			command: MenuCommandId;
			/** i18n key + English fallback (registered EN+ES in the bilingual catalog). */
			labelKey: string;
			label: string;
			/** Display-only shortcut hint (e.g. 'Ctrl K'); the binding itself lives in useShellShortcuts. */
			shortcut?: string;
			/** Target page for navigation commands — lets MenuBar warm the route chunk on hover. */
			page?: PageId;
	  }
	| { kind: 'separator' };

/** One top-level menu: its title plus the ordered items in its dropdown. */
export type MenuDef = {
	id: MenuId;
	labelKey: string;
	label: string;
	items: MenuItem[];
};

const separator: MenuItem = { kind: 'separator' };

/** The menu bar, in display order. Shortcut hints mirror the real bindings declared
 *  in useShellShortcuts / commandActions / AppShell so the hint can never go stale. */
export const MENUS: MenuDef[] = [
	{
		id: 'file',
		labelKey: 'app.menu.file',
		label: 'File',
		items: [
			{
				kind: 'command',
				command: 'open-folder',
				labelKey: 'app.menu.openFolder',
				label: 'Open folder…',
				shortcut: 'Ctrl Alt O',
			},
			{
				kind: 'command',
				command: 'new-workspace',
				labelKey: 'app.menu.newWorkspace',
				label: 'New workspace…',
			},
			{ kind: 'command', command: 'new-task', labelKey: 'app.menu.newTask', label: 'New task' },
			separator,
			{
				kind: 'command',
				command: 'refresh',
				labelKey: 'app.menu.refresh',
				label: 'Refresh',
				shortcut: 'Ctrl Alt R',
			},
		],
	},
	{
		id: 'edit',
		labelKey: 'app.menu.edit',
		label: 'Edit',
		items: [
			{
				kind: 'command',
				command: 'command-palette',
				labelKey: 'app.menu.commandPalette',
				label: 'Command palette…',
				shortcut: 'Ctrl K',
			},
			separator,
			{
				kind: 'command',
				command: 'approvals',
				labelKey: 'app.menu.approvals',
				label: 'Approvals',
				shortcut: 'Ctrl Alt A',
			},
			{ kind: 'command', command: 'events', labelKey: 'app.menu.events', label: 'Events' },
		],
	},
	{
		id: 'view',
		labelKey: 'app.menu.view',
		label: 'View',
		items: [
			{
				kind: 'command',
				command: 'toggle-theme',
				labelKey: 'app.menu.toggleTheme',
				label: 'Toggle theme',
			},
			{
				kind: 'command',
				command: 'toggle-density',
				labelKey: 'app.menu.toggleDensity',
				label: 'Compact density',
			},
			separator,
			{
				kind: 'command',
				command: 'toggle-explorer',
				labelKey: 'app.menu.toggleExplorer',
				label: 'Explorer',
				shortcut: 'Ctrl B',
			},
			{
				kind: 'command',
				command: 'toggle-inspector',
				labelKey: 'app.menu.toggleInspector',
				label: 'Inspector',
				shortcut: 'Ctrl Shift B',
			},
			{
				kind: 'command',
				command: 'toggle-bottom',
				labelKey: 'app.menu.toggleBottom',
				label: 'Bottom panel',
				shortcut: 'Ctrl J',
			},
			separator,
			{
				kind: 'command',
				command: 'set-lang-en',
				labelKey: 'app.menu.langEn',
				label: 'English',
			},
			{
				kind: 'command',
				command: 'set-lang-es',
				labelKey: 'app.menu.langEs',
				label: 'Spanish',
			},
		],
	},
	{
		id: 'go',
		labelKey: 'app.menu.go',
		label: 'Go',
		items: [
			{
				kind: 'command',
				command: 'go-threads',
				labelKey: 'app.menu.goThreads',
				label: 'Threads',
				page: 'threads',
			},
			{
				kind: 'command',
				command: 'go-home',
				labelKey: 'app.menu.goHome',
				label: 'Home',
				page: 'home',
			},
			{
				kind: 'command',
				command: 'go-workbench',
				labelKey: 'app.menu.goWorkbench',
				label: 'Workbench',
				page: 'workbench',
			},
			separator,
			{
				kind: 'command',
				command: 'go-review',
				labelKey: 'app.menu.goReview',
				label: 'Review board',
				page: 'review-board',
			},
			{
				kind: 'command',
				command: 'go-memory',
				labelKey: 'app.menu.goMemory',
				label: 'Memory',
				page: 'memory',
			},
			separator,
			{
				kind: 'command',
				command: 'go-settings',
				labelKey: 'app.menu.goSettings',
				label: 'Settings',
			},
		],
	},
	{
		id: 'help',
		labelKey: 'app.menu.help',
		label: 'Help',
		items: [
			{
				kind: 'command',
				command: 'keyboard-shortcuts',
				labelKey: 'app.menu.keyboardShortcuts',
				label: 'Keyboard shortcuts',
			},
			{ kind: 'command', command: 'about', labelKey: 'app.menu.about', label: 'About AIDO' },
		],
	},
];
