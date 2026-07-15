/**
 * Compact runtime-setup readout for inspector/sidebar contexts: a headline state, the
 * executable/total count and a one-line-per-provider status list, with refresh-health
 * and a deep link into the full runtime setup. Shares all derivation with the panel via
 * `runtimeSetup` so both surfaces stay consistent.
 * @author Rodrigo Mason
 */

import { ArrowRight, RefreshCw } from 'lucide-react';
import { useMemo, useState } from 'react';

import { healthCheckModelGatewayProvider } from '../../api/client';
import type { RuntimeProviderConfiguration, RuntimeProviders } from '../../api/types';
import { StatusDot, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import {
	apiProviderIdsNeedingProbe,
	deriveRuntimeState,
	mergeProviders,
	STATE_META,
} from './runtimeSetup';

type RuntimeSetupInspectorCardProps = {
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	token: string;
	onRefresh: () => Promise<unknown> | undefined;
	onOpenRuntimeSetup: () => void;
};

/**
 * Refresh first re-probes API/gateway providers (when a token is present) so their
 * stored health is current, then calls `onRefresh` to reload the snapshot.
 */
export function RuntimeSetupInspectorCard({
	runtimeProviders,
	runtimeProviderConfiguration,
	token,
	onRefresh,
	onOpenRuntimeSetup,
}: RuntimeSetupInspectorCardProps) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [refreshing, setRefreshing] = useState(false);

	const providers = useMemo(
		() => mergeProviders(runtimeProviders?.providers, runtimeProviderConfiguration),
		[runtimeProviders, runtimeProviderConfiguration],
	);
	const executableCount = providers.filter(
		(provider) => deriveRuntimeState(provider) === 'executable',
	).length;
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
			notify({ title: t('app.runtime.refreshDone', 'Runtime health refreshed'), tone: 'info' });
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
				<span className="muted">
					/ {providers.length} {t('app.runtime.executable', 'executable')}
				</span>
			</div>
			<ul
				className="stack compact"
				aria-label={t('app.runtime.inspector.list', 'Runtime providers')}
			>
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
			<button
				className="button"
				type="button"
				disabled={refreshing}
				aria-busy={refreshing}
				onClick={() => void refreshHealth()}
			>
				<RefreshCw aria-hidden="true" size={15} />
				{t('app.runtime.refresh', 'Refresh health')}
			</button>
			{/* With nothing executable, advancing to setup is the recovery path — promote it. */}
			<button
				className={executableCount === 0 ? 'button primary' : 'button'}
				type="button"
				onClick={onOpenRuntimeSetup}
			>
				<ArrowRight aria-hidden="true" size={15} />
				{t('app.runtime.inspector.open', 'Open runtime setup')}
			</button>
		</>
	);
}
