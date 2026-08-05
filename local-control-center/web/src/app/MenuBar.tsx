/**
 * Desktop-style application menu bar (File / Edit / View / Help).
 *
 * Renders the declarative {@link MENUS} model as an accessible WAI-ARIA menubar:
 * each top menu opens a dropdown of commands that dispatch to the shell. Keyboard
 * model — Left/Right move between menus, Down/Enter/Space open, Up/Down move within
 * items, Home/End jump, Esc closes and restores focus to the menu button, and a
 * pointer-down outside closes. Theme, density and About are handled here via hooks;
 * every other command is delegated upward via props. Stateless beyond its own
 * open-menu/focus chrome.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useToast } from '../components/ui';
import type { WorkspaceMode } from '../features/workspace/useProjectDiscovery';
import { useDensity } from '../hooks/useDensity';
import { useTheme } from '../hooks/useTheme';
import { useI18n } from '../i18n/I18nProvider';
import { MENUS, type MenuCommandId, type MenuDef, type MenuId } from './menuModel';
import type { PageId } from './navigation';
import { preloadRoute } from './routes';

type MenuBarProps = {
	navigateTo: (page: PageId) => void;
	onOpenWorkspaceDialog: (mode: WorkspaceMode) => void;
	onOpenCommandPalette: () => void;
	onOpenApprovals: () => void;
	onOpenEvents: () => void;
	onRefresh: () => void;
	onToggleExplorer: () => void;
	onToggleInspector: () => void;
	onToggleBottom: () => void;
	/** Opens the Settings modal at an optional section. */
	onOpenSettings?: (section?: string) => void;
};

const MENU_ORDER: MenuId[] = MENUS.map((menu) => menu.id);

