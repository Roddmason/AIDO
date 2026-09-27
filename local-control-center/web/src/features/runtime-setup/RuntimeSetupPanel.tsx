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
import { type ReactNode, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';

import {
	detectModelGatewayCliRuntime,
	getLocalEndpoints,
	getModelGatewayModels,
	getModelGatewayProviders,
	getModelGatewayRolePolicies,
	getProviderUsage,
	healthCheckModelGatewayProvider,
	type ProviderUsage,
	type ProviderUsageEntry,
	patchModelGatewayProvider,
	refreshProviderUsage,
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
import { StatusChip as Badge, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
import { invalidateRuntimeTeamCache } from '../runtime-team/useRuntimeTeam';
import { SETTING_CHANGE_EVENT } from '../settings/useSettings';
import { AddProviderWizard } from './AddProviderWizard';
import {
	DeferredLocalRuntime,
	LocalRuntimeDiscovery,
	LocalRuntimeWizard,
} from './lazyLocalRuntime';
import {
	draftFromCatalog,
	draftFromEndpoint,
	isOllamaEndpoint,
	type LocalRuntimeDraft,
} from './localEndpoints';
import { ProviderSwitch } from './ProviderSwitch';
import { hasUsageToShow, ProviderUsageSection } from './ProviderUsageSection';
import {
	COST_META,
	deriveProviderSetup,
	healthStatusLabel,
	type ProviderSetupInfo,
} from './providerCardModel';
import { describeCardReason } from './reasonCopy';
import {
	apiProviderIdsNeedingProbe,
	catalogEntry,
	deriveRuntimeAction,
	deriveRuntimeState,
	INSTRUCTIONS_KEY,
	isLocalOpenAiRuntime,
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
	/** Called after the local-runtime wizard wrote an endpoint, so sibling panels re-read it. */
	onLocalEndpointSaved?: () => void;
};

export function RuntimeSetupPanel({
	runtimeProviders,
	runtimeProviderConfiguration,
	token,
	onRefresh,
	initialProviderId,
	gatewayRevision = 0,
	onLocalEndpointSaved,
}: RuntimeSetupPanelProps) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [refreshing, setRefreshing] = useState(false);
	const [busyAction, setBusyAction] = useState<string | null>(null);
	const [accounts, setAccounts] = useState<ModelGatewayProviderAccount[]>([]);
	const [models, setModels] = useState<ModelGatewayModel[]>([]);
	const [rolePolicies, setRolePolicies] = useState<ModelGatewayRolePolicy[]>([]);
	const [usage, setUsage] = useState<ProviderUsage | null>(null);
	const [wizardOpen, setWizardOpen] = useState(false);
	const [wizardProviderId, setWizardProviderId] = useState<string | null>(null);
	const [localDraft, setLocalDraft] = useState<LocalRuntimeDraft | null>(null);
	const [deepLinkFailed, setDeepLinkFailed] = useState(false);
	const openedInitialProviderRef = useRef<string | null>(null);
	const wizardTrigger = useRef<HTMLElement | null>(null);
	useEffect(() => {
		if (!wizardOpen && localDraft === null) wizardTrigger.current?.focus();
	}, [wizardOpen, localDraft]);

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

	/** Usage quota per provider (read-only; the worker refreshes it, a failure keeps the last one). */
	const loadUsage = useCallback(async () => {
		try {
			setUsage(await getProviderUsage());
		} catch {
			// Enrichment only: cards without usage keep working.
		}
	}, []);

	/** Reads Claude/Codex usage now; the suspension it may cause changes every AI team. */
	const refreshUsage = async () => {
		if (!requireToken()) return;
		try {
			setUsage(await refreshProviderUsage(token));
			invalidateRuntimeTeamCache();
		} catch (error) {
			notify({
				title: t('app.providers.usage.refreshFailed', 'Could not read the usage now'),
				body: redactVisibleSecret(
					error instanceof Error ? error.message : String(error),
					'usage refresh failed',
				),
				tone: 'danger',
			});
		}
	};

	// The general quota threshold lives in Settings: re-read usage when the operator changes it.
	useEffect(() => {
		const onSettingChanged = (event: Event) => {
			const key = (event as CustomEvent<string>).detail;
			if (typeof key === 'string' && key.startsWith('runtime.quota.')) {
				invalidateRuntimeTeamCache();
				void loadUsage();
			}
		};
		window.addEventListener(SETTING_CHANGE_EVENT, onSettingChanged);
		return () => window.removeEventListener(SETTING_CHANGE_EVENT, onSettingChanged);
	}, [loadUsage]);

	const onUsageChanged = (next: ProviderUsage) => {
		setUsage(next);
		invalidateRuntimeTeamCache();
		void onRefresh();
	};

	// biome-ignore lint/correctness/useExhaustiveDependencies: gatewayRevision is a deliberate refresh signal after endpoint mutations (loadGateway handles all in-file mutation refreshes directly).
	useEffect(() => {
		void loadGateway();
		void loadUsage();
	}, [gatewayRevision, loadGateway, loadUsage]);

	useEffect(() => {
		const providerId = initialProviderId?.trim();
		if (!providerId || openedInitialProviderRef.current === providerId) return;
		const entry = catalogEntry(providerId);
		if (entry?.group === 'cli') return;
		openedInitialProviderRef.current = providerId;
		if (!entry) {
			const controller = new AbortController();
			getLocalEndpoints(controller.signal)
				.then((payload) => {
					const stored = payload.endpoints.find((item) => item.id === providerId);
					if (stored && !isOllamaEndpoint(stored)) setLocalDraft(draftFromEndpoint(stored));
				})
				.catch(() => {
					if (!controller.signal.aborted) setDeepLinkFailed(true);
				});
			return () => controller.abort();
		}
		if (isLocalOpenAiRuntime(entry)) {
			setLocalDraft(draftFromCatalog(providerId));
			return;
		}
		setWizardProviderId(providerId);
		setWizardOpen(true);
	}, [initialProviderId]);

	const accountById = useMemo(() => {
		const map = new Map<string, ModelGatewayProviderAccount>();
		for (const account of accounts) map.set(account.providerId, account);
		return map;
	}, [accounts]);

	const providers = useMemo(
		() => mergeProviders(runtimeProviders?.providers, runtimeProviderConfiguration, accounts),
		[runtimeProviders, runtimeProviderConfiguration, accounts],
	);
	const usageById = useMemo(() => {
		const map = new Map<string, ProviderUsageEntry>();
		for (const entry of usage?.providers ?? []) map.set(entry.providerId, entry);
		return map;
	}, [usage]);
	const suspendedCount = usage?.providers.filter((entry) => entry.suspended).length ?? 0;
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
			await Promise.all([onRefresh(), loadGateway(), loadUsage()]);
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

	/** Operator switch (`provider_accounts.enabled`): an inactive provider is used by no thread. */
	const toggleProvider = async (providerId: string, enabled: boolean) => {
		if (busyAction) return;
		if (!requireToken()) return;
		setBusyAction(`${providerId}:toggle`);
		try {
			await patchModelGatewayProvider(token, providerId, { enabled });
			// The switch changes every scope's AI team: drop the cached teams.
			invalidateRuntimeTeamCache();
			await Promise.all([onRefresh(), loadGateway()]);
			notify({
				title: enabled
					? t('app.providers.action.enabled', 'Provider enabled for threads')
					: t('app.providers.action.disabled', 'Provider disabled for threads'),
				tone: 'ok',
			});
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			notify({
				title: t('app.providers.action.toggleFailed', 'Could not update the provider'),
				body: redactVisibleSecret(message, 'provider update failed'),
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
		wizardTrigger.current = document.activeElement as HTMLElement;
		if (providerId && isLocalOpenAiRuntime(catalogEntry(providerId))) {
			setLocalDraft(draftFromCatalog(providerId));
			return;
		}
		setWizardProviderId(providerId);
		setWizardOpen(true);
	};

	return (
		<>
			<div hidden={wizardOpen || localDraft !== null}>
				<p className="muted">
					{t(
						'app.runtime.summary',
						'See, for each provider, exactly why it can or cannot execute — required configuration, detected command, and health.',
					)}
				</p>
				{deepLinkFailed ? (
					<p className="field-help" role="status">
						{t(
							'app.localRuntime.deepLinkFailed',
							'Could not open the setup of that local endpoint; use its card under Local endpoints.',
						)}
					</p>
				) : null}
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
				{suspendedCount > 0 ? (
					<p className="field-help provider-usage-summary" role="status">
						{t(
							'app.providers.usage.summary',
							'{count} provider(s) suspended by usage quota; AIDO fails over to the next runtime of each role.',
						).replace('{count}', String(suspendedCount))}
					</p>
				) : null}
				<DeferredLocalRuntime>
					<LocalRuntimeDiscovery
						token={token}
						onSetUp={(draft) => {
							wizardTrigger.current = document.activeElement as HTMLElement;
							setLocalDraft(draft);
						}}
					/>
				</DeferredLocalRuntime>
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
							? deriveProviderSetup(
									entry,
									accountById.get(provider.id) ?? null,
									models,
									rolePolicies,
								)
							: null;
						return (
							<ProviderCard
								key={provider.id}
								provider={provider}
								account={accountById.get(provider.id) ?? null}
								setup={setup}
								busyAction={busyAction}
								onSetupAction={() => runSetupAction(provider.id)}
								onTest={(model) => runProviderTask(provider.id, 'test', model)}
								onSync={() => runProviderTask(provider.id, 'sync')}
								onToggleEnabled={(enabled) => void toggleProvider(provider.id, enabled)}
								onConfigure={() => openWizard(provider.id)}
								usage={
									hasUsageToShow(usageById.get(provider.id)) ? (
										<ProviderUsageSection
											entry={usageById.get(provider.id) as ProviderUsageEntry}
											generalThresholdPercent={usage?.generalThresholdPercent ?? 80}
											token={token}
											busy={busyAction !== null}
											onChanged={onUsageChanged}
											onRefresh={refreshUsage}
										/>
									) : null
								}
							/>
						);
					})}
				</div>
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
				initialCredentialRef={storedCredentialRef(
					wizardProviderId ? accountById.get(wizardProviderId) : undefined,
				)}
				initialBaseUrl={wizardProviderId ? (accountById.get(wizardProviderId)?.baseUrl ?? '') : ''}
				onClose={() => setWizardOpen(false)}
				onSaved={() => {
					void loadGateway();
					void onRefresh();
				}}
				onLocalRuntimeSelected={(catalogId) => {
					setWizardOpen(false);
					setLocalDraft(draftFromCatalog(catalogId));
				}}
			/>
			<DeferredLocalRuntime placeholder={false}>
				<LocalRuntimeWizard
					draft={localDraft}
					token={token}
					onClose={() => setLocalDraft(null)}
					onSaved={() => {
						void loadGateway();
						void onRefresh();
						onLocalEndpointSaved?.();
					}}
				/>
			</DeferredLocalRuntime>
		</>
	);
}

function ProviderCard({
	provider,
	account,
	setup,
	busyAction,
	onSetupAction,
	onTest,
	onSync,
	onToggleEnabled,
	onConfigure,
	usage,
}: {
	provider: MergedProvider;
	/** The provider account behind the switch; null while the provider has none (not configured). */
	account: ModelGatewayProviderAccount | null;
	setup: ProviderSetupInfo | null;
	busyAction: string | null;
	/** Resolves false when the probe failed, so the card opens its recovery steps. */
	onSetupAction: () => Promise<boolean>;
	onTest: (model?: string) => void;
	onSync: () => void;
	onToggleEnabled: (enabled: boolean) => void;
	onConfigure: () => void;
	/** Usage-quota section, rendered by the panel that owns the usage state. */
	usage?: ReactNode;
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
	const isCli = provider.kind === 'cli';
	// A gateway announces every upstream it can route; with none of them enabled the backend only says
	// "model not configured", so the card tells the operator what to do instead.
	const needsModelSelection = Boolean(
		setup?.enabled &&
			catalogEntry(provider.id)?.providerType === 'gateway' &&
			setup.enabledModelCount === 0,
	);
	// The backend reason leads with a machine code (`provider_disabled: …`); the card shows its plain
	// copy and keeps the raw text in the tooltip and under Configuration details.
	const rawReason = redactVisibleSecret(
		status?.reason ?? provider.config?.reason,
		t('app.runtime.card.noReason', 'No status reported yet.'),
	);
	const reason = needsModelSelection
		? t(
				'app.providers.card.noEnabledModels',
				'No model is enabled for this gateway. Sync models, then select the ones to use in Configure.',
			)
		: describeCardReason(rawReason, t);
	const reasonTranslated = reason !== rawReason;
	const variables = provider.config?.variables ?? [];
	// The manual operator is a switch, not a runtime: there is nothing to probe or configure.
	const isManual = provider.kind === 'manual';
	const configurable = !isCli && !isManual && catalogEntry(provider.id) !== undefined;
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
				<div className="inline">
					<ProviderSwitch
						providerName={provider.displayName}
						checked={Boolean(account?.enabled)}
						busy={busyAction !== null}
						inUse={Boolean(status?.inUse)}
						unavailableHint={
							account
								? undefined
								: t(
										'app.providers.switch.noAccount',
										'Configure this provider first; there is no account to switch on yet.',
									)
						}
						onChange={onToggleEnabled}
					/>
				</div>
			</div>
			<div className="card-meta">
				<Badge tone={meta.tone}>
					<StateIcon aria-hidden="true" size={13} />
					<span>{stateLabel}</span>
				</Badge>
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
				{/* "Not checked" adds nothing next to the state badge; the Health check detail still says so. */}
				{setup && setup.healthStatus !== 'unknown' ? (
					<Badge tone={setup.healthStatus === 'healthy' ? 'ok' : 'warn'}>
						{healthStatusLabel(redactVisibleSecret(setup.healthStatus), t)}
					</Badge>
				) : null}
				{cost ? (
					<Badge tone={cost.tone}>
						<CircleDollarSign aria-hidden="true" size={12} />
						<span>{t(cost.labelKey, cost.fallback)}</span>
					</Badge>
				) : null}
			</div>
			<p className="card-body card-reason" title={reasonTranslated ? rawReason : reason}>
				{reason}
			</p>
			{usage}

			{capabilities.length ? (
				<div className="inline provider-chip-list">
					{capabilities.map((capability) => (
						<Badge tone="info" className="provider-chip" key={capability}>
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
				/* Readiness reads as a checklist, not as four more status pills: the icon and tone carry
				   yes/no, and the hidden suffix gives screen readers the same answer. */
				<ul
					className="provider-readiness"
					aria-label={t('app.providers.card.readiness', 'Readiness')}
				>
					{facts.map((fact) => {
						const FactIcon =
							fact.value === true ? CheckCircle2 : fact.value === false ? XCircle : CircleDashed;
						return (
							<li
								key={fact.id}
								data-tone={fact.value === true ? 'ok' : fact.value === false ? 'warn' : 'info'}
							>
								<FactIcon aria-hidden="true" size={13} />
								<span>{t(fact.labelKey, fact.fallback)}</span>
								<span className="sr-only">
									{fact.value === true
										? t('app.providers.card.factYes', ': yes')
										: fact.value === false
											? t('app.providers.card.factNo', ': no')
											: t('app.providers.card.factUnknown', ': not checked')}
								</span>
							</li>
						);
					})}
				</ul>
			) : (
				<span className="field-help">
					{t(
						'app.runtime.card.noStatusYet',
						'Not checked yet — run the setup check to fill in readiness.',
					)}
				</span>
			)}

			<div className="inline">
				{isManual ? null : action === 'use' ? (
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
				{configurable ? (
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

			<div
				id={detailsId}
				ref={detailsRef}
				tabIndex={-1}
				className="stack provider-card-details"
				hidden={!open}
			>
				{reasonTranslated ? (
					<section className="stack compact">
						<h5 className="field-label">
							{t('app.providers.card.reportedReason', 'Reported reason')}
						</h5>
						<span className="mono provider-card-raw-reason">{rawReason}</span>
					</section>
				) : null}
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

/** Credential placeholders the schema seeds on hosted providers (`shared/migrations.py`, phase 12). */
const SEEDED_CREDENTIAL_PLACEHOLDERS: Record<string, string> = {
	nvidia_nim: 'NVIDIA_NIM_API_KEY',
	openai_api: 'OPENAI_API_KEY',
	openai_compatible: 'OPENAI_API_KEY',
	anthropic_api: 'AIDO_ANTHROPIC_API_KEY',
	openrouter: 'OPENROUTER_API_KEY',
};

/**
 * Reference the wizard may offer to keep. A seeded placeholder (NIM's `NVIDIA_NIM_API_KEY`, stored bare or
 * normalized to `env:`) that does not resolve is not a credential the operator stored: offering "keep"
 * hid the API key field and the provider kept failing with a valid key in hand. Any other reference is
 * the operator's choice and is kept, even when it does not resolve yet.
 */
function storedCredentialRef(account: ModelGatewayProviderAccount | undefined): string {
	const ref = account?.credentialRef?.trim() ?? '';
	if (!ref || !account) return '';
	const placeholder = SEEDED_CREDENTIAL_PLACEHOLDERS[account.providerId];
	const isPlaceholder = Boolean(placeholder) && ref.replace(/^env:/, '') === placeholder;
	const resolves =
		account.credentialStatus === 'configured' || account.credentialStatus === 'unverified';
	return isPlaceholder && !resolves ? '' : ref;
}
