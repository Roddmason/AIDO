/**
 * Appearance section body: theme, density and motion as immediate-effect segmented
 * controls wired to the real preference hooks (useTheme, useDensity, useMotionSetting).
 * No Save button: each choice persists to localStorage and applies to <html> at once,
 * and the hooks broadcast a window event so StatusBar/MenuBar instances stay in sync.
 * @author Rodrigo Mason
 */

import { Moon, Sun } from 'lucide-react';

import { SegmentedControl } from '../../components/ui';
import { useDensity } from '../../hooks/useDensity';
import { useTheme } from '../../hooks/useTheme';
import { useI18n } from '../../i18n/I18nProvider';
import { useMotionSetting } from '../../motion/useControlMotion';

/** One labelled preference row: copy on the left, its segmented control on the right. */
export function PreferenceRow({
	title,
	help,
	children,
}: {
	title: string;
	help: string;
	children: React.ReactNode;
}) {
	return (
		<div className="settings-preference-row">
			<div className="settings-preference-copy">
				<strong>{title}</strong>
				<span className="muted">{help}</span>
			</div>
			{children}
		</div>
	);
}

export function AppearanceBody() {
	const { t } = useI18n();
	const { theme, setTheme } = useTheme();
	const { density, setDensity } = useDensity();
	const { motion, setMotion } = useMotionSetting();

	return (
		<div className="settings-list">
			<PreferenceRow
				title={t('app.settings.appearance.theme', 'Theme')}
				help={t(
					'app.settings.appearance.themeHelp',
					'Dark is the default; Light is an opt-in override.',
				)}
			>
				<SegmentedControl
					label={t('app.settings.appearance.theme', 'Theme')}
					value={theme}
					onChange={setTheme}
					options={[
						{
							value: 'dark',
							label: (
								<>
									<Moon aria-hidden="true" size={14} /> {t('app.settings.appearance.dark', 'Dark')}
								</>
							),
						},
						{
							value: 'light',
							label: (
								<>
									<Sun aria-hidden="true" size={14} /> {t('app.settings.appearance.light', 'Light')}
								</>
							),
						},
					]}
				/>
			</PreferenceRow>

			<PreferenceRow
				title={t('app.settings.appearance.density', 'Density')}
				help={t(
					'app.settings.appearance.densityHelp',
					'Comfortable keeps touch-safe controls; Compact tightens spacing for dense sessions.',
				)}
			>
				<SegmentedControl
					label={t('app.settings.appearance.density', 'Density')}
					value={density}
					onChange={setDensity}
					options={[
						{
							value: 'comfortable',
							label: t('app.settings.appearance.comfortable', 'Comfortable'),
						},
						{ value: 'compact', label: t('app.settings.appearance.compact', 'Compact') },
					]}
				/>
			</PreferenceRow>

			<PreferenceRow
				title={t('app.settings.appearance.motion', 'Motion')}
				help={t(
					'app.settings.appearance.motionHelp',
					'Reduced turns off non-essential transitions.',
				)}
			>
				<SegmentedControl
					label={t('app.settings.appearance.motion', 'Motion')}
					value={motion}
					onChange={setMotion}
					options={[
						{ value: 'system', label: t('app.settings.appearance.followSystem', 'Follow system') },
						{ value: 'reduced', label: t('app.settings.appearance.reduced', 'Reduced') },
					]}
				/>
			</PreferenceRow>
		</div>
	);
}
