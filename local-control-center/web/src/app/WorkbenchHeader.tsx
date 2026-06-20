/**
 * Top bar of the active workspace: identifies the current page and exposes the
 * shell-wide controls (language, theme, command palette, drawers, inspector,
 * refresh). Stateless dispatcher — every action is delegated upward via props.
 */
import { Moon, PanelBottom, PanelRight, RefreshCw, Rows3, Sun } from 'lucide-react';
import type { Ref } from 'react';

import { Button, IconButton, Tooltip } from '../components/ui';
import { useDensity } from '../hooks/useDensity';
import { useTheme } from '../hooks/useTheme';

type LanguageOption = { code: string; name: string; nativeName: string; enabled: boolean };

/**
 * Top bar for the active workspace: brand wordmark, page kicker/title, and the
 * global controls — language switch, theme toggle, command-palette / approvals /
 * events / inspector triggers and refresh. Stateless; all actions are delegated
 * to the shell via callbacks.
 */
export function WorkbenchHeader({
	kicker,
	title,
	language,
	languages,
	onChangeLanguage,
	t,
	onOpenCommandPalette,
	onOpenApprovals,
	onOpenEvents,
	onRefresh,
	inspectorOpen,
	onToggleInspector,
	inspectorToggleRef,
	bottomOpen,
	onToggleBottom,
	bottomToggleRef,
}: {
	kicker: string;
	title: string;
	language: string;
	languages: LanguageOption[];
	onChangeLanguage: (code: string) => void;
	t: (key: string, fallback?: string) => string;
	onOpenCommandPalette: () => void;
	onOpenApprovals: () => void;
	onOpenEvents: () => void;
	onRefresh: () => void;
	inspectorOpen: boolean;
	onToggleInspector: () => void;
	inspectorToggleRef?: Ref<HTMLButtonElement>;
	bottomOpen: boolean;
	onToggleBottom: () => void;
	bottomToggleRef?: Ref<HTMLButtonElement>;
}) {
	const { theme, toggleTheme } = useTheme();
	const { density, toggleDensity } = useDensity();
	const languageOptions = languages.length
		? languages
		: [
				{ code: 'es', name: 'Spanish', nativeName: 'Espanol', enabled: true },
				{ code: 'en', name: 'English', nativeName: 'English', enabled: true },
			];

	return (
		<header className="topbar">
			<div className="topbar-primary">
				<div className="topbar-heading">
					<h1 className="brand-wordmark">{t('app.brand.title', 'AIDO Control Center')}</h1>
					<div className="page-kicker">{kicker}</div>
					<h2 className="topbar-title">{title}</h2>
				</div>
				<div className="topbar-actions">
					<div
						className="language-switch"
						role="group"
						aria-label={t('app.global.languageControl', 'Language control')}
					>
						{languageOptions.map((item) => (
							<Button
								key={item.code}
								aria-pressed={language === item.code}
								onClick={() => onChangeLanguage(item.code)}
							>
								{item.code.toUpperCase()}
							</Button>
						))}
					</div>
					<Tooltip label={t('app.global.toggleTheme', 'Toggle light and dark theme')}>
						<IconButton
							aria-label={t('app.global.toggleTheme', 'Toggle light and dark theme')}
							aria-pressed={theme === 'light'}
							onClick={toggleTheme}
						>
							{theme === 'light' ? (
								<Moon aria-hidden="true" size={18} />
							) : (
								<Sun aria-hidden="true" size={18} />
							)}
						</IconButton>
					</Tooltip>
					<Tooltip label={t('app.global.toggleDensity', 'Toggle compact density')}>
						<IconButton
							aria-label={t('app.global.toggleDensity', 'Toggle compact density')}
							aria-pressed={density === 'compact'}
							onClick={toggleDensity}
						>
							<Rows3 aria-hidden="true" size={18} />
						</IconButton>
					</Tooltip>
					<Button onClick={onOpenCommandPalette}>
						{t('app.global.openCommandPalette', 'Open command palette')}
						<kbd className="command-kbd">{'Ctrl K'}</kbd>
					</Button>
					<Button onClick={onOpenApprovals}>
						{t('app.global.openApprovalsDrawer', 'Open approvals drawer')}
					</Button>
					<Button onClick={onOpenEvents}>
						{t('app.global.openEventDrawer', 'Open event drawer')}
					</Button>
					<Button
						ref={inspectorToggleRef}
						aria-pressed={inspectorOpen}
						icon={<PanelRight aria-hidden="true" size={16} />}
						onClick={onToggleInspector}
					>
						{t('app.global.inspector', 'Inspector')}
					</Button>
					<Button
						ref={bottomToggleRef}
						aria-pressed={bottomOpen}
						aria-keyshortcuts="Control+J"
						icon={<PanelBottom aria-hidden="true" size={16} />}
						onClick={onToggleBottom}
					>
						{t('app.bottomPanel.title', 'Bottom panel')}
					</Button>
					<Tooltip label={t('app.global.refreshState', 'Refresh state')}>
						<IconButton
							aria-label={t('app.global.refreshState', 'Refresh state')}
							onClick={onRefresh}
						>
							<RefreshCw aria-hidden="true" size={18} />
						</IconButton>
					</Tooltip>
				</div>
			</div>
		</header>
	);
}
