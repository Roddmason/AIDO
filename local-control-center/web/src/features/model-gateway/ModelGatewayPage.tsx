import { useCallback, useEffect, useMemo, useState } from 'react';

import {
	createModelPolicy,
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
	healthCheckModelGatewayProvider,
	patchModelGatewayProvider,
	previewModelRoute,
	recordModelGatewayBenchmarkOutcome,
	type ModelGatewayRoutePreviewResponse,
} from '../../api/client';
import type { Dictionary, Overview, RuntimeProviders } from '../../api/types';
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
	overview: Dictionary;
	providers: Dictionary[];
	models: Dictionary[];
	routingProfiles: Dictionary[];
	rolePolicies: Dictionary[];
	usageLedger: Dictionary[];
	usageSummary: Dictionary;
	routingDecisions: Dictionary[];
	providerLimits: Dictionary[];
	budgetRules: Dictionary[];
	cliRuntimes: Dictionary[];
	cliSessions: Dictionary[];
	benchmarks: Dictionary[];
	benchmarkOutcomes: Dictionary[];
};

const emptyGatewayState: ModelGatewayState = {
	overview: {},
	providers: [],
	models: [],
	routingProfiles: [],
	rolePolicies: [],
	usageLedger: [],
	usageSummary: {},
	routingDecisions: [],
	providerLimits: [],
	budgetRules: [],
	cliRuntimes: [],
	cliSessions: [],
	benchmarks: [],
	benchmarkOutcomes: [],
};

function text(value: unknown, fallback = 'n/a') {
	const result = String(value ?? '').trim();
	return result || fallback;
}

function money(value: unknown) {
	const number = Number(value ?? 0);
	return `$${Number.isFinite(number) ? number.toFixed(4) : '0.0000'}`;
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
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	token: string;
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
	const [policyProvider, setPolicyProvider] = useState('internal_mock');
	const [policyModel, setPolicyModel] = useState('mock');
	const [policyMaxCostUsd, setPolicyMaxCostUsd] = useState('1');
	const [policyMaxTokens, setPolicyMaxTokens] = useState('4000');
	const [policyAllowRemote, setPolicyAllowRemote] = useState(false);
	const [policyAllowLocal, setPolicyAllowLocal] = useState(true);
	const [policyError, setPolicyError] = useState('');
	const [createdPolicies, setCreatedPolicies] = useState<Dictionary[]>([]);
	const [benchmarkProvider, setBenchmarkProvider] = useState('codex_cli');
	const [benchmarkModel, setBenchmarkModel] = useState('gpt-5.5');
	const [benchmarkRuntime, setBenchmarkRuntime] = useState('cli');
	const [benchmarkRole, setBenchmarkRole] = useState('developer');
	const [benchmarkSuccess, setBenchmarkSuccess] = useState(true);
	const [benchmarkQaPass, setBenchmarkQaPass] = useState(true);
	const [benchmarkRework, setBenchmarkRework] = useState(false);
	const [benchmarkCost, setBenchmarkCost] = useState('0.42');
	const [benchmarkLatency, setBenchmarkLatency] = useState('1200');
	const [benchmarkError, setBenchmarkError] = useState('');

	const reload = useCallback(async () => {
		setLoading(true);
		setError('');
		try {
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
			] = await Promise.all([
				getModelGatewayOverview(),
				getModelGatewayProviders(),
				getModelGatewayModels(),
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
			});
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

	const totalCost = overview.costUsage.reduce((sum, row) => sum + Number(row.amountUsd ?? 0), 0);
	const providerCatalog = useMemo(
		() => [
			{ provider: 'internal_mock', models: ['mock'], remote: false },
			{ provider: 'ollama', models: runtimeProviders?.ollama.models ?? [], remote: false },
			{ provider: 'openai_compatible', models: ['catalog/openai-compatible-default'], remote: true },
			{ provider: 'openrouter', models: ['openrouter/auto'], remote: true },
			{ provider: 'openai_agents', models: ['openai-agents/catalog-default'], remote: true },
		],
		[runtimeProviders],
	);
	const modelOptions = providerCatalog.find((item) => item.provider === policyProvider)?.models ?? [];
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
			const result = await createModelPolicy(token, {
				id: policyId,
				name: policyName.trim(),
				preferred: [{ provider: policyProvider, model: policyModel }],
				fallback: [],
				maxCostUsd: maxCost,
				maxTokens: tokenLimit,
				temperature: 0.2,
				allowRemote: policyAllowRemote,
				allowLocal: policyAllowLocal,
			});
			setCreatedPolicies((current) => [result.modelPolicy as unknown as Dictionary, ...current]);
		} catch (saveError) {
			setPolicyError(saveError instanceof Error ? saveError.message : 'Model policy save failed.');
		} finally {
			setBusyAction('');
		}
	};

	const recordBenchmarkOutcome = async () => {
		const estimatedCost = Number(benchmarkCost);
		const latencyMs = Number(benchmarkLatency);
		if (!Number.isFinite(estimatedCost) || estimatedCost < 0) {
			setBenchmarkError('Benchmark cost must be zero or positive.');
			return;
		}
		if (!Number.isInteger(latencyMs) || latencyMs < 0) {
			setBenchmarkError('Benchmark latency must be a positive integer.');
			return;
		}
		setBenchmarkError('');
		setBusyAction('record-benchmark-outcome');
		try {
			await recordModelGatewayBenchmarkOutcome(token, {
				providerId: benchmarkProvider,
				model: benchmarkModel,
				runtimeType: benchmarkRuntime,
				role: benchmarkRole,
				taskId: 'manual_benchmark_outcome',
				success: benchmarkSuccess,
				qaPass: benchmarkQaPass,
				rework: benchmarkRework,
				estimatedCostUsd: estimatedCost,
				latencyMs,
				metadata: { source: 'operator_console' },
			});
			await reload();
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
					<Metric label="active CLI sessions" value={gateway.overview.activeCliSessions} />
				</div>
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
							{providerCatalog.map((item) => (
								<option key={item.provider} value={item.provider} disabled={item.models.length === 0}>{item.provider}</option>
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
			</Surface>

			<Surface title="Model policies">
				<DataTable rows={visibleModelPolicies} empty={<EmptyState title="No model policies" body="Model policies define allowed providers, fallback chains and budgets." />} columns={[
					{ key: 'id', label: 'Policy', render: (row) => <span className="mono">{text(row.id)}</span> },
					{ key: 'budget', label: 'Budget', render: (row) => money(row.maxCostUsd) },
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
					<Metric label="Ollama status" value={runtimeProviders?.ollama.available ? 'ready' : 'optional'} />
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
				<div className="metric-value">${totalCost.toFixed(4)}</div>
				<div className="metric-label">recorded legacy model usage</div>
			</Surface>
		</>
	);
}
