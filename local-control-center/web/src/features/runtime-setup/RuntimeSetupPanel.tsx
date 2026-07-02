/**
 * Full runtime-setup panel: one expandable card per provider answering "why can/can't
 * this execute?" — required env vars, detected command, health and configure steps.
 * Re-probes API/gateway providers on refresh (their health is stored, not live on GET)
 * and redacts secret values everywhere, showing only set/unset state and a fingerprint.
 * @author Rodrigo Mason
 */

import { CheckCircle2, CircleDashed, RefreshCw, XCircle } from 'lucide-react';
import { useId, useMemo, useRef, useState } from 'react';

import { detectModelGatewayCliRuntime, healthCheckModelGatewayProvider } from '../../api/client';
import type { RuntimeProviderConfiguration, RuntimeProviders } from '../../api/types';
import { Badge } from '../../components/primitives';
import { useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
import {
	apiProviderIdsNeedingProbe,
	deriveRuntimeAction,
	deriveRuntimeState,
	INSTRUCTIONS_KEY,
	KIND_LABEL,
	type MergedProvider,
	mergeProviders,
	PROVIDER_ICON,
	type RuntimeSetupProviderId,
	readinessFacts,
	STATE_META,
} from './runtimeSetup';

/** Non-CLI providers offered individually in the empty state. The four CLIs are covered
 *  by the single "Detect installed CLIs" primary action instead of four equal buttons,
 *  so a user with 0 executable runtimes gets one obvious first step, not eight. */
const GUIDED_SETUP_ACTIONS = [
	{ id: 'ollama', labelKey: 'app.runtime.setup.configureOllama', label: 'Configure Ollama' },
	{ id: 'openai_compatible', labelKey: 'app.runtime.setup.configureApi', label: 'Configure API' },
	{
		id: 'openrouter',
		labelKey: 'app.runtime.setup.configureOpenRouter',
		label: 'Configure OpenRouter',
	},
	{
		id: 'nvidia_nim',
		labelKey: 'app.runtime.setup.configureNvidiaNim',
		label: 'Configure NVIDIA NIM',
	},
] as const;
const CLI_SETUP_PROVIDER_IDS = new Set<RuntimeSetupProviderId>([
	'codex_cli',
	'claude_code_cli',
	'openhands',
	'swe_agent',
]);

type RuntimeSetupPanelProps = {
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	token: string;
	onRefresh: () => Promise<unknown> | undefined;
};

export function RuntimeSetupPanel({
	runtimeProviders,
	runtimeProviderConfiguration,
	token,
	onRefresh,
}: RuntimeSetupPanelProps) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [refreshing, setRefreshing] = useState(false);
	const [busyAction, setBusyAction] = useState<string | null>(null);

	const providers = useMemo(
		() => mergeProviders(runtimeProviders?.providers, runtimeProviderConfiguration),
		[runtimeProviders, runtimeProviderConfiguration],
	);
	const executableCount = providers.filter(
		(provider) => deriveRuntimeState(provider) === 'executable',
	).length;

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

	const requireToken = (): boolean => {
		if (token) return true;
		notify({
			title: t(
				'app.runtime.setup.tokenRequired',
				'A local write token is required to run runtime setup checks.',
			),
			tone: 'warn',
		});
		return false;
	};

	/** Returns whether the probe succeeded so the card can auto-open its recovery steps. */
	const runSetupAction = async (providerId: RuntimeSetupProviderId): Promise<boolean> => {
		if (busyAction) return true;
		if (!requireToken()) return true;
		setBusyAction(providerId);
		try {
			if (CLI_SETUP_PROVIDER_IDS.has(providerId)) {
				await detectModelGatewayCliRuntime(token, providerId);
			} else {
				await healthCheckModelGatewayProvider(token, providerId);
			}
			await onRefresh();
			notify({ title: t('app.runtime.setup.checkDone', 'Runtime check finished'), tone: 'ok' });
			return true;
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			notify({
				title: t('app.runtime.setup.checkBlocked', 'Runtime check failed'),
				body: redactVisibleSecret(message, 'runtime setup action failed'),
				tone: 'danger',
			});
			return false;
		} finally {
			setBusyAction(null);
		}
	};

	/** One-click first step: probe every CLI runtime in parallel, then reload the snapshot. */
	const detectClis = async () => {
		if (busyAction) return;
		if (!requireToken()) return;
		setBusyAction('detect_clis');
		try {
			await Promise.allSettled(
				[...CLI_SETUP_PROVIDER_IDS].map((id) => detectModelGatewayCliRuntime(token, id)),
			);
			await onRefresh();
			notify({ title: t('app.runtime.setup.detectDone', 'CLI detection finished'), tone: 'ok' });
		} finally {
			setBusyAction(null);
		}
	};

	return (
		<>
			<p className="muted">
				{t(
					'app.runtime.summary',
					'See, for each provider, exactly why it can or cannot execute — required configuration, detected command, and health.',
				)}
			</p>
			<div className="surface-toolbar">
				<div className="inline">
					<span className="muted">{t('app.runtime.readyLabel', 'Runtimes ready')}</span>
					<strong className="tnum">{executableCount}</strong>
					<span className="muted">
						/ <span className="tnum">{providers.length}</span>{' '}
						{t('app.runtime.executable', 'executable')}
					</span>
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
			{executableCount === 0 ? (
				<section className="empty-state" aria-live="polite">
					<strong>{t('app.runtime.setup.noneExecutable', 'No executable runtimes')}</strong>
					<div className="inline">
						<button
							className="button primary"
							type="button"
							disabled={busyAction !== null}
							aria-busy={busyAction === 'detect_clis'}
							onClick={() => void detectClis()}
						>
							{busyAction === 'detect_clis'
								? t('app.runtime.setup.running', 'Running...')
								: t('app.runtime.setup.detectClis', 'Detect installed CLIs')}
						</button>
					</div>
					<span className="field-help">
						{t(
							'app.runtime.setup.detectClisHint',
							'One check finds Codex, Claude Code, OpenHands and SWE-agent on this machine.',
						)}
					</span>
					<div className="inline">
						{GUIDED_SETUP_ACTIONS.map((action) => (
							<button
								key={action.id}
								className="button"
								type="button"
								disabled={busyAction !== null}
								aria-busy={busyAction === action.id}
								onClick={() => void runSetupAction(action.id)}
							>
								{busyAction === action.id
									? t('app.runtime.setup.running', 'Running...')
									: t(action.labelKey, action.label)}
							</button>
						))}
					</div>
				</section>
			) : null}
			<div className="masonry-grid">
				{providers.map((provider) => (
					<ProviderCard
						key={provider.id}
						provider={provider}
						busy={busyAction === provider.id}
						onSetupAction={() => runSetupAction(provider.id as RuntimeSetupProviderId)}
					/>
				))}
			</div>
		</>
	);
}

