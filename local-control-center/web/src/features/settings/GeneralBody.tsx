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
				<div className="stack">
					<span>{t('ui.static.backend.fastapi.v1.aac8dc1e', 'Backend: FastAPI v1')}</span>
					<span>
						{t(
							'ui.static.frontend.vite.react.typescript.b3d4c705',
							'Frontend: Vite + React + TypeScript',
						)}
					</span>
					<span>
						{t(
							'ui.static.autostart.user.scoped.task.scheduler.9d01bf00',
							'Autostart: user-scoped Task Scheduler',
						)}
					</span>
					<span>
						{t(
							'ui.static.package.manager.corepack.pnpm.10.24.0.2e9f3ce1',
							'Package manager: corepack pnpm@10.24.0',
						)}
					</span>
					<span>{t('ui.static.python.runner.uv.8c1511e8', 'Python runner: uv')}</span>
				</div>
			</section>
		</div>
	);
}
