/**
 * Project Plugins section body: read-only listing of the plugins currently enabled
 * for this workspace. Enablement is global and managed in the General → Plugins
 * registry; this surface only answers "what extends my runs right now", so it
 * deliberately carries no install/enable/disable affordances.
 * @author Rodrigo Mason
 */

import { useEffect, useState } from 'react';

import { listPlugins, type PluginRecord } from '../../api/client';
import { Badge, EmptyState } from '../../components/primitives';
import { ErrorState, Skeleton } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';

export function ProjectPluginsBody() {
	const { t } = useI18n();
	const [plugins, setPlugins] = useState<PluginRecord[] | null>(null);
	const [error, setError] = useState('');

	useEffect(() => {
		const controller = new AbortController();
		listPlugins(controller.signal)
			.then((payload) => setPlugins(payload.plugins))
			.catch((err: unknown) => {
				if (controller.signal.aborted) return;
				setError(err instanceof Error ? err.message : String(err));
			});
		return () => controller.abort();
	}, []);

	if (error) {
		return (
			<ErrorState
				title={t('app.settings.plugins.loadFailed', 'Failed to load plugins')}
				body={error}
			/>
		);
	}

	if (plugins === null) {
		return (
			<>
				<Skeleton
					className="settings-skeleton"
					label={t('app.settings.plugins.loading', 'Loading plugins...')}
				/>
				<Skeleton className="settings-skeleton" />
			</>
		);
	}

	const enabled = plugins.filter((plugin) => plugin.status === 'enabled');

	if (enabled.length === 0) {
		return (
			<EmptyState
				title={t('app.settings.plugins.projectEmptyTitle', 'No plugins enabled')}
				body={t(
					'app.settings.plugins.projectEmptyBody',
					'Enabled plugins appear here once activated in General settings.',
				)}
			/>
		);
	}

	return (
		<div className="stack">
			<p className="settings-section-intro">
				{t(
					'app.settings.plugins.projectNote',
					'Plugins enabled for this workspace. Enablement is managed in General settings.',
				)}
			</p>
			<div className="settings-list">
				{enabled.map((plugin) => (
					<div key={plugin.id} className="settings-plugin-row">
						<div className="settings-plugin-main">
							<strong>{plugin.name}</strong>
							<span className="muted">{plugin.publisher}</span>
							<div className="settings-plugin-tags">
								<Badge tone="ok">{plugin.status}</Badge>
								<Badge tone="info">{plugin.trustLevel}</Badge>
								{plugin.capabilities.map((capability) => (
									<span key={capability} className="setting-list-chip mono">
										{capability}
									</span>
								))}
							</div>
						</div>
						<span className="mono muted">{plugin.activeVersion?.version ?? ''}</span>
					</div>
				))}
			</div>
		</div>
	);
}
