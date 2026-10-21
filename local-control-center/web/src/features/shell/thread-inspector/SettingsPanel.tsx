/**
 * Settings tab of the Thread Inspector: read-only resolved project and general settings with their source and inheritance.
 * @author Rodrigo Mason
 */
import type { ResolvedSetting, SettingsResponse } from '../../../api/client';
import { EmptyState, StatusChip } from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';
import type { InspectorResource } from '../useThreadInspectorData';
import { ResourceGate, SectionTitle } from './panelStates';

function settingValueText(value: unknown): string {
	if (value == null) return '—';
	if (typeof value === 'boolean') return value ? 'on' : 'off';
	if (typeof value === 'object') return JSON.stringify(value);
	return String(value);
}

export function SettingsPanel({ settings }: { settings: InspectorResource<SettingsResponse> }) {
	const { t } = useI18n();
	return (
		<ResourceGate resource={settings}>
			{(data) => {
				if (!data.general.length && !data.project.length) {
					return (
						<EmptyState
							title={t('app.threads.inspector.settings.emptyTitle', 'No settings resolved')}
							body={t(
								'app.threads.inspector.settings.empty',
								'No settings resolved for this project.',
							)}
						/>
					);
				}
				return (
					<div className="thread-inspector-stack">
						<SettingsSection
							title={t('app.threads.inspector.settings.project', 'Project')}
							rows={data.project}
						/>
						<SettingsSection
							title={t('app.threads.inspector.settings.general', 'General')}
							rows={data.general}
						/>
						<p className="thread-inspector-subline">
							{t(
								'app.threads.inspector.settings.hint',
								'Read-only view — change values from Settings or the composer.',
							)}
						</p>
					</div>
				);
			}}
		</ResourceGate>
	);
}

function SettingsSection({ title, rows }: { title: string; rows: ResolvedSetting[] }) {
	const { t } = useI18n();
	if (!rows.length) return null;
	return (
		<div>
			<SectionTitle>{title}</SectionTitle>
			<ul className="thread-inspector-list">
				{rows.map((setting) => (
					<li className="thread-inspector-row" key={`${setting.key}-${setting.source}`}>
						<div className="thread-inspector-row-main">
							<strong>{setting.labelKey ? t(setting.labelKey, setting.key) : setting.key}</strong>
							<span className="thread-inspector-subline mono">{setting.key}</span>
							{setting.inherited ? (
								<span className="thread-inspector-subline">
									{t('app.threads.inspector.settings.inherited', 'inherited')}
								</span>
							) : null}
						</div>
						<div className="thread-inspector-row-side">
							<span className="mono">{settingValueText(setting.value)}</span>
							<StatusChip tone="info">{setting.source}</StatusChip>
						</div>
					</li>
				))}
			</ul>
		</div>
	);
}
