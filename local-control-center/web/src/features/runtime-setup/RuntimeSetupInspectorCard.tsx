/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useMemo, useState } from 'react';
import { ArrowRight, RefreshCw } from 'lucide-react';

import { healthCheckModelGatewayProvider } from '../../api/client';
import type { RuntimeProviderConfiguration, RuntimeProviders } from '../../api/types';
import { StatusDot } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { STATE_META, apiProviderIdsNeedingProbe, deriveRuntimeState, mergeProviders } from './runtimeSetup';

type RuntimeSetupInspectorCardProps = {
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	token: string;
	onRefresh: () => Promise<unknown> | void;
	onOpenRuntimeSetup: () => void;
};

export function RuntimeSetupInspectorCard({
	runtimeProviders,
	runtimeProviderConfiguration,
	token,
	onRefresh,
	onOpenRuntimeSetup,
}: RuntimeSetupInspectorCardProps) {
	const { t } = useI18n();
	const [refreshing, setRefreshing] = useState(false);

	const providers = useMemo(
		() => mergeProviders(runtimeProviders?.providers, runtimeProviderConfiguration),
		[runtimeProviders, runtimeProviderConfiguration],
	);
	const executableCount = providers.filter((provider) => deriveRuntimeState(provider) === 'executable').length;
	const headline = !runtimeProviders
		? t('app.runtime.inspector.pending', 'Runtime discovery pending')
		: executableCount > 0
			? t('app.runtime.inspector.ready', 'Runtime ready')
			: t('app.runtime.inspector.config', 'Configuration required');

	const refreshHealth = async () => {
		if (refreshing) return;
		setRefreshing(true);
		try {
			const probeIds = token ? apiProviderIdsNeedingProbe(providers) : [];
			if (probeIds.length) {
				await Promise.allSettled(probeIds.map((id) => healthCheckModelGatewayProvider(token, id)));
			}
			await onRefresh();
		} finally {
			setRefreshing(false);
		}
	};

	return (
		<>
			<div className="inline">
				<StatusDot tone={executableCount > 0 ? 'ok' : runtimeProviders ? 'warn' : 'info'} />
				<strong>{headline}</strong>
			</div>
			<div className="inline">
				<strong className="tnum">{executableCount}</strong>
				<span className="muted">/ {providers.length} {t('app.runtime.executable', 'executable')}</span>
			</div>
			<ul className="stack compact" aria-label={t('app.runtime.inspector.list', 'Runtime providers')}>
				{providers.map((provider) => {
					const state = deriveRuntimeState(provider);
					const meta = STATE_META[state];
					return (
						<li className="inline" key={provider.id}>
							<StatusDot tone={meta.tone} />
							<span>{provider.displayName}</span>
							<span className="muted">{t(meta.labelKey, state)}</span>
						</li>
					);
				})}
			</ul>
			<button className="button" type="button" disabled={refreshing} aria-busy={refreshing} onClick={() => void refreshHealth()}>
				<RefreshCw aria-hidden="true" size={15} />
				{t('app.runtime.refresh', 'Refresh health')}
			</button>
			<button className="button" type="button" onClick={onOpenRuntimeSetup}>
				<ArrowRight aria-hidden="true" size={15} />
				{t('app.runtime.inspector.open', 'Open runtime setup')}
			</button>
		</>
	);
}