function ProviderCard({
	provider,
	busy,
	onSetupAction,
}: {
	provider: MergedProvider;
	busy: boolean;
	/** Resolves false when the probe failed, so the card opens its recovery steps. */
	onSetupAction: () => Promise<boolean>;
}) {
	const { t } = useI18n();
	const [open, setOpen] = useState(false);
	const detailsId = useId();
	const detailsRef = useRef<HTMLDivElement | null>(null);

	const state = deriveRuntimeState(provider);
	const meta = STATE_META[state];
	const StateIcon = meta.Icon;
	const ProviderGlyph = PROVIDER_ICON[provider.id as RuntimeSetupProviderId];
	const stateLabel = t(meta.labelKey, state);

	const status = provider.status;
	const reason = redactVisibleSecret(
		status?.reason ?? provider.config?.reason,
		t('app.runtime.card.noReason', 'No status reported yet.'),
	);
	const variables = provider.config?.variables ?? [];
	const isCli = provider.kind === 'cli';
	const capabilities = status?.capabilities ?? [];
	const facts = readinessFacts(provider);
	const kindLabel = KIND_LABEL[provider.kind];
	const action = deriveRuntimeAction(state);
	const actionLabel =
		action === 'setup'
			? t('app.runtime.action.setup', 'Detect & check')
			: action === 'use'
				? t('app.runtime.action.use', 'Use in a thread')
				: t('app.runtime.action.validate', 'Validate');

	/** A failed probe opens "How to configure" and moves focus there (recovery path). */
	const runAction = async () => {
		const ok = await onSetupAction();
		if (!ok) {
			setOpen(true);
			requestAnimationFrame(() => detailsRef.current?.focus());
		}
	};

	return (
		<article className="card card--static" data-tone={meta.tone}>
			<div className="card-header">
				<div className="inline">
					{ProviderGlyph ? <ProviderGlyph aria-hidden="true" size={18} /> : null}
					<h4 className="card-title">{provider.displayName}</h4>
				</div>
				<Badge tone={meta.tone}>
					<StateIcon aria-hidden="true" size={13} />
					<span>{stateLabel}</span>
				</Badge>
			</div>
			<div className="card-meta">
				<Badge tone="info">
					{kindLabel ? t(kindLabel.labelKey, kindLabel.fallback) : provider.kind}
				</Badge>
			</div>
			<p className="card-body">{reason}</p>

			{status ? (
				<div className="inline">
					{facts.map((fact) => {
						const FactIcon =
							fact.value === true ? CheckCircle2 : fact.value === false ? XCircle : CircleDashed;
						return (
							<Badge
								key={fact.id}
								tone={fact.value === true ? 'ok' : fact.value === false ? 'warn' : 'info'}
							>
								<FactIcon aria-hidden="true" size={12} />
								<span>{t(fact.labelKey, fact.fallback)}</span>
							</Badge>
						);
					})}
				</div>
			) : (
				<span className="field-help">
					{t(
						'app.runtime.card.noStatusYet',
						'Not checked yet — run the setup check to fill in readiness.',
					)}
				</span>
			)}

			<div className="inline">
				{action === 'use' ? (
					<a className="button primary" href="#home">
						{actionLabel}
					</a>
				) : (
					<button
						className={action === 'setup' ? 'button primary' : 'button'}
						type="button"
						disabled={busy}
						aria-busy={busy}
						onClick={() => void runAction()}
					>
						{busy ? t('app.runtime.setup.running', 'Running...') : actionLabel}
					</button>
				)}
				<button
					className="button"
					type="button"
					aria-expanded={open}
					aria-controls={detailsId}
					onClick={() => setOpen((value) => !value)}
				>
					{open
						? t('app.runtime.card.hideDetails', 'Hide details')
						: t('app.runtime.card.details', 'Configuration details')}
				</button>
			</div>

			<div id={detailsId} ref={detailsRef} tabIndex={-1} className="stack" hidden={!open}>
				<section className="stack compact">
					<h5 className="field-label">
						{t('app.runtime.card.envVars', 'Required environment variables')}
					</h5>
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
								{variable.secret ? (
									<Badge tone="info">{t('app.runtime.card.secret', 'secret')}</Badge>
								) : null}
								{variable.fingerprint ? (
									<span className="mono muted">{variable.fingerprint}</span>
								) : null}
							</div>
						))
					) : (
						<span className="muted">
							{t('app.runtime.card.noEnvVars', 'No environment variables required.')}
						</span>
					)}
					<span className="field-help">
						{t(
							'app.runtime.card.noSecretsNote',
							'Secret values are never shown — only whether they are set and a short fingerprint.',
						)}
					</span>
				</section>

				{isCli ? (
					<section className="stack compact">
						<h5 className="field-label">{t('app.runtime.card.command', 'Command detected')}</h5>
						{status?.detectedCommand ? (
							<span className="mono">
								{redactVisibleSecret(status.detectedCommand)}
								{status.version ? ` · ${redactVisibleSecret(status.version)}` : ''}
							</span>
						) : (
							<span className="muted">
								{t('app.runtime.card.notDetected', 'Not detected on PATH.')}
							</span>
						)}
					</section>
				) : null}

				<section className="stack compact">
					<h5 className="field-label">{t('app.runtime.card.health', 'Health check')}</h5>
					<div className="inline">
						{status?.healthStatus === 'healthy' ? (
							<CheckCircle2 aria-hidden="true" size={14} />
						) : (
							<XCircle aria-hidden="true" size={14} />
						)}
						<span className="mono">
							{redactVisibleSecret(status?.healthStatus, t('app.runtime.card.unknown', 'unknown'))}
						</span>
					</div>
					<span className="field-help">
						{t('app.runtime.card.lastChecked', 'Last checked')}:{' '}
						<span className="mono">
							{redactVisibleSecret(status?.healthCheckedAt, t('app.runtime.card.never', 'never'))}
						</span>
					</span>
					{status?.lastError ? (
						<span className="field-help">
							{t('app.runtime.card.lastError', 'Last error')}:{' '}
							{redactVisibleSecret(status.lastError, t('app.runtime.card.none', 'none'))}
						</span>
					) : null}
				</section>

				{capabilities.length || status?.requiresApproval ? (
					<section className="stack compact">
						<h5 className="field-label">{t('app.runtime.card.capabilities', 'Capabilities')}</h5>
						<div className="inline">
							{capabilities.map((capability) => (
								<Badge tone="info" key={capability}>
									{capability}
								</Badge>
							))}
							{status?.requiresApproval ? (
								<Badge tone="warn">
									{t('app.runtime.card.requiresApproval', 'requires approval')}
								</Badge>
							) : null}
						</div>
					</section>
				) : null}

				<section className="stack compact">
					<h5 className="field-label">{t('app.runtime.card.instructions', 'How to configure')}</h5>
					<span className="field-help">
						{t(INSTRUCTIONS_KEY[provider.id as RuntimeSetupProviderId], '')}
					</span>
				</section>
			</div>
		</article>
	);
}
