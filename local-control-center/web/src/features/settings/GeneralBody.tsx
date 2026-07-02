/**
 * General section body: interface language (immediate effect, from the live i18n
 * catalog), the startup-worker settings (wired SettingRows passed in by the section
 * registry) and the read-only platform defaults readouts.
 * @author Rodrigo Mason
 */

import type { ReactNode } from 'react';

import { SegmentedControl } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { PreferenceRow } from './AppearanceBody';

export function GeneralBody({ workerSettings }: { workerSettings: ReactNode }) {
	const { t, language, languages, setLanguage } = useI18n();
	const languageOptions = languages.map((item) => ({
		value: item.code,
		label: item.nativeName || item.name || item.code,
	}));

	return (
		<div className="stack">
			<div className="settings-list">
				<PreferenceRow
					title={t('ui.static.interface.language.9407e9ca', 'Interface language')}
					help={t('app.settings.general.languageHelp', 'Applies immediately across the interface.')}
				>
					{languageOptions.length > 0 ? (
						<SegmentedControl
							label={t('ui.static.interface.language.9407e9ca', 'Interface language')}
							value={language}
							onChange={setLanguage}
							options={languageOptions}
						/>
					) : (
						<span className="muted">
							{t('app.settings.general.languagesUnavailable', 'Language catalog unavailable')}
						</span>
					)}
				</PreferenceRow>
			</div>

			<section className="settings-group">
				<h4 className="settings-group-title">
					{t('app.settings.general.startupWorker', 'Startup worker')}
				</h4>
				<p className="settings-section-intro">
					{t(
						'app.settings.general.workerHelp',
						'The background worker that leases jobs when the platform starts.',
					)}
				</p>
				{workerSettings}
			</section>

			<section className="settings-group">
				<h4 className="settings-group-title">
					{t('app.settings.general.platform', 'Platform defaults')}
				</h4>
				<div className="settings-readouts">
					<div>
						<strong>{t('app.settings.general.backendLabel', 'Backend')}</strong>
						<span className="mono">{'FastAPI v1'}</span>
					</div>
					<div>
						<strong>{t('app.settings.general.frontendLabel', 'Frontend')}</strong>
						<span className="mono">{'Vite + React + TypeScript'}</span>
					</div>
					<div>
						<strong>{t('app.settings.general.autostartLabel', 'Autostart')}</strong>
						<span>{t('app.settings.general.autostartValue', 'User-scoped Task Scheduler')}</span>
					</div>
					<div>
						<strong>{t('settings.runtime.packageManager', 'Package manager')}</strong>
						<span className="mono">corepack pnpm@10.24.0</span>
					</div>
					<div>
						<strong>{t('app.settings.runtime.pythonRunnerLabel', 'Python runner')}</strong>
						<span className="mono">uv run</span>
					</div>
				</div>
			</section>
		</div>
	);
}
