/**
 * General section panel (general scope): interface language (immediate effect, from the
 * live i18n catalog), the startup-worker panel, and the read-only platform defaults.
 * The worker rows are composed as a panel over the section context rather than injected
 * as pre-rendered nodes, so this panel owns its whole surface.
 * @author Rodrigo Mason
 */

import { SegmentedControl } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { PreferenceChip, PreferenceRow } from './AppearanceSettingsPanel';
import type { SectionContext } from './sections';
import { WorkerSettingsPanel } from './WorkerSettingsPanel';

export function GeneralSettingsPanel({ ctx }: { ctx: SectionContext }) {
	const { t, catalog, language, languages, setLanguage } = useI18n();
	const languageOptions = languages.map((item) => ({
		value: item.code,
		label: item.nativeName || item.name || item.code,
	}));
	// Until the catalog lands there is no default to compare against, so the row reads
	// as the catalog default rather than claiming an override the operator never made.
	const isCatalogDefault = !catalog || language === catalog.defaultLanguage;

	return (
		<div className="stack">
			<div className="settings-list">
				<PreferenceRow
					title={t('ui.static.interface.language.9407e9ca', 'Interface language')}
					help={t('app.settings.general.languageHelp', 'Applies immediately across the interface.')}
					chip={
						isCatalogDefault ? (
							<PreferenceChip origin="default" />
						) : (
							<PreferenceChip origin="custom" source="browser" />
						)
					}
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
				<WorkerSettingsPanel ctx={ctx} />
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
