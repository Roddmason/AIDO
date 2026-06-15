/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useId, useMemo, useState } from 'react';
import { CheckCircle2, RefreshCw, XCircle } from 'lucide-react';

import { healthCheckModelGatewayProvider } from '../../api/client';
import type { RuntimeProviderConfiguration, RuntimeProviders } from '../../api/types';
import { Badge } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
import {
	INSTRUCTIONS_KEY,
	PROVIDER_ICON,
	STATE_META,
	apiProviderIdsNeedingProbe,
	deriveRuntimeState,
	mergeProviders,
	type MergedProvider,
	type RuntimeSetupProviderId,
} from './runtimeSetup';

type RuntimeSetupPanelProps = {
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	token: string;
	onRefresh: () => Promise<unknown> | void;
};

export function RuntimeSetupPanel({ runtimeProviders, runtimeProviderConfiguration, token, onRefresh }: RuntimeSetupPanelProps) {
	const { t } = useI18n();
	const [refreshing, setRefreshing] = useState(false);

	const providers = useMemo(
		() => mergeProviders(runtimeProviders?.providers, runtimeProviderConfiguration),
		[runtimeProviders, runtimeProviderConfiguration],
	);
	const executableCount = providers.filter((provider) => deriveRuntimeState(provider) === 'executable').length;

	const refreshHealth = async () => {
		if (refreshing) return;
		setRefreshing(true);
		try {
			// API/gateway providers expose only stored health on the status GET, so
			// actively re-probe them; CLI and Ollama re-check live on the refresh.
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
			<p className="muted">
				{t('app.runtime.summary', 'See, for each provider, exactly why it can or cannot execute — required configuration, detected command, and health.')}
			</p>
			<div className="surface-toolbar">
				<div className="inline">
					<strong className="tnum">{executableCount}</strong>
					<span className="muted">/ {providers.length} {t('app.runtime.executable', 'executable')}</span>
				</div>
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
			</div>
			<div className="masonry-grid">
				{providers.map((provider) => (
					<ProviderCard key={provider.id} provider={provider} />
				))}
			</div>
		</>
	);
}

function ProviderCard({ provider }: { provider: MergedProvider }) {
	const { t } = useI18n();
	const [open, setOpen] = useState(false);
	const detailsId = useId();

	const state = deriveRuntimeState(provider);
	const meta = STATE_META[state];
	const StateIcon = meta.Icon;
	const ProviderGlyph = PROVIDER_ICON[provider.id as RuntimeSetupProviderId];
	const stateLabel = t(meta.labelKey, state);

	const status = provider.status;
	const reason = redactVisibleSecret(status?.reason ?? provider.config?.reason, t('app.runtime.card.noReason', 'No status reported yet.'));
	const variables = provider.config?.variables ?? [];
	const isCli = provider.kind === 'cli';
	const capabilities = status?.capabilities ?? [];

	return (
		<article className="card" data-tone={meta.tone}>
			<div className="card-header">
				<div className="inline">
					{ProviderGlyph ? <ProviderGlyph aria-hidden="true" size={18} /> : null}
					<span className="card-title">{provider.displayName}</span>
				</div>
				<Badge tone={meta.tone}>
					<StateIcon aria-hidden="true" size={13} />
					<span>{stateLabel}</span>
				</Badge>
			</div>
			<div className="card-meta">
				<Badge tone="info">{provider.kind}</Badge>
			</div>
			<p className="card-body">{reason}</p>

			<button
				className="button"
				type="button"
				aria-expanded={open}
				aria-controls={detailsId}
				onClick={() => setOpen((value) => !value)}
			>
				{open ? t('app.runtime.card.hideDetails', 'Hide details') : t('app.runtime.card.details', 'Configuration details')}
			</button>

			<div id={detailsId} className="stack" hidden={!open}>
				<section className="stack compact">
					<h3 className="field-label">{t('app.runtime.card.envVars', 'Required environment variables')}</h3>
					{variables.length ? (
						variables.map((variable) => (
							<div className="inline" key={variable.key}>
								<code className="mono">{variable.name}</code>
								<Badge tone={variable.configured ? 'ok' : variable.required ? 'danger' : 'warn'}>
									{variable.configured
										? t('app.runtime.card.configured', 'configured')
										: variable.required
											? t('app.runtime.card.missing', 'missing')
											: t('app.runtime.card.optional', 'optional')}
								</Badge>
								{variable.secret ? <Badge tone="info">{t('app.runtime.card.secret', 'secret')}</Badge> : null}
								{variable.fingerprint ? <span className="mono muted">{variable.fingerprint}</span> : null}
							</div>
						))
					) : (
						<span className="muted">{t('app.runtime.card.noEnvVars', 'No environment variables required.')}</span>
					)}
					<span className="field-help">{t('app.runtime.card.noSecretsNote', 'Secret values are never shown — only whether they are set and a short fingerprint.')}</span>
				</section>

				{isCli ? (
					<section className="stack compact">
						<h3 className="field-label">{t('app.runtime.card.command', 'Command detected')}</h3>
						{status?.detectedCommand ? (
							<span className="mono">
								{redactVisibleSecret(status.detectedCommand)}
								{status.version ? ` · ${redactVisibleSecret(status.version)}` : ''}
							</span>
						) : (
							<span className="muted">{t('app.runtime.card.notDetected', 'Not detected on PATH.')}</span>
						)}
					</section>
				) : null}

				<section className="stack compact">
					<h3 className="field-label">{t('app.runtime.card.health', 'Health check')}</h3>
					<div className="inline">
						{status?.healthStatus === 'healthy' ? <CheckCircle2 aria-hidden="true" size={14} /> : <XCircle aria-hidden="true" size={14} />}
						<span className="mono">{redactVisibleSecret(status?.healthStatus, t('app.runtime.card.unknown', 'unknown'))}</span>
					</div>
					<span className="field-help">
						{t('app.runtime.card.lastChecked', 'Last checked')}: <span className="mono">{redactVisibleSecret(status?.healthCheckedAt, t('app.runtime.card.never', 'never'))}</span>
					</span>
					{status?.lastError ? (
						<span className="field-help">
							{t('app.runtime.card.lastError', 'Last error')}: {redactVisibleSecret(status.lastError, t('app.runtime.card.none', 'none'))}
						</span>
					) : null}
				</section>

				{capabilities.length || status?.requiresApproval ? (
					<section className="stack compact">
						<h3 className="field-label">{t('app.runtime.card.capabilities', 'Capabilities')}</h3>
						<div className="inline">
							{capabilities.map((capability) => (
								<Badge tone="info" key={capability}>{capability}</Badge>
							))}
							{status?.requiresApproval ? <Badge tone="warn">{t('app.runtime.card.requiresApproval', 'requires approval')}</Badge> : null}
						</div>
					</section>
				) : null}

				<section className="stack compact">
					<h3 className="field-label">{t('app.runtime.card.instructions', 'How to configure')}</h3>
					<span className="field-help">{t(INSTRUCTIONS_KEY[provider.id as RuntimeSetupProviderId], '')}</span>
				</section>
			</div>
		</article>
	);
}
