/**
 * Providers & CLI setup catalog: one card per catalog provider that answers both "why can/can't this
 * execute?" (runtime detection, health, required env vars — secrets never shown) and "how is it set
 * up?" (credential state, base URL, discovered models, cost knowledge, and which roles route to it).
 * CLI runtimes keep their Detect & check flow; API/local providers add Test prompt, Sync models and a
 * Configure action that opens the Add-provider wizard. Re-probes API/gateway providers on refresh.
 * @author Rodrigo Mason
 */

import {
	CheckCircle2,
	CircleDashed,
	CircleDollarSign,
	Plus,
	RefreshCw,
	Send,
	Settings2,
	XCircle,
} from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';

import {
	detectModelGatewayCliRuntime,
	getModelGatewayModels,
	getModelGatewayProviders,
	getModelGatewayRolePolicies,
	healthCheckModelGatewayProvider,
	syncProviderAccountModels,
	testPromptModelGatewayProvider,
} from '../../api/client';
import type {
	ModelGatewayModel,
	ModelGatewayProviderAccount,
	ModelGatewayRolePolicy,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../../api/types';
import { Badge } from '../../components/primitives';
import { useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
import { AddProviderWizard } from './AddProviderWizard';
import { COST_META, deriveProviderSetup, type ProviderSetupInfo } from './providerCardModel';
import {
	apiProviderIdsNeedingProbe,
	catalogEntry,
	deriveRuntimeAction,
	deriveRuntimeState,
	INSTRUCTIONS_KEY,
	KIND_LABEL,
	type MergedProvider,
	mergeProviders,
	PROVIDER_ICON,
	readinessFacts,
	STATE_META,
} from './runtimeSetup';

/** Non-CLI providers offered individually in the empty state; the CLIs are covered by a single
 *  "Detect installed CLIs" action so a user with 0 executable runtimes gets one obvious first step. */
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
const CLI_SETUP_PROVIDER_IDS = new Set(['codex_cli', 'claude_code_cli', 'openhands', 'swe_agent']);

type RuntimeSetupPanelProps = {
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	token: string;
	onRefresh: () => Promise<unknown> | undefined;
	initialProviderId?: string | null;
	gatewayRevision?: number;
};

export function RuntimeSetupPanel({
	runtimeProviders,
	runtimeProviderConfiguration,
	token,
	onRefresh,
	initialProviderId,
	gatewayRevision = 0,
}: RuntimeSetupPanelProps) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [refreshing, setRefreshing] = useState(false);
	const [busyAction, setBusyAction] = useState<string | null>(null);
	const [accounts, setAccounts] = useState<ModelGatewayProviderAccount[]>([]);
	const [models, setModels] = useState<ModelGatewayModel[]>([]);
	const [rolePolicies, setRolePolicies] = useState<ModelGatewayRolePolicy[]>([]);
	const [wizardOpen, setWizardOpen] = useState(false);
	const [wizardProviderId, setWizardProviderId] = useState<string | null>(null);
	const openedInitialProviderRef = useRef<string | null>(null);

	const loadGateway = useCallback(async () => {
		try {
			const [providerPayload, modelPayload, rolePayload] = await Promise.all([
				getModelGatewayProviders(),
				getModelGatewayModels(),
				getModelGatewayRolePolicies(),
			]);
			setAccounts(providerPayload.providers);
			setModels(modelPayload.models);
			setRolePolicies(rolePayload.rolePolicies);
		} catch {
			// Read-only enrichment: a failed gateway fetch leaves cards on their runtime-diagnostic data.
		}
	}, []);

	// biome-ignore lint/correctness/useExhaustiveDependencies: gatewayRevision is a deliberate refresh signal after endpoint mutations (loadGateway handles all in-file mutation refreshes directly).
	useEffect(() => {
		void loadGateway();
	}, [gatewayRevision, loadGateway]);

	useEffect(() => {
		const providerId = initialProviderId?.trim();
		if (!providerId || openedInitialProviderRef.current === providerId) return;
		const entry = catalogEntry(providerId);
		if (!entry || entry.group === 'cli') return;
		openedInitialProviderRef.current = providerId;
		setWizardProviderId(providerId);
		setWizardOpen(true);
	}, [initialProviderId]);

	const accountById = useMemo(() => {
		const map = new Map<string, ModelGatewayProviderAccount>();
		for (const account of accounts) map.set(account.providerId, account);
		return map;
	}, [accounts]);

	const providers = useMemo(
		() => mergeProviders(runtimeProviders?.providers, runtimeProviderConfiguration),
		[runtimeProviders, runtimeProviderConfiguration],
	);
	const executableCount = providers.filter(
		(provider) => deriveRuntimeState(provider) === 'executable',
	).length;

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

	const refreshHealth = async () => {
		if (refreshing) return;
		setRefreshing(true);
		try {
			const probeIds = token ? apiProviderIdsNeedingProbe(providers) : [];
			if (probeIds.length) {
				await Promise.allSettled(probeIds.map((id) => healthCheckModelGatewayProvider(token, id)));
			}
			await Promise.all([onRefresh(), loadGateway()]);
			notify({ title: t('app.runtime.refreshDone', 'Runtime health refreshed'), tone: 'info' });
		} finally {
			setRefreshing(false);
		}
	};

	/** Returns whether the probe succeeded so the card can auto-open its recovery steps. */
	const runSetupAction = async (providerId: string): Promise<boolean> => {
		if (busyAction) return true;
		if (!requireToken()) return true;
		setBusyAction(providerId);
		try {
			if (CLI_SETUP_PROVIDER_IDS.has(providerId)) {
				await detectModelGatewayCliRuntime(token, providerId);
			} else {
				await healthCheckModelGatewayProvider(token, providerId);
			}
			await Promise.all([onRefresh(), loadGateway()]);
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

	const runProviderTask = async (providerId: string, task: 'test' | 'sync', model?: string) => {
		if (busyAction) return;
		if (!requireToken()) return;
		setBusyAction(`${providerId}:${task}`);
		try {
			if (task === 'sync') {
				const result = await syncProviderAccountModels(token, providerId);
				const count = (result as { models?: unknown[] }).models?.length ?? 0;
				notify({
					title: t('app.providers.action.modelsSynced', 'Models synced'),
					body: String(count),
					tone: 'ok',
				});
			} else {
				const response = await testPromptModelGatewayProvider(
					token,
					providerId,
					model ? { model } : {},
				);
				const outcome = (response as { test: { ok: boolean; latencyMs?: number } }).test;
				notify({
					title: outcome.ok
						? t('app.providers.action.testOk', 'Provider responded')
						: t('app.providers.action.testFailed', 'Test prompt failed'),
					body:
						outcome.ok && typeof outcome.latencyMs === 'number' ? `${outcome.latencyMs} ms` : '',
					tone: outcome.ok ? 'ok' : 'danger',
				});
			}
			await loadGateway();
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			notify({
				title: t('app.providers.action.taskFailed', 'Provider action failed'),
				body: redactVisibleSecret(message, 'provider action failed'),
				tone: 'danger',
			});
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
			const results = await Promise.allSettled(
				[...CLI_SETUP_PROVIDER_IDS].map((id) => detectModelGatewayCliRuntime(token, id)),
			);
			await Promise.all([onRefresh(), loadGateway()]);
			const failures = results.filter((result) => result.status === 'rejected');
			if (failures.length === results.length) {
				notify({
					title: t('app.runtime.setup.detectFailed', 'CLI detection failed'),
					body: redactVisibleSecret(
						failures[0]?.reason,
						t(
							'app.runtime.setup.detectFailedBody',
							'AIDO could not run any CLI detection request.',
						),
					),
					tone: 'danger',
				});
			} else if (failures.length > 0) {
				notify({
					title: t('app.runtime.setup.detectPartial', 'CLI detection partially completed'),
					body: t(
						'app.runtime.setup.detectPartialBody',
						'Some CLI checks failed. Retry detection to refresh the remaining runtimes.',
					),
					tone: 'warn',
				});
			} else {
				notify({ title: t('app.runtime.setup.detectDone', 'CLI detection finished'), tone: 'ok' });
			}
		} finally {
			setBusyAction(null);
		}
	};

	const openWizard = (providerId: string | null) => {
		setWizardProviderId(providerId);
		setWizardOpen(true);
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
				<div className="inline">
					<button className="button primary" type="button" onClick={() => openWizard(null)}>
						<Plus aria-hidden="true" size={15} />
						{t('app.providers.addProvider', 'Add provider')}
					</button>
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
								onClick={() => openWizard(action.id)}
							>
								{t(action.labelKey, action.label)}
							</button>
						))}
					</div>
				</section>
			) : null}
			<div className="masonry-grid">
				{providers.map((provider) => {
					const entry = catalogEntry(provider.id);
					const setup = entry
						? deriveProviderSetup(entry, accountById.get(provider.id) ?? null, models, rolePolicies)
						: null;
					return (
						<ProviderCard
							key={provider.id}
							provider={provider}
							setup={setup}
							busyAction={busyAction}
							onSetupAction={() => runSetupAction(provider.id)}
							onTest={(model) => runProviderTask(provider.id, 'test', model)}
							onSync={() => runProviderTask(provider.id, 'sync')}
							onConfigure={() => openWizard(provider.id)}
						/>
					);
				})}
			</div>
			<AddProviderWizard
				open={wizardOpen}
				token={token}
				initialProviderId={wizardProviderId}
				initialPricingMode={
					wizardProviderId ? accountById.get(wizardProviderId)?.pricingMode : undefined
				}
				initialFreeTierAttested={
					wizardProviderId
						? accountById.get(wizardProviderId)?.metadata?.freeTierDeclaredByOperator === true
						: false
				}
				onClose={() => setWizardOpen(false)}
				onSaved={() => void refreshHealth()}
			/>
		</>
	);
}

