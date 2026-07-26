/**
 * Página IDE del Model Gateway: orquesta todos los sub-paneles de proveedores, ruteo, uso y benchmarks.
 * Concentra la carga de estado del gateway (overview, catálogos, ledger, decisiones) y las acciones de
 * mutación (toggle/health de proveedores, preview de ruta, alta de políticas y outcomes); cada panel
 * recibe sus datos por props y notifica de vuelta vía callbacks.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import {
	createModelGatewayRolePolicy,
	detectModelGatewayCliRuntime,
	discoverModelGatewayProviderModels,
	getModelGatewayBenchmarkOutcomes,
	getModelGatewayBenchmarks,
	healthCheckModelGatewayProvider,
	type ModelGatewayRoutePreviewResponse,
	patchModelGatewayProvider,
	previewModelRoute,
	recordModelGatewayBenchmarkOutcome,
} from '../../api/client';
import type {
	ModelGatewayBenchmark,
	ModelGatewayBenchmarkOutcome,
	ModelGatewayRolePolicy,
	Overview,
	RuntimeProvider,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../../api/types';
import { resolveHashRoute, splitHash } from '../../app/routing';
import {
	StatusChip as Badge,
	DataTable,
	EmptyState,
	ErrorState,
	PageHeader,
	Surface,
	Tabs,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { sumRecordedCostUsd, toneForStatus } from '../../lib/format';
import { BenchmarksPanel } from './BenchmarksPanel';
import { BudgetsPanel } from './BudgetsPanel';
import { CliSessionsPanel } from './CliSessionsPanel';
import { ModelCatalogPanel } from './ModelCatalogPanel';
import { NvidiaNimConfigurationPanel } from './NvidiaNimConfigurationPanel';
import { NvidiaNimModelManifestPanel } from './NvidiaNimModelManifestPanel';
import { ParallelAIExecutionPanel } from './ParallelAIExecutionPanel';
import { ProviderAccountsPanel } from './ProviderAccountsPanel';
import { ProviderLimitsPanel } from './ProviderLimitsPanel';
import { RoleAssignmentsPanel } from './RoleAssignmentsPanel';
import { RoutePreviewPanel } from './RoutePreviewPanel';
import { RoutingDecisionsPanel } from './RoutingDecisionsPanel';
import { RoutingProfilesPanel } from './RoutingProfilesPanel';
import { RuntimeProvidersPanel } from './RuntimeProvidersPanel';
import { UsageLedgerPanel } from './UsageLedgerPanel';
import {
	emptyGatewayState,
	MODEL_GATEWAY_TABS,
	type ModelGatewayState,
	type ModelGatewayTab,
	useModelGatewayTabData,
} from './useModelGatewayTabData';
import { Metric, money, text } from './utils';

function upsertNewestById<T extends { id: string; updatedAt?: string; createdAt?: string }>(
	records: T[],
	incoming: T,
) {
	const existing = records.find((record) => record.id === incoming.id);
	const incomingTime = Date.parse(String(incoming.updatedAt ?? incoming.createdAt ?? ''));
	const existingTime = Date.parse(String(existing?.updatedAt ?? existing?.createdAt ?? ''));
	if (
		existing &&
		!Number.isNaN(existingTime) &&
		(Number.isNaN(incomingTime) || existingTime > incomingTime)
	)
		return records;
	return existing
		? records.map((record) => (record.id === incoming.id ? incoming : record))
		: [incoming, ...records];
}

function benchmarkFromOutcome(outcome: ModelGatewayBenchmarkOutcome): ModelGatewayBenchmark {
	const now = new Date().toISOString();
	const createdAt = text(outcome.createdAt, now);
	const provenance = text(outcome.provenance, 'operator_reported');
	const operatorReportedTasks = provenance === 'operator_reported' ? 1 : 0;
	const automatedRunTasks = provenance === 'automated_run' ? 1 : 0;
	const releaseValidationTasks = provenance === 'release_validation' ? 1 : 0;
	const objectiveTasksAttempted = automatedRunTasks + releaseValidationTasks;
	return {
		id: `outcome-derived:${text(outcome.providerId, '')}:${text(outcome.model, '')}:${text(outcome.role, '')}`,
		providerId: outcome.providerId,
		model: outcome.model,
		role: outcome.role,
		tasksAttempted: 1,
		objectiveTasksAttempted,
		operatorReportedTasks,
		automatedRunTasks,
		releaseValidationTasks,
		successRate: objectiveTasksAttempted ? (outcome.success ? 1 : 0) : null,
		qaPassRate: objectiveTasksAttempted ? (outcome.qaPass ? 1 : 0) : null,
		reworkRate: objectiveTasksAttempted ? (outcome.rework ? 1 : 0) : null,
		avgCost: objectiveTasksAttempted
			? (outcome.actualCostUsd ?? outcome.estimatedCostUsd ?? null)
			: null,
		avgLatencyMs: objectiveTasksAttempted ? (outcome.latencyMs ?? null) : null,
		lastUsedAt: createdAt,
		insufficientData: objectiveTasksAttempted < 3,
		provenanceCounts: {
			operator_reported: operatorReportedTasks,
			automated_run: automatedRunTasks,
			release_validation: releaseValidationTasks,
		},
		metadata: { source: 'local_projection_from_persisted_outcome' },
		createdAt,
		updatedAt: createdAt,
	};
}

function policyBudgetUsd(row: Overview['modelPolicies'][number] | ModelGatewayRolePolicy) {
	return 'maxCostPerTaskUsd' in row ? row.maxCostPerTaskUsd : row.maxCostUsd;
}

/** Reads the deep-linked tab from `#models?tab=<id>`, defaulting to the providers tab. */
function initialModelGatewayTab(): ModelGatewayTab {
	const tab = splitHash().params.get('tab');
	return (MODEL_GATEWAY_TABS as string[]).includes(tab ?? '')
		? (tab as ModelGatewayTab)
		: 'providers';
}

