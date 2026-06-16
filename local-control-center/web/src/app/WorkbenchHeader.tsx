/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { Moon, PanelRight, RefreshCw, Sun } from 'lucide-react';

import { useTheme } from '../hooks/useTheme';

type LanguageOption = { code: string; name: string; nativeName: string; enabled: boolean };

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
}) {
	const { theme, toggleTheme } = useTheme();
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
					<h1 className="brand-wordmark">AIDO Control Center</h1>
					<div className="page-kicker">{kicker}</div>
					<h2 className="topbar-title">{title}</h2>
				</div>
				<div className="topbar-actions">
					<div className="language-switch" role="group" aria-label={t('app.global.languageControl', 'Language control')}>
						{languageOptions.map((item) => (
							<button
								key={item.code}
								className="button"
								type="button"
								aria-pressed={language === item.code}
								onClick={() => onChangeLanguage(item.code)}
							>
								{item.code.toUpperCase()}
							</button>
						))}
					</div>
					<button
						className="icon-button"
						type="button"
						aria-pressed={theme === 'light'}
						aria-label={t('app.global.toggleTheme', 'Toggle light and dark theme')}
						onClick={toggleTheme}
					>
						{theme === 'light' ? <Moon aria-hidden="true" size={18} /> : <Sun aria-hidden="true" size={18} />}
					</button>
					<button className="button" type="button" onClick={onOpenCommandPalette}>
						{t('app.global.openCommandPalette', 'Open command palette')}
						<kbd className="command-kbd">{'Ctrl K'}</kbd>
					</button>
					<button className="button" type="button" onClick={onOpenApprovals}>
						{t('app.global.openApprovalsDrawer', 'Open approvals drawer')}
					</button>
					<button className="button" type="button" onClick={onOpenEvents}>
						{t('app.global.openEventDrawer', 'Open event drawer')}
					</button>
					<button
						className="button"
						type="button"
						aria-pressed={inspectorOpen}
						onClick={onToggleInspector}
					>
						<PanelRight aria-hidden="true" size={16} />
						{t('app.global.inspector', 'Inspector')}
					</button>
					<button className="icon-button" type="button" aria-label={t('app.global.refreshState', 'Refresh state')} onClick={onRefresh}>
						<RefreshCw aria-hidden="true" size={18} />
					</button>
				</div>
			</div>
		</header>
	);
}