function ProviderCard({
	provider,
	setup,
	busyAction,
	onSetupAction,
	onTest,
	onSync,
	onConfigure,
}: {
	provider: MergedProvider;
	setup: ProviderSetupInfo | null;
	busyAction: string | null;
	/** Resolves false when the probe failed, so the card opens its recovery steps. */
	onSetupAction: () => Promise<boolean>;
	onTest: (model?: string) => void;
	onSync: () => void;
	onConfigure: () => void;
}) {
	const { t } = useI18n();
	const [open, setOpen] = useState(false);
	const detailsId = useId();
	const detailsRef = useRef<HTMLDivElement | null>(null);

	const state = deriveRuntimeState(provider);
	const meta = STATE_META[state];
	const StateIcon = meta.Icon;
	const ProviderGlyph = PROVIDER_ICON[provider.id];
	const stateLabel = t(meta.labelKey, state);

	const status = provider.status;
	const reason = redactVisibleSecret(
		status?.reason ?? provider.config?.reason,
		t('app.runtime.card.noReason', 'No status reported yet.'),
	);
	const variables = provider.config?.variables ?? [];
	const isCli = provider.kind === 'cli';
	const capabilities = catalogEntry(provider.id)?.capabilities ?? status?.capabilities ?? [];
	const facts = readinessFacts(provider);
	const kindLabel = KIND_LABEL[provider.kind];
	const action = deriveRuntimeAction(state);
	const actionLabel =
		action === 'setup'
			? t('app.runtime.action.setup', 'Detect & check')
			: action === 'use'
				? t('app.runtime.action.use', 'Use in a thread')
				: t('app.runtime.action.validate', 'Validate');

	const busy = busyAction === provider.id;
	const testBusy = busyAction === `${provider.id}:test`;
	const syncBusy = busyAction === `${provider.id}:sync`;
	const cost = setup ? COST_META[setup.cost] : null;
	const canRunTasks = Boolean(setup?.enabled && setup?.hasCredential && !isCli);

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
				{setup ? (
					<Badge tone={setup.hasCredential ? 'ok' : 'warn'}>
						{setup.hasCredential
							? t('app.providers.credential.configured', 'credential set')
							: t('app.providers.credential.missing', 'no credential')}
					</Badge>
				) : null}
				{setup ? (
					<Badge tone={setup.healthStatus === 'healthy' ? 'ok' : 'warn'}>
						{redactVisibleSecret(setup.healthStatus, t('app.runtime.card.unknown', 'unknown'))}
					</Badge>
				) : null}
				{cost ? (
					<Badge tone={cost.tone}>
						<CircleDollarSign aria-hidden="true" size={12} />
						<span>{t(cost.labelKey, cost.fallback)}</span>
					</Badge>
				) : null}
			</div>
			<p className="card-body card-reason" title={reason}>
				{reason}
			</p>

			{capabilities.length ? (
				<div className="inline">
					{capabilities.map((capability) => (
						<Badge tone="info" key={capability}>
							{capability}
						</Badge>
					))}
				</div>
			) : null}

			<dl className="provider-facts">
				{!isCli ? (
					<div>
						<dt>{t('app.providers.card.baseUrl', 'Base URL')}</dt>
						<dd className="mono">
							{setup?.baseUrl || t('app.providers.card.baseUrlUnset', 'not set')}
						</dd>
					</div>
				) : null}
				{setup && !isCli ? (
					<div>
						<dt>{t('app.providers.card.models', 'Models')}</dt>
						<dd className="tnum">{setup.modelCount}</dd>
					</div>
				) : null}
			</dl>

			{setup?.roles.length ? (
				<div className="inline">
					<span className="field-label">
						{t('app.providers.card.useForRoles', 'Use for roles')}
					</span>
					{setup.roles.slice(0, 6).map((role) => (
						<Badge tone="info" key={role}>
							{role}
						</Badge>
					))}
				</div>
			) : null}

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
				{!isCli ? (
					<button className="button" type="button" onClick={onConfigure}>
						<Settings2 aria-hidden="true" size={14} />
						{t('app.providers.card.configure', 'Configure')}
					</button>
				) : null}
				{canRunTasks ? (
					<button
						className="button"
						type="button"
						disabled={syncBusy}
						aria-busy={syncBusy}
						onClick={onSync}
					>
						<RefreshCw aria-hidden="true" size={14} />
						{t('app.providers.card.syncModels', 'Sync models')}
					</button>
				) : null}
				{canRunTasks ? (
					<button
						className="button"
						type="button"
						disabled={testBusy}
						aria-busy={testBusy}
						onClick={() => onTest()}
					>
						<Send aria-hidden="true" size={14} />
						{t('app.providers.card.testPrompt', 'Test prompt')}
					</button>
				) : null}
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
							/* Executable paths are unbreakable tokens: without anywhere-wrap this line sets the
							   whole card's min-content width and forces a sideways scroll on the card grid. */
							<span className="mono" style={{ overflowWrap: 'anywhere' }}>
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
						{status?.healthStatus === 'healthy' || setup?.healthStatus === 'healthy' ? (
							<CheckCircle2 aria-hidden="true" size={14} />
						) : (
							<XCircle aria-hidden="true" size={14} />
						)}
						<span className="mono">
							{redactVisibleSecret(
								status?.healthStatus ?? setup?.healthStatus,
								t('app.runtime.card.unknown', 'unknown'),
							)}
						</span>
					</div>
					<span className="field-help">
						{t('app.runtime.card.lastChecked', 'Last checked')}:{' '}
						<span className="mono">
							{redactVisibleSecret(
								status?.healthCheckedAt ?? setup?.account?.lastHealthCheckAt,
								t('app.runtime.card.never', 'never'),
							)}
						</span>
					</span>
					{status?.lastError ? (
						<span className="field-help">
							{t('app.runtime.card.lastError', 'Last error')}:{' '}
							{redactVisibleSecret(status.lastError, t('app.runtime.card.none', 'none'))}
						</span>
					) : null}
				</section>

				<section className="stack compact">
					<h5 className="field-label">{t('app.runtime.card.instructions', 'How to configure')}</h5>
					<span className="field-help">{t(INSTRUCTIONS_KEY[provider.id] ?? '', '')}</span>
				</section>
			</div>
		</article>
	);
}