/**
 * Compone la consola completa del Model Gateway a partir del `overview` ya cargado por el host.
 * Recarga su propio estado del gateway al montar y tras cada acción; usa `token` para las mutaciones
 * autenticadas y `onRefreshRuntimeProviders` para resincronizar el estado de runtimes del host.
 */
export function ModelGatewayPage({
	overview,
	runtimeProviders,
	token,
	onRefreshRuntimeProviders,
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	token: string;
	onRefreshRuntimeProviders: () => Promise<unknown>;
}) {
	const { t } = useI18n();
	const [gateway, setGateway] = useState<ModelGatewayState>(emptyGatewayState);
	const [error, setError] = useState('');
	const [usageFilter, setUsageFilter] = useState('');
	const [decisionFilter, setDecisionFilter] = useState('');
	const [previewRole, setPreviewRole] = useState('developer');
	const [previewMode, setPreviewMode] = useState('balanced_best_value');
	const [previewTaskType, setPreviewTaskType] = useState('implementation');
	const [previewRisk, setPreviewRisk] = useState('medium');
	const [previewTokens, setPreviewTokens] = useState('45000');
	const [previewBudget, setPreviewBudget] = useState('4.20');
	const [previewPrivacy, setPreviewPrivacy] = useState('remote_allowed');
	const [requiresCodeEdit, setRequiresCodeEdit] = useState(true);
	const [requiresTools, setRequiresTools] = useState(true);
	const [requiresSearch, setRequiresSearch] = useState(false);
	const [requiresReasoning, setRequiresReasoning] = useState(false);
	const [requiresJson, setRequiresJson] = useState(false);
	const [preview, setPreview] = useState<ModelGatewayRoutePreviewResponse | null>(null);
	const [busyAction, setBusyAction] = useState('');
	const [policyId, setPolicyId] = useState('implementation_default');
	const [policyName, setPolicyName] = useState('Implementation Default');
	const [policyProvider, setPolicyProvider] = useState('ollama');
	const [policyModel, setPolicyModel] = useState('');
	const [policyMaxCostUsd, setPolicyMaxCostUsd] = useState('1');
	const [policyMaxTokens, setPolicyMaxTokens] = useState('4000');
	const [policyAllowRemote, setPolicyAllowRemote] = useState(false);
	const [policyAllowLocal, setPolicyAllowLocal] = useState(true);
	const [policyError, setPolicyError] = useState('');
	const [createdPolicies, setCreatedPolicies] = useState<ModelGatewayRolePolicy[]>([]);
	const [benchmarkProvider, setBenchmarkProvider] = useState('codex_cli');
	const [benchmarkModel, setBenchmarkModel] = useState('gpt-5.5');
	const [benchmarkRuntime, setBenchmarkRuntime] = useState('cli');
	const [benchmarkRole, setBenchmarkRole] = useState('developer');
	const [benchmarkSuccess, setBenchmarkSuccess] = useState(false);
	const [benchmarkQaPass, setBenchmarkQaPass] = useState(false);
	const [benchmarkRework, setBenchmarkRework] = useState(false);
	const [benchmarkCost, setBenchmarkCost] = useState('');
	const [benchmarkLatency, setBenchmarkLatency] = useState('');
	const [benchmarkError, setBenchmarkError] = useState('');
	const [runtimeProviderState, setRuntimeProviderState] = useState<RuntimeProviders | null>(
		runtimeProviders,
	);

	useEffect(() => {
		if (!runtimeProviders) return;
		setRuntimeProviderState((current) => {
			const incomingCount = runtimeProviders.providers?.length ?? 0;
			const currentCount = current?.providers?.length ?? 0;
			if (incomingCount === 0 && currentCount > 0) return current;
			return runtimeProviders;
		});
	}, [runtimeProviders]);

	const [activeTab, setActiveTab] = useState<ModelGatewayTab>(initialModelGatewayTab);
	const tabs = useModelGatewayTabData({ activeTab, setGateway, setRuntimeProviderState, t });
	const selectTab = useCallback((tab: ModelGatewayTab) => {
		setActiveTab(tab);
		window.location.hash = `models?tab=${encodeURIComponent(tab)}`;
	}, []);
	useEffect(() => {
		const onHash = () => {
			if (resolveHashRoute() !== 'models') return;
			const tab = splitHash().params.get('tab');
			if (tab && (MODEL_GATEWAY_TABS as string[]).includes(tab)) {
				setActiveTab(tab as ModelGatewayTab);
			}
		};
		window.addEventListener('hashchange', onHash);
		return () => window.removeEventListener('hashchange', onHash);
	}, []);

	const filteredUsage = useMemo(() => {
		const query = usageFilter.trim().toLowerCase();
		if (!query) return gateway.usageLedger;
		return gateway.usageLedger.filter((row) =>
			[
				text(row.providerId),
				text(row.model),
				text(row.role),
				text(row.runtimeType),
				text(row.workflowRunId),
				text(row.agentId),
			]
				.join(' ')
				.toLowerCase()
				.includes(query),
		);
	}, [gateway.usageLedger, usageFilter]);

	const filteredDecisions = useMemo(() => {
		const query = decisionFilter.trim().toLowerCase();
		if (!query) return gateway.routingDecisions;
		return gateway.routingDecisions.filter((row) =>
			[
				text(row.role),
				text(row.taskType),
				text(row.mode),
				text(row.selectedProvider),
				text(row.selectedModel),
				text(row.decisionReason),
			]
				.join(' ')
				.toLowerCase()
				.includes(query),
		);
	}, [decisionFilter, gateway.routingDecisions]);

	const totalCost = sumRecordedCostUsd(overview.costUsage);
	const providerCatalog = useMemo(() => {
		const policyModels = gateway.models;
		const modelsFor = (providerId: string) =>
			Array.from(
				new Set(
					policyModels
						.filter((item) => item.providerId === providerId && item.enabled !== false)
						.map((item) => text(item.model, ''))
						.filter(Boolean),
				),
			);
		return [
			{
				provider: 'ollama',
				models: Array.from(
					new Set([...(runtimeProviderState?.ollama.models ?? []), ...modelsFor('ollama')]),
				),
				remote: false,
			},
			{ provider: 'openai_compatible', models: modelsFor('openai_compatible'), remote: true },
			{ provider: 'openrouter', models: modelsFor('openrouter'), remote: true },
			{ provider: 'openai_agents', models: modelsFor('openai_agents'), remote: true },
		];
	}, [gateway.models, runtimeProviderState]);
	const modelOptions =
		providerCatalog.find((item) => item.provider === policyProvider)?.models ?? [];
	useEffect(() => {
		const currentCatalog = providerCatalog.find((item) => item.provider === policyProvider);
		if (!currentCatalog || currentCatalog.models.length === 0) {
			const firstAvailable = providerCatalog.find((item) => item.models.length > 0);
			if (firstAvailable) {
				setPolicyProvider(firstAvailable.provider);
				setPolicyModel(firstAvailable.models[0] ?? '');
			}
			return;
		}
		if (policyModel && currentCatalog.models.includes(policyModel)) return;
		setPolicyModel(currentCatalog.models[0] ?? '');
	}, [policyModel, policyProvider, providerCatalog]);
	const policyProviderOptions = providerCatalog.filter((item) => item.models.length > 0);
	const policyCatalogReady = tabs.slice('policies').status === 'ready';
	const benchmarkModelOptions = useMemo(() => {
		const models = gateway.models
			.filter((item) => item.providerId === benchmarkProvider)
			.map((item) => text(item.model, ''))
			.filter(Boolean);
		return models.length ? models : [benchmarkModel || 'configured_model'];
	}, [benchmarkModel, benchmarkProvider, gateway.models]);
	const visibleModelPolicies = useMemo(() => {
		const ids = new Set<string>();
		const rows = [...createdPolicies, ...overview.modelPolicies];
		return rows.filter((row) => {
			const id = text(row.id, '');
			if (!id || ids.has(id)) return false;
			ids.add(id);
			return true;
		});
	}, [createdPolicies, overview.modelPolicies]);
	const runtimeRows = runtimeProviderState?.providers ?? [];
	const executableRuntimeCount = runtimeRows.filter((runtime) => runtime.executable).length;
	const unavailableRuntimeCount = runtimeRows.filter((runtime) => !runtime.available).length;
	const runtimeConfigurationRows = gateway.runtimeProviderConfiguration;
	const runtimeConfigurationById = useMemo(() => {
		const map = new Map<string, RuntimeProviderConfiguration>();
		for (const row of runtimeConfigurationRows) {
			const id = text(row.id, '');
			if (id) map.set(id, row);
		}
		return map;
	}, [runtimeConfigurationRows]);
	const missingRuntimeConfigCount = runtimeConfigurationRows.filter(
		(row) => row.configured !== true,
	).length;

	const refreshRuntimeHealth = async (runtime: RuntimeProvider) => {
		const runtimeId = String(runtime.id ?? '');
		const kind = String(runtime.kind ?? '');
		setBusyAction(`${runtimeId}:runtime-health`);
		setError('');
		try {
			if (kind === 'cli') {
				await detectModelGatewayCliRuntime(token, runtimeId);
			} else if (kind === 'api' || kind === 'gateway' || kind === 'local') {
				await healthCheckModelGatewayProvider(token, runtimeId);
			} else {
				throw new Error(
					`Runtime provider ${runtimeId} does not expose an automated healthcheck endpoint.`,
				);
			}
			tabs.refresh('providers');
			await onRefreshRuntimeProviders();
		} catch (actionError) {
			setError(
				actionError instanceof Error
					? actionError.message
					: t(
							'app.modelGateway.error.runtimeHealthRefreshFailed',
							'Runtime healthcheck refresh failed.',
						),
			);
		} finally {
			setBusyAction('');
		}
	};

	const runProviderAction = async (
		providerId: string,
		action: 'toggle' | 'health' | 'discover',
	) => {
		setBusyAction(`${providerId}:${action}`);
		setError('');
		try {
			if (action === 'toggle') {
				const provider = gateway.providers.find((item) => item.providerId === providerId);
				await patchModelGatewayProvider(token, providerId, { enabled: !provider?.enabled });
			}
			if (action === 'health') await healthCheckModelGatewayProvider(token, providerId);
			if (action === 'discover') await discoverModelGatewayProviderModels(token, providerId);
			tabs.refresh('providers');
		} catch (actionError) {
			setError(
				actionError instanceof Error
					? actionError.message
					: t('app.modelGateway.error.providerActionFailed', 'Provider action failed.'),
			);
		} finally {
			setBusyAction('');
		}
	};

	const submitPreview = async () => {
		setBusyAction('route-preview');
		setError('');
		try {
			const result = await previewModelRoute(token, {
				role: previewRole,
				taskType: previewTaskType.trim() || 'task',
				mode: previewMode,
				riskLevel: previewRisk,
				contextTokensEstimate: Number(previewTokens) || 0,
				requiresCodeEdit,
				requiresTools,
				requiresSearch,
				requiresReasoning,
				requiresJson,
				privacyLevel: previewPrivacy,
				budgetRemainingUsd: Number(previewBudget) || 0,
			});
			setPreview(result);
			tabs.refresh('decisions');
		} catch (previewError) {
			setError(
				previewError instanceof Error
					? previewError.message
					: t('app.modelGateway.error.routePreviewFailed', 'Route preview failed.'),
			);
		} finally {
			setBusyAction('');
		}
	};

	const savePolicy = async () => {
		if (!/^[a-z0-9_-]{3,64}$/.test(policyId)) {
			setPolicyError(
				t(
					'ui.static.policy.id.must.use.lowercase.letters.numbers.dashes.or.under.6c250447',
					'Policy id must use lowercase letters, numbers, dashes or underscores.',
				),
			);
			return;
		}
		if (!policyName.trim()) {
			setPolicyError(t('ui.static.policy.name.is.required.9ebaa55e', 'Policy name is required.'));
			return;
		}
		if (!modelOptions.includes(policyModel)) {
			setPolicyError(
				t(
					'ui.static.select.a.model.from.the.catalog.for.the.selected.provider.c2055bb7',
					'Select a model from the catalog for the selected provider.',
				),
			);
			return;
		}
		const maxCost = Number(policyMaxCostUsd);
		const tokenLimit = Number(policyMaxTokens);
		if (!Number.isFinite(maxCost) || maxCost < 0) {
			setPolicyError(
				t(
					'ui.static.maximum.cost.must.be.zero.or.a.positive.number.7e5a838f',
					'Maximum cost must be zero or a positive number.',
				),
			);
			return;
		}
		if (!Number.isInteger(tokenLimit) || tokenLimit < 0 || tokenLimit > 2000000) {
			setPolicyError(
				t(
					'ui.static.maximum.tokens.must.be.an.integer.between.0.and.2000000.35ef8fbb',
					'Maximum tokens must be an integer between 0 and 2000000.',
				),
			);
			return;
		}
		setPolicyError('');
		setBusyAction('save-model-policy');
		try {
			const result = await createModelGatewayRolePolicy(token, {
				id: policyId,
				role: policyId,
				routingProfileId: 'balanced_best_value',
				preferred: [{ provider: policyProvider, model: policyModel }],
				fallback: [],
				maxCostPerTaskUsd: maxCost,
				maxTokensPerRun: tokenLimit,
				allowRemote: policyAllowRemote,
				allowLocal: policyAllowLocal,
				allowCli: false,
				allowApi: true,
			});
			setCreatedPolicies((current) => [result.rolePolicy, ...current]);
		} catch (saveError) {
			setPolicyError(
				saveError instanceof Error
					? saveError.message
					: t('app.modelGateway.error.policySaveFailed', 'Model policy save failed.'),
			);
		} finally {
			setBusyAction('');
		}
	};

	const recordBenchmarkOutcome = async () => {
		const costInput = benchmarkCost.trim();
		const latencyInput = benchmarkLatency.trim();
		const estimatedCost = costInput ? Number(costInput) : undefined;
		const latencyMs = latencyInput ? Number(latencyInput) : undefined;
		if (estimatedCost !== undefined && (!Number.isFinite(estimatedCost) || estimatedCost < 0)) {
			setBenchmarkError(
				t(
					'app.modelGateway.error.benchmarkCostInvalid',
					'Benchmark cost must be blank, zero or positive.',
				),
			);
			return;
		}
		if (latencyMs !== undefined && (!Number.isInteger(latencyMs) || latencyMs < 0)) {
			setBenchmarkError(
				t(
					'app.modelGateway.error.benchmarkLatencyInvalid',
					'Benchmark latency must be blank, zero or a positive integer.',
				),
			);
			return;
		}
		setBenchmarkError('');
		setBusyAction('record-benchmark-outcome');
		try {
			const result = await recordModelGatewayBenchmarkOutcome(token, {
				providerId: benchmarkProvider,
				model: benchmarkModel,
				runtimeType: benchmarkRuntime,
				role: benchmarkRole,
				taskId: 'manual_benchmark_outcome',
				provenance: 'operator_reported',
				success: benchmarkSuccess,
				qaPass: benchmarkQaPass,
				rework: benchmarkRework,
				...(estimatedCost === undefined ? {} : { estimatedCostUsd: estimatedCost }),
				...(latencyMs === undefined ? {} : { latencyMs }),
				metadata: { source: 'operator_console' },
			});
			setGateway((current) => ({
				...current,
				benchmarks: upsertNewestById(current.benchmarks, benchmarkFromOutcome(result.outcome)),
				benchmarkOutcomes: upsertNewestById(current.benchmarkOutcomes, result.outcome),
			}));
			const [benchmarks, outcomes] = await Promise.all([
				getModelGatewayBenchmarks(),
				getModelGatewayBenchmarkOutcomes(),
			]);
			setGateway((current) => ({
				...current,
				benchmarks: benchmarks.benchmarks,
				benchmarkOutcomes: outcomes.outcomes,
			}));
		} catch (saveError) {
			setBenchmarkError(
				saveError instanceof Error
					? saveError.message
					: t('app.modelGateway.error.benchmarkSaveFailed', 'Benchmark outcome save failed.'),
			);
		} finally {
			setBusyAction('');
		}
	};

	const tabItems: { id: ModelGatewayTab; label: string }[] = [
		{ id: 'providers', label: t('app.modelGateway.tabs.providers', 'Providers') },
		{ id: 'catalog', label: t('app.modelGateway.tabs.catalog', 'Catalog') },
		{ id: 'routing', label: t('app.modelGateway.tabs.routing', 'Routing') },
		{ id: 'policies', label: t('app.modelGateway.tabs.policies', 'Policies') },
		{ id: 'budgets', label: t('app.modelGateway.tabs.budgets', 'Budgets') },
		{ id: 'usage', label: t('app.modelGateway.tabs.usage', 'Usage') },
		{ id: 'benchmarks', label: t('app.modelGateway.tabs.benchmarks', 'Benchmarks') },
		{ id: 'decisions', label: t('app.modelGateway.tabs.decisions', 'Decisions') },
		{ id: 'cli', label: t('app.modelGateway.tabs.cli', 'CLI sessions') },
	];
	const tabSlice = tabs.slice(activeTab);

	return (
		<>
			<PageHeader
				kicker={t(
					'ui.static.unified.model.runtime.gateway.95fbf5a6',
					'Unified Model & Runtime Gateway',
				)}
				title={t('app.nav.models', 'Model Gateway')}
				summary={t(
					'ui.static.control.api.model.providers.cli.coding.runtimes.routing.poli.a0fb27d4',
					'Control API model providers, CLI coding runtimes, routing policies, token usage, cost, quota and audit decisions from one local-first console.',
				)}
			/>
			{error ? (
				<div className="form-error" role="alert">
					{error}
				</div>
			) : null}
			<Surface title={t('ui.static.overview.0efc2e6b', 'Overview')}>
				<div className="grid metrics">
					<Metric
						label={t('ui.static.providers.enabled.4cd5c6c6', 'providers enabled')}
						value={gateway.overview.providersEnabled}
					/>
					<Metric
						label={t('ui.static.api.providers.eacba2cf', 'API providers')}
						value={gateway.overview.apiProviders}
					/>
					<Metric
						label={t('ui.static.cli.runtimes.d0947c09', 'CLI runtimes')}
						value={gateway.overview.cliRuntimes}
					/>
					<Metric
						label={t('ui.static.local.providers.64dfa5d6', 'local providers')}
						value={gateway.overview.localProviders}
					/>
					<Metric
						label={t('app.modelGateway.overview.healthy', 'healthy')}
						value={gateway.overview.healthy}
					/>
					<Metric
						label={t('app.modelGateway.overview.degraded', 'degraded')}
						value={gateway.overview.degraded}
					/>
					<Metric
						label={t('app.modelGateway.overview.offline', 'offline')}
						value={gateway.overview.offline}
					/>
					<Metric
						label={t('ui.static.tokens.today.6e3f00fd', 'tokens today')}
						value={gateway.overview.totalTokensToday ?? t('app.runtime.card.unknown', 'unknown')}
					/>
					<Metric
						label={t('ui.static.estimated.cost.today.bc1751b9', 'estimated cost today')}
						value={money(
							gateway.overview.estimatedCostToday,
							t('app.runtime.card.unknown', 'unknown'),
						)}
					/>
					<Metric
						label={t('ui.static.actual.cost.today.e0b8f6a3', 'actual cost today')}
						value={money(
							gateway.overview.actualCostToday,
							t('app.runtime.card.unknown', 'unknown'),
						)}
					/>
					<Metric
						label={t('ui.static.pending.model.approvals.5c592baf', 'pending model approvals')}
						value={gateway.overview.pendingModelApprovals}
					/>
					<Metric
						label={t('ui.static.providers.in.cooldown.e5f4bbe2', 'providers in cooldown')}
						value={gateway.overview.providersInCooldown}
					/>
					<Metric
						label={t('ui.static.missing.runtime.config.3ecf7c92', 'missing runtime config')}
						value={missingRuntimeConfigCount}
					/>
					<Metric
						label={t('ui.static.active.cli.sessions.2afa399c', 'active CLI sessions')}
						value={gateway.overview.activeCliSessions}
					/>
				</div>
			</Surface>

			<Tabs
				className="model-gateway-tabs"
				idBase="model-gateway"
				label={t('app.modelGateway.tablist.aria', 'Model Gateway sections')}
				tabs={tabItems}
				activeTab={activeTab}
				onChange={(id) => selectTab(id as ModelGatewayTab)}
			>
				{tabSlice.status === 'error' ? (
					<ErrorState
						title={t('app.modelGateway.tab.error', 'This section failed to load')}
						body={tabSlice.error}
					/>
				) : null}
				{activeTab === 'providers' ? (
					<>
						<NvidiaNimConfigurationPanel
							providers={gateway.providers}
							token={token}
							onSaved={() => tabs.refresh('providers')}
						/>
						<RuntimeProvidersPanel
							runtimeRows={runtimeRows}
							runtimeConfigurationById={runtimeConfigurationById}
							busyAction={busyAction}
							onRefreshHealth={(runtime) => void refreshRuntimeHealth(runtime)}
						/>
						<ProviderAccountsPanel
							providers={gateway.providers}
							busyAction={busyAction}
							onProviderAction={(providerId, action) => void runProviderAction(providerId, action)}
						/>
						<Surface title={t('app.nav.settings', 'Settings')}>
							<div className="grid three">
								<Metric
									label={t('ui.static.default.routing.mode.b752ab89', 'default routing mode')}
									value="balanced_best_value"
								/>
								<Metric
									label={t('ui.static.real.provider.calls.20f8f3a3', 'real provider calls')}
									value={t('app.modelGateway.settings.disabledByDefault', 'disabled by default')}
								/>
								<Metric
									label={t('ui.static.cli.runtimes.d0947c09', 'CLI runtimes')}
									value={t('app.modelGateway.settings.disabledByDefault', 'disabled by default')}
								/>
								<Metric
									label={t('ui.static.executable.runtimes.f27d567f', 'executable runtimes')}
									value={executableRuntimeCount}
								/>
								<Metric
									label={t('ui.static.unavailable.runtimes.a6f44775', 'unavailable runtimes')}
									value={unavailableRuntimeCount}
								/>
								<Metric
									label={t(
										'ui.static.legacy.model.usage.total.c0e7e1e5',
										'legacy model usage total',
									)}
									value={money(totalCost, t('app.runtime.card.unknown', 'unknown'))}
								/>
							</div>
						</Surface>
					</>
				) : null}
				{activeTab === 'catalog' ? (
					<>
						<NvidiaNimModelManifestPanel
							providers={gateway.providers}
							token={token}
							onSaved={() => tabs.refresh('catalog')}
						/>
						<ModelCatalogPanel models={gateway.models} />
					</>
				) : null}
				{activeTab === 'routing' ? (
					<>
						<ParallelAIExecutionPanel
							projects={overview.projects}
							providers={gateway.providers}
							models={gateway.models}
							token={token}
						/>
						<RoutePreviewPanel
							form={{
								role: previewRole,
								mode: previewMode,
								taskType: previewTaskType,
								risk: previewRisk,
								tokens: previewTokens,
								budget: previewBudget,
								privacy: previewPrivacy,
								requiresCodeEdit,
								requiresTools,
								requiresSearch,
								requiresReasoning,
								requiresJson,
							}}
							preview={preview}
							busyAction={busyAction}
							onChange={(field, value) => {
								if (field === 'role') setPreviewRole(String(value));
								if (field === 'mode') setPreviewMode(String(value));
								if (field === 'taskType') setPreviewTaskType(String(value));
								if (field === 'risk') setPreviewRisk(String(value));
								if (field === 'tokens') setPreviewTokens(String(value));
								if (field === 'budget') setPreviewBudget(String(value));
								if (field === 'privacy') setPreviewPrivacy(String(value));
								if (field === 'requiresCodeEdit') setRequiresCodeEdit(Boolean(value));
								if (field === 'requiresTools') setRequiresTools(Boolean(value));
								if (field === 'requiresSearch') setRequiresSearch(Boolean(value));
								if (field === 'requiresReasoning') setRequiresReasoning(Boolean(value));
								if (field === 'requiresJson') setRequiresJson(Boolean(value));
							}}
							onSubmit={() => void submitPreview()}
						/>
						<RoutingProfilesPanel routingProfiles={gateway.routingProfiles} />
					</>
				) : null}
				{activeTab === 'policies' ? (
					<>
						<Surface
							title={t('ui.static.strict.model.policy.form.b2d420cc', 'Strict model policy form')}
						>
							{!policyCatalogReady ? (
								<EmptyState
									title={t('ui.static.loading.model.catalog.2a1f6d83', 'Loading model catalog')}
									body={t(
										'ui.static.model.policies.can.be.edited.after.backend.catalog.loaded.6450fcb1',
										'Model policies can be edited after the backend catalog is loaded.',
									)}
								/>
							) : policyProviderOptions.length === 0 ? (
								<EmptyState
									title={t(
										'ui.static.model.catalog.unavailable.c0e4b5a7',
										'Model catalog unavailable',
									)}
									body={t(
										'ui.static.no.enabled.model.catalog.entries.available.for.policy.creation.1afeb0fb',
										'No enabled model catalog entries are available for policy creation.',
									)}
								/>
							) : (
								<div className="form-grid">
									<div className="field">
										<label htmlFor="model-policy-id">
											{t('ui.static.policy.id.4d35e204', 'Policy id')}
										</label>
										<input
											id="model-policy-id"
											className="input"
											value={policyId}
											pattern="[a-z0-9_-]{3,64}"
											onChange={(event) => setPolicyId(event.target.value)}
										/>
									</div>
									<div className="field">
										<label htmlFor="model-policy-name">
											{t('ui.static.policy.name.101bf6ea', 'Policy name')}
										</label>
										<input
											id="model-policy-name"
											className="input"
											value={policyName}
											onChange={(event) => setPolicyName(event.target.value)}
										/>
									</div>
									<div className="field">
										<label htmlFor="preferred-provider">
											{t('ui.static.preferred.provider.a21572df', 'Preferred provider')}
										</label>
										<select
											id="preferred-provider"
											className="select"
											value={policyProvider}
											onChange={(event) => {
												const nextProvider = event.target.value;
												const nextCatalog = providerCatalog.find(
													(item) => item.provider === nextProvider,
												);
												setPolicyProvider(nextProvider);
												setPolicyModel(nextCatalog?.models[0] ?? '');
												setPolicyAllowRemote(Boolean(nextCatalog?.remote));
												setPolicyAllowLocal(!nextCatalog?.remote);
											}}
										>
											{policyProviderOptions.map((item) => (
												<option key={item.provider} value={item.provider}>
													{item.provider}
												</option>
											))}
										</select>
									</div>
									<div className="field">
										<label htmlFor="model-catalog">{t('ui.static.model.68c2cc7f', 'Model')}</label>
										<select
											id="model-catalog"
											className="select"
											value={policyModel}
											onChange={(event) => setPolicyModel(event.target.value)}
										>
											{modelOptions.map((item) => (
												<option key={item} value={item}>
													{item}
												</option>
											))}
										</select>
									</div>
									<div className="field">
										<label htmlFor="max-cost-usd">
											{t('ui.static.maximum.cost.usd.03a1d8c3', 'Maximum cost USD')}
										</label>
										<input
											id="max-cost-usd"
											className="input"
											type="number"
											min="0"
											step="0.01"
											value={policyMaxCostUsd}
											onChange={(event) => setPolicyMaxCostUsd(event.target.value)}
										/>
									</div>
									<div className="field">
										<label htmlFor="max-tokens">
											{t('ui.static.maximum.tokens.c7be12de', 'Maximum tokens')}
										</label>
										<input
											id="max-tokens"
											className="input"
											type="number"
											min="0"
											max="2000000"
											step="1"
											value={policyMaxTokens}
											onChange={(event) => setPolicyMaxTokens(event.target.value)}
										/>
									</div>
									<label className="checkbox-row" htmlFor="allow-remote">
										<input
											id="allow-remote"
											type="checkbox"
											checked={policyAllowRemote}
											onChange={(event) => setPolicyAllowRemote(event.target.checked)}
										/>
										{t('app.modelGateway.policy.allowRemote', 'Allow remote providers')}
									</label>
									<label className="checkbox-row" htmlFor="allow-local">
										<input
											id="allow-local"
											type="checkbox"
											checked={policyAllowLocal}
											onChange={(event) => setPolicyAllowLocal(event.target.checked)}
										/>
										{t('app.modelGateway.policy.allowLocal', 'Allow local providers')}
									</label>
									{policyError ? (
										<div className="form-error" role="alert">
											{policyError}
										</div>
									) : null}
									<button
										className="button primary"
										type="button"
										disabled={busyAction === 'save-model-policy'}
										onClick={() => void savePolicy()}
									>
										{t('ui.static.save.model.policy.144bf8c2', 'Save model policy')}
									</button>
								</div>
							)}
						</Surface>
						<Surface title={t('ui.static.model.policies.68e48433', 'Model policies')}>
							<DataTable
								rows={visibleModelPolicies}
								empty={
									<EmptyState
										title={t('ui.static.no.model.policies.993f8301', 'No model policies')}
										body={t(
											'ui.static.model.policies.define.allowed.providers.fallback.chains.and.6b28dbd0',
											'Model policies define allowed providers, fallback chains and budgets.',
										)}
									/>
								}
								columns={[
									{
										key: 'id',
										label: t('ui.static.policy.bb9cf141', 'Policy'),
										render: (row) => <span className="mono">{text(row.id)}</span>,
									},
									{
										key: 'budget',
										label: t('ui.static.budget.7aeba4cd', 'Budget'),
										render: (row) =>
											money(policyBudgetUsd(row), t('app.runtime.card.unknown', 'unknown')),
									},
									{
										key: 'remote',
										label: t('ui.static.remote.c93f6536', 'Remote'),
										render: (row) =>
											row.allowRemote
												? t('app.modelGateway.policy.remoteAllowed', 'allowed')
												: t('app.modelGateway.policy.remoteBlocked', 'blocked'),
									},
								]}
							/>
						</Surface>
						<RoleAssignmentsPanel rolePolicies={gateway.rolePolicies} />
					</>
				) : null}
				{activeTab === 'budgets' ? (
					<>
						<BudgetsPanel budgetRules={gateway.budgetRules} />
						<ProviderLimitsPanel
							providerLimits={gateway.providerLimits}
							providers={gateway.providers}
							token={token}
							onSaved={() => tabs.refresh('budgets')}
						/>
					</>
				) : null}
				{activeTab === 'usage' ? (
					<>
						<UsageLedgerPanel
							rows={filteredUsage}
							filter={usageFilter}
							onFilterChange={setUsageFilter}
						/>
						<Surface title={t('ui.static.model.calls.88e40906', 'Model calls')}>
							<DataTable
								rows={overview.modelCalls}
								empty={
									<EmptyState
										title={t('ui.static.no.model.calls.3e3394fe', 'No model calls')}
										body={t(
											'ui.static.agent.runs.and.model.gateway.preparations.are.recorded.here.26abd766',
											'Agent runs and model gateway preparations are recorded here.',
										)}
									/>
								}
								columns={[
									{
										key: 'provider',
										label: t('ui.static.provider.7ceee3f3', 'Provider'),
										render: (row) => <span className="mono">{String(row.provider ?? '')}</span>,
									},
									{
										key: 'model',
										label: t('ui.static.model.68c2cc7f', 'Model'),
										render: (row) => <span className="mono">{String(row.model ?? '')}</span>,
									},
									{
										key: 'status',
										label: t('ui.static.status.bae7d5be', 'Status'),
										render: (row) => (
											<Badge tone={toneForStatus(String(row.status ?? ''))}>
												{String(row.status ?? '')}
											</Badge>
										),
									},
									{
										key: 'cost',
										label: t('ui.static.cost.64ae43e8', 'Cost'),
										render: (row) => money(row.costUsd, t('app.runtime.card.unknown', 'unknown')),
									},
								]}
							/>
						</Surface>
						<Surface title={t('ui.static.cost.ledger.7af91996', 'Cost history')}>
							<div className="metric-value">
								{money(totalCost, t('app.runtime.card.unknown', 'unknown'))}
							</div>
							<div className="metric-label">
								{t('app.modelGateway.cost.recordedLegacy', 'recorded legacy model usage')}
							</div>
						</Surface>
					</>
				) : null}
				{activeTab === 'benchmarks' ? (
					<BenchmarksPanel
						benchmarks={gateway.benchmarks}
						benchmarkOutcomes={gateway.benchmarkOutcomes}
						providers={gateway.providers}
						models={gateway.models}
						modelOptions={benchmarkModelOptions}
						busyAction={busyAction}
						error={benchmarkError}
						form={{
							provider: benchmarkProvider,
							model: benchmarkModel,
							runtime: benchmarkRuntime,
							role: benchmarkRole,
							success: benchmarkSuccess,
							qaPass: benchmarkQaPass,
							rework: benchmarkRework,
							cost: benchmarkCost,
							latency: benchmarkLatency,
							provenance: 'operator_reported',
						}}
						onProviderChange={(provider, firstModel) => {
							setBenchmarkProvider(provider);
							if (firstModel) setBenchmarkModel(firstModel);
						}}
						onChange={(field, value) => {
							if (field === 'model') setBenchmarkModel(String(value));
							if (field === 'runtime') setBenchmarkRuntime(String(value));
							if (field === 'role') setBenchmarkRole(String(value));
							if (field === 'cost') setBenchmarkCost(String(value));
							if (field === 'latency') setBenchmarkLatency(String(value));
							if (field === 'success') setBenchmarkSuccess(Boolean(value));
							if (field === 'qaPass') setBenchmarkQaPass(Boolean(value));
							if (field === 'rework') setBenchmarkRework(Boolean(value));
						}}
						onSubmit={() => void recordBenchmarkOutcome()}
					/>
				) : null}
				{activeTab === 'decisions' ? (
					<RoutingDecisionsPanel
						rows={filteredDecisions}
						filter={decisionFilter}
						onFilterChange={setDecisionFilter}
					/>
				) : null}
				{activeTab === 'cli' ? (
					<CliSessionsPanel
						cliRuntimes={gateway.cliRuntimes}
						cliSessions={gateway.cliSessions}
						token={token}
					/>
				) : null}
			</Tabs>
		</>
	);
}