/** Renders the top menu bar and owns only its open-menu and keyboard-focus state. */
export function MenuBar({
	navigateTo,
	onOpenWorkspaceDialog,
	onOpenCommandPalette,
	onOpenApprovals,
	onOpenEvents,
	onRefresh,
	onToggleExplorer,
	onToggleInspector,
	onToggleBottom,
	onOpenSettings,
}: MenuBarProps) {
	const { t, setLanguage } = useI18n();
	const { toggleTheme } = useTheme();
	const { toggleDensity } = useDensity();
	const { notify } = useToast();
	const [openMenu, setOpenMenu] = useState<MenuId | null>(null);
	const containerRef = useRef<HTMLDivElement>(null);
	const buttonRefs = useRef(new Map<MenuId, HTMLButtonElement | null>());
	const menuRef = useRef<HTMLDivElement>(null);
	const focusEdgeRef = useRef<'first' | 'last'>('first');

	const handlers = useMemo<Record<MenuCommandId, () => void>>(
		() => ({
			'open-folder': () => onOpenWorkspaceDialog('open_folder'),
			'new-workspace': () => onOpenWorkspaceDialog('create_workspace'),
			'new-task': () => navigateTo('workbench'),
			refresh: onRefresh,
			'command-palette': onOpenCommandPalette,
			approvals: onOpenApprovals,
			events: onOpenEvents,
			'toggle-theme': toggleTheme,
			'toggle-density': toggleDensity,
			'set-lang-en': () => setLanguage('en'),
			'set-lang-es': () => setLanguage('es'),
			'toggle-explorer': onToggleExplorer,
			'toggle-inspector': onToggleInspector,
			'toggle-bottom': onToggleBottom,
			'go-threads': () => navigateTo('threads'),
			'go-home': () => navigateTo('home'),
			'go-workbench': () => navigateTo('workbench'),
			'go-review': () => navigateTo('review-board'),
			'go-memory': () => navigateTo('memory'),
			'go-workspaces': () => navigateTo('workspaces'),
			'go-integrations': () => navigateTo('integrations'),
			'go-settings': () => (onOpenSettings ? onOpenSettings() : navigateTo('home')),
			'keyboard-shortcuts': onOpenCommandPalette,
			about: () =>
				notify({
					title: t('app.brand.title', 'AIDO Control Center'),
					body: t(
						'app.menu.aboutBody',
						'Local-first AI control center. Press Ctrl+K for the command palette.',
					),
					tone: 'info',
				}),
		}),
		[
			navigateTo,
			onOpenWorkspaceDialog,
			onOpenCommandPalette,
			onOpenApprovals,
			onOpenEvents,
			onRefresh,
			onToggleExplorer,
			onToggleInspector,
			onToggleBottom,
			onOpenSettings,
			toggleTheme,
			toggleDensity,
			setLanguage,
			notify,
			t,
		],
	);

	const closeMenu = useCallback((restoreFocus: boolean) => {
		setOpenMenu((current) => {
			if (restoreFocus && current) buttonRefs.current.get(current)?.focus();
			return null;
		});
	}, []);

	const openAt = useCallback((id: MenuId, edge: 'first' | 'last') => {
		focusEdgeRef.current = edge;
		setOpenMenu(id);
	}, []);

	const runCommand = useCallback(
		(command: MenuCommandId) => {
			closeMenu(true);
			handlers[command]();
		},
		[closeMenu, handlers],
	);

	useEffect(() => {
		if (!openMenu) return;
		const onPointerDown = (event: PointerEvent) => {
			if (!containerRef.current?.contains(event.target as Node)) setOpenMenu(null);
		};
		window.addEventListener('pointerdown', onPointerDown);
		return () => window.removeEventListener('pointerdown', onPointerDown);
	}, [openMenu]);

	const menuItemEls = useCallback(
		(): HTMLButtonElement[] =>
			Array.from(
				menuRef.current?.querySelectorAll<HTMLButtonElement>('button[role="menuitem"]') ?? [],
			),
		[],
	);

	useEffect(() => {
		if (!openMenu) return;
		const items = menuItemEls();
		const target = focusEdgeRef.current === 'last' ? items[items.length - 1] : items[0];
		target?.focus();
	}, [openMenu, menuItemEls]);

	const adjacentMenu = (id: MenuId, delta: 1 | -1): MenuId => {
		const index = MENU_ORDER.indexOf(id);
		const next = (index + delta + MENU_ORDER.length) % MENU_ORDER.length;
		return MENU_ORDER[next];
	};

	const onButtonKeyDown = (event: React.KeyboardEvent, id: MenuId) => {
		switch (event.key) {
			case 'ArrowRight': {
				event.preventDefault();
				const next = adjacentMenu(id, 1);
				if (openMenu) openAt(next, 'first');
				else buttonRefs.current.get(next)?.focus();
				break;
			}
			case 'ArrowLeft': {
				event.preventDefault();
				const prev = adjacentMenu(id, -1);
				if (openMenu) openAt(prev, 'first');
				else buttonRefs.current.get(prev)?.focus();
				break;
			}
			case 'ArrowDown':
			case 'Enter':
			case ' ':
				event.preventDefault();
				openAt(id, 'first');
				break;
			case 'ArrowUp':
				event.preventDefault();
				openAt(id, 'last');
				break;
			case 'Escape':
				if (openMenu) {
					event.preventDefault();
					closeMenu(true);
				}
				break;
		}
	};

	const onMenuKeyDown = (event: React.KeyboardEvent, id: MenuId) => {
		const items = menuItemEls();
		const activeIndex = items.indexOf(document.activeElement as HTMLButtonElement);
		switch (event.key) {
			case 'ArrowDown':
				event.preventDefault();
				items[(activeIndex + 1 + items.length) % items.length]?.focus();
				break;
			case 'ArrowUp':
				event.preventDefault();
				items[(activeIndex - 1 + items.length) % items.length]?.focus();
				break;
			case 'Home':
				event.preventDefault();
				items[0]?.focus();
				break;
			case 'End':
				event.preventDefault();
				items[items.length - 1]?.focus();
				break;
			case 'ArrowRight':
				event.preventDefault();
				openAt(adjacentMenu(id, 1), 'first');
				break;
			case 'ArrowLeft':
				event.preventDefault();
				openAt(adjacentMenu(id, -1), 'first');
				break;
			case 'Escape':
				event.preventDefault();
				closeMenu(true);
				break;
			case 'Tab':
				setOpenMenu(null);
				break;
		}
	};

	return (
		<div ref={containerRef} className="menu-bar">
			<h1 className="menu-bar-brand" aria-label={t('app.brand.title', 'AIDO Control Center')}>
				AIDO
			</h1>
			{/* El menubar ARIA envuelve SOLO los menu items: un heading no es hijo válido de
			    role="menubar", y el h1 de marca debe quedar fuera del patrón de teclado. */}
			<div
				className="menu-bar-menus"
				role="menubar"
				aria-label={t('app.menu.bar', 'Application menu')}
			>
				{MENUS.map((menu: MenuDef) => {
					const expanded = openMenu === menu.id;
					return (
						<div className="menu-bar-item" key={menu.id}>
							<button
								ref={(element) => {
									buttonRefs.current.set(menu.id, element);
								}}
								type="button"
								role="menuitem"
								className="menu-bar-button"
								aria-haspopup="menu"
								aria-expanded={expanded}
								tabIndex={openMenu === null || expanded ? 0 : -1}
								onClick={() => (expanded ? closeMenu(false) : openAt(menu.id, 'first'))}
								onMouseEnter={() => {
									if (openMenu && !expanded) openAt(menu.id, 'first');
								}}
								onKeyDown={(event) => onButtonKeyDown(event, menu.id)}
							>
								{t(menu.labelKey, menu.label)}
							</button>
							{expanded ? (
								<div
									ref={menuRef}
									className="menu-dropdown"
									role="menu"
									aria-label={t(menu.labelKey, menu.label)}
									onKeyDown={(event) => onMenuKeyDown(event, menu.id)}
								>
									{menu.items.map((item, index) =>
										item.kind === 'separator' ? (
											// biome-ignore lint/suspicious/noArrayIndexKey: separators are static and positional.
											<hr className="menu-separator" key={`sep-${index}`} />
										) : (
											<button
												key={item.command}
												type="button"
												role="menuitem"
												className="menu-dropdown-item"
												onClick={() => runCommand(item.command)}
												onMouseEnter={() => {
													if (item.page) preloadRoute(item.page);
												}}
											>
												<span>{t(item.labelKey, item.label)}</span>
												{item.shortcut ? (
													<kbd className="menu-shortcut">{item.shortcut}</kbd>
												) : null}
											</button>
										),
									)}
								</div>
							) : null}
						</div>
					);
				})}
			</div>
		</div>
	);
}
