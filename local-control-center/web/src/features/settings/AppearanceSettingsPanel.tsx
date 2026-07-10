/**
 * Appearance section panel (general scope): theme, density and motion as immediate-effect
 * segmented controls wired to the real preference hooks (useTheme, useDensity,
 * useMotionSetting). No Save button: each choice persists to localStorage and applies to
 * <html> at once, and the hooks broadcast a window event so StatusBar/MenuBar instances
 * stay in sync.
 *
 * These preferences never reach the settings resolver, so their source chip cannot say
 * "Inherited from General". It states the truth instead: a baseline value reads Default,
 * an explicit choice reads Custom · This browser, and Motion left on the OS preference
 * reads Inherited · System — the only preference here with a real upstream source.
 * @author Rodrigo Mason
 */

import { Moon, Sun } from 'lucide-react';
import type { ReactNode } from 'react';

import { SegmentedControl } from '../../components/ui';
import { useDensity } from '../../hooks/useDensity';
import { useTheme } from '../../hooks/useTheme';
import { useI18n } from '../../i18n/I18nProvider';
import { useMotionSetting } from '../../motion/useControlMotion';

/** Where a browser-local preference's effective value comes from. */
type PreferenceOrigin = 'default' | 'custom' | 'inherited';

/** Upstream that supplies a non-default preference value. */
type PreferenceSource = 'system' | 'browser';

/**
 * Source chip for a browser-local preference. Shares the wired `.setting-chip` markup and
 * `data-origin` states with SettingRow's ChipLabel so both surfaces read as one vocabulary.
 */
export function PreferenceChip({
	origin,
	source,
}: {
	origin: PreferenceOrigin;
	source?: PreferenceSource;
}) {
	const { t } = useI18n();
	const label =
		origin === 'default'
			? t('app.settings.chip.default', 'Default')
			: origin === 'custom'
				? t('app.settings.chip.custom', 'Custom')
				: t('app.settings.chip.inherited', 'Inherited');
	const sourceLabel =
		source === 'system'
			? t('app.settings.chip.source.system', 'System')
			: source === 'browser'
				? t('app.settings.chip.source.browser', 'This browser')
				: '';

	return (
		<span className="setting-chip" data-origin={origin}>
			{label}
			{sourceLabel ? (
				<>
					{' · '}
					<span className="setting-chip-source">{sourceLabel}</span>
				</>
			) : null}
		</span>
	);
}

/** One labelled preference row: copy and source chip on the left, its control on the right. */
export function PreferenceRow({
	title,
	help,
	chip,
	children,
}: {
	title: string;
	help: string;
	/** Source chip for the preference; omitted when the row has no meaningful origin. */
	chip?: ReactNode;
	children: ReactNode;
}) {
	return (
		<div className="settings-preference-row">
			<div className="settings-preference-copy">
				<strong>{title}</strong>
				<span className="muted">{help}</span>
				{chip}
			</div>
			{children}
		</div>
	);
}

export function AppearanceSettingsPanel() {
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
				chip={
					theme === 'dark' ? (
						<PreferenceChip origin="default" />
					) : (
						<PreferenceChip origin="custom" source="browser" />
					)
				}
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
				chip={
					density === 'comfortable' ? (
						<PreferenceChip origin="default" />
					) : (
						<PreferenceChip origin="custom" source="browser" />
					)
				}
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
				chip={
					motion === 'system' ? (
						<PreferenceChip origin="inherited" source="system" />
					) : (
						<PreferenceChip origin="custom" source="browser" />
					)
				}
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
