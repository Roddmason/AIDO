/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import {
	createModelGatewayRolePolicy,
	detectModelGatewayCliRuntime,
	discoverModelGatewayProviderModels,
	getModelGatewayBudgetRules,
	getModelGatewayBenchmarks,
	getModelGatewayBenchmarkOutcomes,
	getModelGatewayCliRuntimes,
	getModelGatewayCliSessions,
	getModelGatewayModels,
	getModelGatewayOverview,
	getModelGatewayProviderLimits,
	getModelGatewayProviders,
	getModelGatewayRolePolicies,
	getModelGatewayRoutingDecisions,
	getModelGatewayRoutingProfiles,
	getModelGatewayUsageLedger,
	getModelGatewayUsageSummary,
	getRuntimeProviders,
	getRuntimeProviderConfiguration,
	healthCheckModelGatewayProvider,
	patchModelGatewayProvider,
	previewModelRoute,
	recordModelGatewayBenchmarkOutcome,
	type ModelGatewayRoutePreviewResponse,
} from '../../api/client';
import type {
	ModelGatewayBenchmark,
	ModelGatewayBenchmarkOutcome,
	ModelGatewayBudgetRule,
	ModelGatewayCliRuntime,
	ModelGatewayCliSession,
	ModelGatewayModel,
	ModelGatewayOverview,
	ModelGatewayProviderAccount,
	ModelGatewayProviderLimit,
	ModelGatewayRolePolicy,
	ModelGatewayRoutingDecision,
	ModelGatewayRoutingProfile,
	ModelGatewayUsage,
	ModelGatewayUsageSummary,
	Overview,
	RuntimeProvider,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { toneForStatus } from '../../lib/format';
import { BenchmarksPanel } from './BenchmarksPanel';
import { BudgetsPanel } from './BudgetsPanel';
import { CliSessionsPanel } from './CliSessionsPanel';
import { ModelCatalogPanel } from './ModelCatalogPanel';
import { ProviderAccountsPanel } from './ProviderAccountsPanel';
import { ProviderLimitsPanel } from './ProviderLimitsPanel';
import { RoleAssignmentsPanel } from './RoleAssignmentsPanel';
import { RoutePreviewPanel } from './RoutePreviewPanel';
import { RoutingDecisionsPanel } from './RoutingDecisionsPanel';
import { RoutingProfilesPanel } from './RoutingProfilesPanel';
import { UsageLedgerPanel } from './UsageLedgerPanel';

type ModelGatewayState = {
	overview: ModelGatewayOverview;
	providers: ModelGatewayProviderAccount[];
	models: ModelGatewayModel[];
	routingProfiles: ModelGatewayRoutingProfile[];
	rolePolicies: ModelGatewayRolePolicy[];
	usageLedger: ModelGatewayUsage[];
	usageSummary: ModelGatewayUsageSummary | null;
	routingDecisions: ModelGatewayRoutingDecision[];
	providerLimits: ModelGatewayProviderLimit[];
	budgetRules: ModelGatewayBudgetRule[];
	cliRuntimes: ModelGatewayCliRuntime[];
	cliSessions: ModelGatewayCliSession[];
	benchmarks: ModelGatewayBenchmark[];
	benchmarkOutcomes: ModelGatewayBenchmarkOutcome[];
	runtimeProviderConfiguration: RuntimeProviderConfiguration[];
};

const emptyGatewayState: ModelGatewayState = {
	overview: {
		activeCliSessions: 0,
		actualCostToday: 0,
		apiProviders: 0,
		cliRuntimes: 0,
		degraded: 0,
		estimatedCostToday: 0,
		healthy: 0,
		localProviders: 0,
		offline: 0,
		pendingModelApprovals: 0,
		providersEnabled: 0,
		providersInCooldown: 0,
		totalTokensToday: 0,
	},
	providers: [],
	models: [],
	routingProfiles: [],
	rolePolicies: [],
	usageLedger: [],
	usageSummary: null,
	routingDecisions: [],
	providerLimits: [],
	budgetRules: [],
	cliRuntimes: [],
	cliSessions: [],
	benchmarks: [],
	benchmarkOutcomes: [],
	runtimeProviderConfiguration: [],
};

function text(value: unknown, fallback = 'n/a') {
	const result = String(value ?? '').trim();
	return result || fallback;
}

function upsertNewestById<T extends { id: string; updatedAt?: string; createdAt?: string }>(records: T[], incoming: T) {
	const existing = records.find((record) => record.id === incoming.id);
	const incomingTime = Date.parse(String(incoming.updatedAt ?? incoming.createdAt ?? ''));
	const existingTime = Date.parse(String(existing?.updatedAt ?? existing?.createdAt ?? ''));
	if (existing && !Number.isNaN(existingTime) && (Number.isNaN(incomingTime) || existingTime > incomingTime)) return records;
	return existing ? records.map((record) => (record.id === incoming.id ? incoming : record)) : [incoming, ...records];
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
		avgCost: objectiveTasksAttempted ? outcome.actualCostUsd ?? outcome.estimatedCostUsd ?? null : null,
		avgLatencyMs: objectiveTasksAttempted ? outcome.latencyMs ?? null : null,
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

function redactVisibleSecret(value: unknown, fallback = 'n/a') {
	return text(value, fallback)
		.replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, '[redacted_secret]')
		.replace(/\bsk-[A-Za-z0-9_-]{8,}/gi, '[redacted_secret]')
		.replace(/([?&](?:api[_-]?key|token|secret)=)[^&\s]+/gi, '$1[redacted_secret]')
		.replace(/\b(?:api[_-]?key|token|secret)\s*[:=]\s*[^,\s;]+/gi, '[redacted_secret]');
}

function money(value: unknown) {
	if (value === null || value === undefined || value === '') return 'unknown';
	const number = Number(value);
	return Number.isFinite(number) ? `$${number.toFixed(4)}` : 'unknown';
}

function policyBudgetUsd(row: Overview['modelPolicies'][number] | ModelGatewayRolePolicy) {
	return 'maxCostPerTaskUsd' in row ? row.maxCostPerTaskUsd : row.maxCostUsd;
}

function sumRecordedCost(rows: Overview['costUsage']) {
	const amounts = rows.map((row) => Number(row.amountUsd)).filter((amount) => Number.isFinite(amount));
	return amounts.length ? amounts.reduce((sum, amount) => sum + amount, 0) : null;
}

function Metric({ label, value }: { label: string; value: unknown }) {
	return (
		<Surface flat>
			<div className="metric-value">{text(value, '0')}</div>
			<div className="metric-label">{label}</div>
		</Surface>
	);
}

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
	const [gateway, setGateway] = useState<ModelGatewayState>(emptyGatewayState);
	const [loading, setLoading] = useState(true);
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
	const [policyCatalogModels, setPolicyCatalogModels] = useState<ModelGatewayModel[]>([]);
	const [policyCatalogLoaded, setPolicyCatalogLoaded] = useState(false);
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
	const [runtimeProviderState, setRuntimeProviderState] = useState<RuntimeProviders | null>(runtimeProviders);

	useEffect(() => {
		setRuntimeProviderState(runtimeProviders);
	}, [runtimeProviders]);

	const reload = useCallback(async () => {
		setLoading(true);
		setError('');
		try {
			setPolicyCatalogLoaded(false);
			const modelCatalogPromise = getModelGatewayModels();
			void modelCatalogPromise
				.then((payload) => {
					setPolicyCatalogModels(payload.models);
					setPolicyCatalogLoaded(true);
				})
				.catch(() => setPolicyCatalogLoaded(true));
			const [
				gatewayOverview,
				providers,
				models,
				routingProfiles,
				rolePolicies,
				usageLedger,
				usageSummary,
				routingDecisions,
				providerLimits,
				budgetRules,
				cliRuntimes,
				cliSessions,
				benchmarks,
				benchmarkOutcomes,
				runtimeProviderConfiguration,
			] = await Promise.all([
				getModelGatewayOverview(),
				getModelGatewayProviders(),
				modelCatalogPromise,
				getModelGatewayRoutingProfiles(),
				getModelGatewayRolePolicies(),
				getModelGatewayUsageLedger(),
				getModelGatewayUsageSummary(),
				getModelGatewayRoutingDecisions(),
				getModelGatewayProviderLimits(),
				getModelGatewayBudgetRules(),
				getModelGatewayCliRuntimes(),
				getModelGatewayCliSessions(),
				getModelGatewayBenchmarks(),
				getModelGatewayBenchmarkOutcomes(),
				getRuntimeProviderConfiguration(),
			]);
			setGateway({
				overview: gatewayOverview.overview,
				providers: providers.providers,
				models: models.models,
				routingProfiles: routingProfiles.routingProfiles,
				rolePolicies: rolePolicies.rolePolicies,
				usageLedger: usageLedger.usageLedger,
				usageSummary: usageSummary.summary,
				routingDecisions: routingDecisions.routingDecisions,
				providerLimits: providerLimits.providerLimits,
				budgetRules: budgetRules.budgetRules,
				cliRuntimes: cliRuntimes.cliRuntimes,
				cliSessions: cliSessions.cliSessions,
				benchmarks: benchmarks.benchmarks,
				benchmarkOutcomes: benchmarkOutcomes.outcomes,
				runtimeProviderConfiguration: runtimeProviderConfiguration.providers,
			});
			setPolicyCatalogModels(models.models);
			setPolicyCatalogLoaded(true);
			void getRuntimeProviders()
				.then((runtimeProviderStatus) => setRuntimeProviderState(runtimeProviderStatus))
				.catch(() => undefined);
		} catch (loadError) {
			setError(loadError instanceof Error ? loadError.message : 'Model Gateway state failed to load.');
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		void reload();
	}, [reload]);

	const filteredUsage = useMemo(() => {
		const query = usageFilter.trim().toLowerCase();
		if (!query) return gateway.usageLedger;
		return gateway.usageLedger.filter((row) =>
			[text(row.providerId), text(row.model), text(row.role), text(row.runtimeType), text(row.workflowRunId), text(row.agentId)]
				.join(' ')
				.toLowerCase()
				.includes(query),
		);
	}, [gateway.usageLedger, usageFilter]);

	const filteredDecisions = useMemo(() => {
		const query = decisionFilter.trim().toLowerCase();
		if (!query) return gateway.routingDecisions;
		return gateway.routingDecisions.filter((row) =>
			[text(row.role), text(row.taskType), text(row.mode), text(row.selectedProvider), text(row.selectedModel), text(row.decisionReason)]
				.join(' ')
				.toLowerCase()
				.includes(query),
		);
	}, [decisionFilter, gateway.routingDecisions]);

	const totalCost = sumRecordedCost(overview.costUsage);
	const providerCatalog = useMemo(() => {
		const policyModels = policyCatalogLoaded ? policyCatalogModels : gateway.models;
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
			{ provider: 'ollama', models: Array.from(new Set([...(runtimeProviderState?.ollama.models ?? []), ...modelsFor('ollama')])), remote: false },
			{ provider: 'openai_compatible', models: modelsFor('openai_compatible'), remote: true },
			{ provider: 'openrouter', models: modelsFor('openrouter'), remote: true },
			{ provider: 'openai_agents', models: modelsFor('openai_agents'), remote: true },
		];
	}, [gateway.models, policyCatalogLoaded, policyCatalogModels, runtimeProviderState]);
	const modelOptions = providerCatalog.find((item) => item.provider === policyProvider)?.models ?? [];
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
	const policyCatalogReady = policyCatalogLoaded || !loading;
	const benchmarkModelOptions = useMemo(() => {
		const models = gateway.models.filter((item) => item.providerId === benchmarkProvider).map((item) => text(item.model, '')).filter(Boolean);
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
	const missingRuntimeConfigCount = runtimeConfigurationRows.filter((row) => row.configured !== true).length;

	const runtimeStateBadge = (enabled: boolean, positive: string, negative: string) => (
		<Badge tone={enabled ? 'ok' : 'warn'}>{enabled ? positive : negative}</Badge>
	);

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
				throw new Error(`Runtime provider ${runtimeId} does not expose an automated healthcheck endpoint.`);
			}
			await Promise.all([reload(), onRefreshRuntimeProviders()]);
		} catch (actionError) {
			setError(actionError instanceof Error ? actionError.message : 'Runtime healthcheck refresh failed.');
		} finally {
			setBusyAction('');
		}
	};

	const runProviderAction = async (providerId: string, action: 'toggle' | 'health' | 'discover') => {
		setBusyAction(`${providerId}:${action}`);
		setError('');
		try {
			if (action === 'toggle') {
				const provider = gateway.providers.find((item) => item.providerId === providerId);
				await patchModelGatewayProvider(token, providerId, { enabled: !provider?.enabled });
			}
			if (action === 'health') await healthCheckModelGatewayProvider(token, providerId);
			if (action === 'discover') await discoverModelGatewayProviderModels(token, providerId);
			await reload();
		} catch (actionError) {
			setError(actionError instanceof Error ? actionError.message : 'Provider action failed.');
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
			await reload();
		} catch (previewError) {
			setError(previewError instanceof Error ? previewError.message : 'Route preview failed.');
		} finally {
			setBusyAction('');
		}
	};

	const savePolicy = async () => {
		if (!/^[a-z0-9_-]{3,64}$/.test(policyId)) {
			setPolicyError('Policy id must use lowercase letters, numbers, dashes or underscores.');
			return;
		}
		if (!policyName.trim()) {
			setPolicyError('Policy name is required.');
			return;
		}
		if (!modelOptions.includes(policyModel)) {
			setPolicyError('Select a model from the catalog for the selected provider.');
			return;
		}
		const maxCost = Number(policyMaxCostUsd);
		const tokenLimit = Number(policyMaxTokens);
		if (!Number.isFinite(maxCost) || maxCost < 0) {
			setPolicyError('Maximum cost must be zero or a positive number.');
			return;
		}
		if (!Number.isInteger(tokenLimit) || tokenLimit < 512 || tokenLimit > 200000) {
			setPolicyError('Maximum tokens must be an integer between 512 and 200000.');
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
			setPolicyError(saveError instanceof Error ? saveError.message : 'Model policy save failed.');
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
			setBenchmarkError('Benchmark cost must be blank, zero or positive.');
			return;
		}
		if (latencyMs !== undefined && (!Number.isInteger(latencyMs) || latencyMs < 0)) {
			setBenchmarkError('Benchmark latency must be blank, zero or a positive integer.');
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
			setBenchmarkError(saveError instanceof Error ? saveError.message : 'Benchmark outcome save failed.');
		} finally {
			setBusyAction('');
		}
	};

	return (
		<>
			<PageHeader
				kicker="Unified Model & Runtime Gateway"
				title="Model Gateway"
				summary="Control API model providers, CLI coding runtimes, routing policies, token usage, cost, quota and audit decisions from one local-first console."
			/>
			{error ? <div className="form-error" role="alert">{error}</div> : null}
			{loading ? <EmptyState title="Loading Model Gateway" body="Reading provider accounts, routing policies and usage ledger." /> : null}

			<Surface title="Overview">
				<div className="grid metrics">
					<Metric label="providers enabled" value={gateway.overview.providersEnabled} />
					<Metric label="API providers" value={gateway.overview.apiProviders} />
					<Metric label="CLI runtimes" value={gateway.overview.cliRuntimes} />
					<Metric label="local providers" value={gateway.overview.localProviders} />
					<Metric label="healthy" value={gateway.overview.healthy} />
					<Metric label="degraded" value={gateway.overview.degraded} />
					<Metric label="offline" value={gateway.overview.offline} />
					<Metric label="tokens today" value={gateway.overview.totalTokensToday} />
					<Metric label="estimated cost today" value={money(gateway.overview.estimatedCostToday)} />
					<Metric label="actual cost today" value={money(gateway.overview.actualCostToday)} />
					<Metric label="pending model approvals" value={gateway.overview.pendingModelApprovals} />
					<Metric label="providers in cooldown" value={gateway.overview.providersInCooldown} />
					<Metric label="missing runtime config" value={missingRuntimeConfigCount} />
					<Metric label="active CLI sessions" value={gateway.overview.activeCliSessions} />
				</div>
			</Surface>

			<Surface title="Runtime & Model Gateway">
				<h3 className="section-subtitle">Runtime Providers</h3>
				<DataTable rows={runtimeRows} empty={<EmptyState title="No runtime provider status" body="Runtime discovery has not returned provider status records." />} columns={[
					{ key: 'id', label: 'Id', render: (row) => <span className="mono">{row.id}</span> },
					{ key: 'kind', label: 'Kind', render: (row) => <Badge>{row.kind}</Badge> },
					{ key: 'detected', label: 'Detected', render: (row) => runtimeStateBadge(Boolean(row.detected), 'detected', 'not detected') },
					{ key: 'configured', label: 'Configured', render: (row) => runtimeStateBadge(Boolean(row.configured), 'configured', 'not configured') },
					{ key: 'available', label: 'Available', render: (row) => runtimeStateBadge(Boolean(row.available), 'available', 'not available') },
					{ key: 'executable', label: 'Executable', render: (row) => runtimeStateBadge(Boolean(row.executable), 'executable', 'not executable') },
					{
						key: 'healthStatus',
						label: 'Health status',
						render: (row) => (
							<Badge tone={row.healthStatus === 'healthy' ? 'ok' : 'warn'}>{redactVisibleSecret(row.healthStatus, 'unknown')}</Badge>
						),
					},
					{
						key: 'capabilities',
						label: 'Capabilities',
						render: (row) => (
							<div className="inline">
								{row.requiresApproval ? <Badge tone="warn">approval</Badge> : null}
								{row.capabilities?.length ? row.capabilities.map((capability) => <Badge key={capability}>{capability}</Badge>) : <Badge tone="warn">none</Badge>}
							</div>
						),
					},
					{
						key: 'configuration',
						label: 'Configuration',
						render: (row) => {
							const configuration = runtimeConfigurationById.get(String(row.id ?? ''));
							const configured = configuration?.configured === true || row.configured;
							const missingFromConfiguration = Array.isArray(configuration?.missing) ? configuration.missing : [];
							const missingSource = missingFromConfiguration.length || configured ? missingFromConfiguration : row.requiredConfiguration ?? [];
							const missing = missingSource.map((item) => redactVisibleSecret(item)).filter(Boolean);
							const variables = Array.isArray(configuration?.variables) ? configuration.variables : [];
							return (
								<div className="stack">
									<Badge tone={configured ? 'ok' : 'warn'}>{text(configuration?.status, configured ? 'configured' : 'missing_config')}</Badge>
									<div className="inline">
										{missing.length ? missing.map((item) => <Badge key={item} tone="warn">{item}</Badge>) : <Badge tone="ok">none missing</Badge>}
									</div>
									{variables.map((variable) => {
										const name = redactVisibleSecret(variable.name);
										const fingerprint = redactVisibleSecret(variable.fingerprint, '');
										return (
											<div className="inline" key={name}>
												<Badge tone={variable.configured ? 'ok' : 'warn'}>{variable.configured ? 'set' : 'missing'}</Badge>
												<span className="mono">{name}</span>
												{fingerprint ? <span className="mono">{fingerprint}</span> : null}
											</div>
										);
									})}
								</div>
							);
						},
					},
					{ key: 'reason', label: 'Reason', render: (row) => redactVisibleSecret(row.reason) },
					{ key: 'lastError', label: 'Last error', render: (row) => redactVisibleSecret(row.lastError, 'none') },
					{ key: 'version', label: 'Version', render: (row) => <span className="mono">{redactVisibleSecret(row.version)}</span> },
					{ key: 'command', label: 'Detected command', render: (row) => <span className="mono">{redactVisibleSecret(row.detectedCommand)}</span> },
					{
						key: 'health',
						label: 'Healthcheck',
						render: (row) => {
							const runtimeId = String(row.id ?? '');
							const kind = String(row.kind ?? '');
							const canRefresh = kind === 'cli' || kind === 'api' || kind === 'gateway' || kind === 'local';
							const busy = busyAction === `${runtimeId}:runtime-health`;
							return (
								<div className="stack">
									<span className="mono">{redactVisibleSecret(row.healthCheckedAt, 'not checked')}</span>
									<button
										className="button"
										type="button"
										disabled={!canRefresh || busy}
										aria-label={`Refresh healthcheck for ${runtimeId}`}
										onClick={() => void refreshRuntimeHealth(row)}
									>
										{busy ? 'Refreshing healthcheck' : 'Refresh healthcheck'}
									</button>
									{canRefresh ? null : <span className="muted">No automated healthcheck endpoint.</span>}
								</div>
							);
						},
					},
				]} />
			</Surface>

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

			<ProviderAccountsPanel
				providers={gateway.providers}
				busyAction={busyAction}
				onProviderAction={(providerId, action) => void runProviderAction(providerId, action)}
			/>

			<Surface title="Strict model policy form">
				{!policyCatalogReady ? (
					<EmptyState title="Loading model catalog" body="Model policies can be edited after the backend catalog is loaded." />
				) : policyProviderOptions.length === 0 ? (
					<EmptyState title="Model catalog unavailable" body="No enabled model catalog entries are available for policy creation." />
				) : (
				<div className="form-grid">
					<div className="field">
						<label htmlFor="model-policy-id">Policy id</label>
						<input id="model-policy-id" className="input" value={policyId} pattern="[a-z0-9_-]{3,64}" onChange={(event) => setPolicyId(event.target.value)} />
					</div>
					<div className="field">
						<label htmlFor="model-policy-name">Policy name</label>
						<input id="model-policy-name" className="input" value={policyName} onChange={(event) => setPolicyName(event.target.value)} />
					</div>
					<div className="field">
						<label htmlFor="preferred-provider">Preferred provider</label>
						<select
							id="preferred-provider"
							className="select"
							value={policyProvider}
							onChange={(event) => {
								const nextProvider = event.target.value;
								const nextCatalog = providerCatalog.find((item) => item.provider === nextProvider);
								setPolicyProvider(nextProvider);
								setPolicyModel(nextCatalog?.models[0] ?? '');
								setPolicyAllowRemote(Boolean(nextCatalog?.remote));
								setPolicyAllowLocal(!nextCatalog?.remote);
							}}
						>
							{policyProviderOptions.map((item) => (
								<option key={item.provider} value={item.provider}>{item.provider}</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="model-catalog">Model</label>
						<select id="model-catalog" className="select" value={policyModel} onChange={(event) => setPolicyModel(event.target.value)}>
							{modelOptions.map((item) => <option key={item} value={item}>{item}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="max-cost-usd">Maximum cost USD</label>
						<input id="max-cost-usd" className="input" type="number" min="0" step="0.01" value={policyMaxCostUsd} onChange={(event) => setPolicyMaxCostUsd(event.target.value)} />
					</div>
					<div className="field">
						<label htmlFor="max-tokens">Maximum tokens</label>
						<input id="max-tokens" className="input" type="number" min="512" max="200000" step="1" value={policyMaxTokens} onChange={(event) => setPolicyMaxTokens(event.target.value)} />
					</div>
					<label className="checkbox-row" htmlFor="allow-remote">
						<input id="allow-remote" type="checkbox" checked={policyAllowRemote} onChange={(event) => setPolicyAllowRemote(event.target.checked)} />
						Allow remote providers
					</label>
					<label className="checkbox-row" htmlFor="allow-local">
						<input id="allow-local" type="checkbox" checked={policyAllowLocal} onChange={(event) => setPolicyAllowLocal(event.target.checked)} />
						Allow local providers
					</label>
					{policyError ? <div className="form-error" role="alert">{policyError}</div> : null}
					<button className="button primary" type="button" disabled={busyAction === 'save-model-policy'} onClick={() => void savePolicy()}>Save model policy</button>
				</div>
				)}
			</Surface>

			<Surface title="Model policies">
				<DataTable rows={visibleModelPolicies} empty={<EmptyState title="No model policies" body="Model policies define allowed providers, fallback chains and budgets." />} columns={[
					{ key: 'id', label: 'Policy', render: (row) => <span className="mono">{text(row.id)}</span> },
					{ key: 'budget', label: 'Budget', render: (row) => money(policyBudgetUsd(row)) },
					{ key: 'remote', label: 'Remote', render: (row) => row.allowRemote ? 'allowed' : 'blocked' },
				]} />
			</Surface>

			<ModelCatalogPanel models={gateway.models} />

			<RoutingProfilesPanel routingProfiles={gateway.routingProfiles} />

			<RoleAssignmentsPanel rolePolicies={gateway.rolePolicies} />

			<UsageLedgerPanel rows={filteredUsage} filter={usageFilter} onFilterChange={setUsageFilter} />

			<BudgetsPanel budgetRules={gateway.budgetRules} />

			<ProviderLimitsPanel providerLimits={gateway.providerLimits} />

			<RoutingDecisionsPanel rows={filteredDecisions} filter={decisionFilter} onFilterChange={setDecisionFilter} />

			<CliSessionsPanel cliRuntimes={gateway.cliRuntimes} cliSessions={gateway.cliSessions} />

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

			<Surface title="Settings">
				<div className="grid three">
					<Metric label="default routing mode" value="balanced_best_value" />
					<Metric label="real provider calls" value="disabled by default" />
					<Metric label="CLI runtimes" value="disabled by default" />
					<Metric label="executable runtimes" value={executableRuntimeCount} />
					<Metric label="unavailable runtimes" value={unavailableRuntimeCount} />
					<Metric label="legacy model usage total" value={money(totalCost)} />
				</div>
			</Surface>

			<Surface title="Model calls">
				<DataTable rows={overview.modelCalls} empty={<EmptyState title="No model calls" body="Agent runs and model gateway preparations are recorded here." />} columns={[
					{ key: 'provider', label: 'Provider', render: (row) => <span className="mono">{String(row.provider ?? '')}</span> },
					{ key: 'model', label: 'Model', render: (row) => <span className="mono">{String(row.model ?? '')}</span> },
					{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					{ key: 'cost', label: 'Cost', render: (row) => money(row.costUsd) },
				]} />
			</Surface>
			<Surface title="Cost ledger">
				<div className="metric-value">{money(totalCost)}</div>
				<div className="metric-label">recorded legacy model usage</div>
			</Surface>
		</>
	);
}
