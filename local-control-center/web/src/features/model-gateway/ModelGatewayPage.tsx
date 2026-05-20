import { useCallback, useEffect, useMemo, useState } from 'react';

import {
	createModelPolicy,
	discoverModelGatewayProviderModels,
	getModelGatewayBudgetRules,
	getModelGatewayBenchmarks,
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
	type ModelGatewayRoutePreviewResponse,
} from '../../api/client';
import type { Dictionary, Overview, RuntimeProviders } from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { toneForStatus } from '../../lib/format';

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
};

function text(value: unknown, fallback = 'n/a') {
	const result = String(value ?? '').trim();
	return result || fallback;
}

function boolLabel(value: unknown) {
	return value ? 'yes' : 'no';
}

function money(value: unknown) {
	const number = Number(value ?? 0);
	return `$${Number.isFinite(number) ? number.toFixed(4) : '0.0000'}`;
}

function listLabel(value: unknown) {
	return Array.isArray(value) ? value.map((item) => String(item)).join(', ') || 'none' : text(value, 'none');
}

function SecretSafeValue({ value }: { value: unknown }) {
	const rendered = text(value, 'not configured');
	const unsafe = /sk-[A-Za-z0-9_-]+|Bearer\s+/i.test(rendered);
	return <span className="mono">{unsafe ? '[redacted]' : rendered}</span>;
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

			<Surface title="Route Preview">
				<div className="form-grid">
					<div className="grid three">
						<div className="field">
							<label htmlFor="route-preview-role">Preview role</label>
							<select id="route-preview-role" className="select" value={previewRole} onChange={(event) => setPreviewRole(event.target.value)}>
								{['analyst', 'product_owner', 'technical_lead', 'developer', 'qa', 'security_reviewer', 'release_manager'].map((role) => (
									<option key={role} value={role}>{role}</option>
								))}
							</select>
						</div>
						<div className="field">
							<label htmlFor="route-preview-mode">Preview mode</label>
							<select id="route-preview-mode" className="select" value={previewMode} onChange={(event) => setPreviewMode(event.target.value)}>
								{['free_first', 'cost_controlled', 'balanced_best_value', 'max_performance', 'manual_by_profile', 'local_private'].map((mode) => (
									<option key={mode} value={mode}>{mode}</option>
								))}
							</select>
						</div>
						<div className="field">
							<label htmlFor="route-preview-task-type">Preview task type</label>
							<input id="route-preview-task-type" className="input" value={previewTaskType} onChange={(event) => setPreviewTaskType(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="route-preview-risk">Preview risk level</label>
							<select id="route-preview-risk" className="select" value={previewRisk} onChange={(event) => setPreviewRisk(event.target.value)}>
								{['low', 'medium', 'high', 'critical'].map((risk) => <option key={risk} value={risk}>{risk}</option>)}
							</select>
						</div>
						<div className="field">
							<label htmlFor="route-preview-context">Preview context tokens</label>
							<input id="route-preview-context" className="input" type="number" min="0" value={previewTokens} onChange={(event) => setPreviewTokens(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="route-preview-budget">Preview budget remaining USD</label>
							<input id="route-preview-budget" className="input" type="number" min="0" step="0.01" value={previewBudget} onChange={(event) => setPreviewBudget(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="route-preview-privacy">Preview privacy level</label>
							<select id="route-preview-privacy" className="select" value={previewPrivacy} onChange={(event) => setPreviewPrivacy(event.target.value)}>
								<option value="remote_allowed">remote_allowed</option>
								<option value="sensitive">sensitive</option>
								<option value="local_private">local_private</option>
							</select>
						</div>
					</div>
					<div className="inline">
						<label className="checkbox-row" htmlFor="route-preview-code-edit"><input id="route-preview-code-edit" type="checkbox" checked={requiresCodeEdit} onChange={(event) => setRequiresCodeEdit(event.target.checked)} />Preview requires code edit</label>
						<label className="checkbox-row" htmlFor="route-preview-tools"><input id="route-preview-tools" type="checkbox" checked={requiresTools} onChange={(event) => setRequiresTools(event.target.checked)} />Preview requires tools</label>
						<label className="checkbox-row" htmlFor="route-preview-search"><input id="route-preview-search" type="checkbox" checked={requiresSearch} onChange={(event) => setRequiresSearch(event.target.checked)} />Preview requires search</label>
						<label className="checkbox-row" htmlFor="route-preview-reasoning"><input id="route-preview-reasoning" type="checkbox" checked={requiresReasoning} onChange={(event) => setRequiresReasoning(event.target.checked)} />Preview requires reasoning</label>
						<label className="checkbox-row" htmlFor="route-preview-json"><input id="route-preview-json" type="checkbox" checked={requiresJson} onChange={(event) => setRequiresJson(event.target.checked)} />Preview requires JSON</label>
					</div>
					<button className="button primary" type="button" disabled={busyAction === 'route-preview'} onClick={() => void submitPreview()}>
						Preview route
					</button>
					{preview ? (
						<div className="stack" aria-live="polite">
							<h3 className="surface-title">Selected route</h3>
							<div className="inline">
								<Badge tone={preview.selected ? 'ok' : 'warn'}>{preview.selected?.provider ?? 'none'}</Badge>
								<Badge>{preview.selected?.model ?? 'no model'}</Badge>
								<Badge>{preview.selected?.runtime ?? 'no runtime'}</Badge>
								<Badge>{preview.selected?.effort ?? 'default effort'}</Badge>
								<Badge>{money(preview.estimatedCostUsd)}</Badge>
							</div>
							<div className="muted">{preview.decisionReason}</div>
						</div>
					) : null}
				</div>
			</Surface>

			<Surface title="Provider Accounts">
				<DataTable rows={gateway.providers} empty={<EmptyState title="No provider accounts" body="Provider accounts seed during startup." />} columns={[
					{ key: 'provider', label: 'Provider', render: (row) => <span className="mono">{text(row.providerId)}</span> },
					{ key: 'type', label: 'Type', render: (row) => text(row.providerType) },
					{ key: 'format', label: 'API format', render: (row) => text(row.apiFormat) },
					{ key: 'base', label: 'Base URL', render: (row) => <SecretSafeValue value={row.baseUrl} /> },
					{ key: 'credential', label: 'Credential', render: (row) => <div className="stack"><Badge tone={row.credentialStatus === 'configured' ? 'ok' : 'warn'}>{text(row.credentialStatus)}</Badge><SecretSafeValue value={row.credentialRef} /></div> },
					{ key: 'enabled', label: 'Enabled', render: (row) => <Badge tone={row.enabled ? 'ok' : 'warn'}>{boolLabel(row.enabled)}</Badge> },
					{ key: 'quota', label: 'Quota mode', render: (row) => text(row.quotaMode) },
					{ key: 'health', label: 'Health', render: (row) => <Badge tone={toneForStatus(text(row.healthStatus))}>{text(row.healthStatus)}</Badge> },
					{ key: 'last', label: 'Last check', render: (row) => text(row.lastHealthCheckAt) },
					{ key: 'error', label: 'Last error', render: (row) => <SecretSafeValue value={row.lastError} /> },
					{ key: 'cost', label: 'Cost today', render: () => money(0) },
					{ key: 'actions', label: 'Actions', render: (row) => {
						const providerId = text(row.providerId, '');
						return (
							<div className="inline">
								<button className="button" type="button" disabled={busyAction === `${providerId}:toggle`} onClick={() => void runProviderAction(providerId, 'toggle')}>{row.enabled ? 'Disable' : 'Enable'}</button>
								<button className="button" type="button" disabled={busyAction === `${providerId}:health`} onClick={() => void runProviderAction(providerId, 'health')}>Health check</button>
								<button className="button" type="button" disabled={busyAction === `${providerId}:discover`} onClick={() => void runProviderAction(providerId, 'discover')}>Discover models</button>
							</div>
						);
					} },
				]} />
			</Surface>

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

			<Surface title="Model Catalog">
				<DataTable rows={gateway.models} empty={<EmptyState title="No models" body="Model catalog entries appear after seeds or provider discovery." />} columns={[
					{ key: 'provider', label: 'Provider', render: (row) => <span className="mono">{text(row.providerId)}</span> },
					{ key: 'model', label: 'Model', render: (row) => <span className="mono">{text(row.model)}</span> },
					{ key: 'family', label: 'Family', render: (row) => text(row.modelFamily) },
					{ key: 'context', label: 'Context', render: (row) => text(row.contextWindow) },
					{ key: 'tools', label: 'Tools', render: (row) => boolLabel(row.supportsTools) },
					{ key: 'json', label: 'JSON', render: (row) => boolLabel(row.supportsJson) },
					{ key: 'vision', label: 'Vision', render: (row) => boolLabel(row.supportsVision) },
					{ key: 'reasoning', label: 'Reasoning', render: (row) => boolLabel(row.supportsReasoning) },
					{ key: 'effort', label: 'Effort levels', render: (row) => listLabel(row.effortLevels) },
					{ key: 'input', label: 'Input price', render: (row) => text(row.inputPricePerMtok, 'unknown') },
					{ key: 'output', label: 'Output price', render: (row) => text(row.outputPricePerMtok, 'unknown') },
					{ key: 'reasoningPrice', label: 'Reasoning price', render: (row) => text(row.reasoningPricePerMtok, 'unknown') },
					{ key: 'free', label: 'Free tier', render: (row) => boolLabel(row.freeTier) },
					{ key: 'enabled', label: 'Enabled', render: (row) => <Badge tone={row.enabled ? 'ok' : 'warn'}>{boolLabel(row.enabled)}</Badge> },
					{ key: 'source', label: 'Source', render: (row) => text(row.source) },
				]} />
			</Surface>

			<Surface title="Routing Profiles">
				<DataTable rows={gateway.routingProfiles} empty={<EmptyState title="No routing profiles" body="Routing modes seed during startup." />} columns={[
					{ key: 'name', label: 'Profile', render: (row) => <span className="mono">{text(row.name)}</span> },
					{ key: 'mode', label: 'Mode', render: (row) => text(row.mode) },
					{ key: 'objective', label: 'Objective', render: (row) => text(row.objective) },
					{ key: 'rules', label: 'Rules', render: (row) => <span className="mono">{JSON.stringify(row.rules ?? {})}</span> },
					{ key: 'enabled', label: 'Enabled', render: (row) => boolLabel(row.enabled) },
				]} />
			</Surface>

			<Surface title="Role Assignments">
				<DataTable rows={gateway.rolePolicies} empty={<EmptyState title="No role policies" body="Role routing policies seed during startup." />} columns={[
					{ key: 'role', label: 'Role', render: (row) => <span className="mono">{text(row.role)}</span> },
					{ key: 'mode', label: 'Mode', render: (row) => text(row.routingProfileId) },
					{ key: 'primary', label: 'Primary provider/model/runtime', render: (row) => listLabel(row.preferred) },
					{ key: 'fallbacks', label: 'Fallbacks', render: (row) => listLabel(row.fallback) },
					{ key: 'maxCost', label: 'Max cost/task', render: (row) => money(row.maxCostPerTaskUsd) },
					{ key: 'maxTokens', label: 'Max tokens/run', render: (row) => text(row.maxTokensPerRun) },
					{ key: 'remote', label: 'Remote allowed', render: (row) => boolLabel(row.allowRemote) },
					{ key: 'cli', label: 'CLI allowed', render: (row) => boolLabel(row.allowCli) },
					{ key: 'api', label: 'API allowed', render: (row) => boolLabel(row.allowApi) },
					{ key: 'thinking', label: 'Thinking/effort', render: (row) => row.requiresApprovalForReasoningMax ? 'max requires approval' : 'profile default' },
					{ key: 'approval', label: 'Approval threshold', render: (row) => money(row.requiresApprovalOverUsd ?? row.maxCostPerTaskUsd) },
				]} />
			</Surface>

			<Surface title="Usage Ledger">
				<div className="field">
					<label htmlFor="usage-ledger-filter">Usage ledger filter</label>
					<input id="usage-ledger-filter" className="input" value={usageFilter} onChange={(event) => setUsageFilter(event.target.value)} placeholder="Filter provider, model, role, runtime, workflow or agent" />
				</div>
				<DataTable rows={filteredUsage} empty={<EmptyState title="No usage ledger entries" body="Mock and real calls record token and cost usage here." />} columns={[
					{ key: 'time', label: 'Timestamp', render: (row) => text(row.createdAt) },
					{ key: 'provider', label: 'Provider', render: (row) => text(row.providerId) },
					{ key: 'model', label: 'Model', render: (row) => text(row.model) },
					{ key: 'runtime', label: 'Runtime', render: (row) => text(row.runtimeType) },
					{ key: 'role', label: 'Role', render: (row) => text(row.role) },
					{ key: 'agent', label: 'Agent', render: (row) => text(row.agentId) },
					{ key: 'workflow', label: 'Workflow', render: (row) => text(row.workflowRunId) },
					{ key: 'task', label: 'Task', render: (row) => text(row.taskId) },
					{ key: 'input', label: 'Input tokens', render: (row) => text(row.inputTokens, '0') },
					{ key: 'cached', label: 'Cached input', render: (row) => text(row.cachedInputTokens, '0') },
					{ key: 'output', label: 'Output tokens', render: (row) => text(row.outputTokens, '0') },
					{ key: 'reasoning', label: 'Reasoning tokens', render: (row) => text(row.reasoningTokens, '0') },
					{ key: 'tool', label: 'Tool tokens', render: (row) => text(row.toolTokens, '0') },
					{ key: 'total', label: 'Total tokens', render: (row) => text(row.totalTokens, '0') },
					{ key: 'est', label: 'Estimated cost', render: (row) => money(row.estimatedCostUsd) },
					{ key: 'actual', label: 'Actual cost', render: (row) => row.actualCostUsd === null || row.actualCostUsd === undefined ? 'unknown' : money(row.actualCostUsd) },
					{ key: 'latency', label: 'Latency', render: (row) => text(row.latencyMs) },
					{ key: 'source', label: 'Usage source', render: (row) => {
						const raw = row.rawUsage as Dictionary | undefined;
						return text(raw?.usage_source, 'unknown');
					} },
				]} />
			</Surface>

			<Surface title="Budgets">
				<DataTable rows={gateway.budgetRules} empty={<EmptyState title="No budget rules" body="Budget rules can be added for global, role, provider, project and workflow scopes." />} columns={[
					{ key: 'scope', label: 'Scope', render: (row) => `${text(row.scopeType)}:${text(row.scopeId, '*')}` },
					{ key: 'cost', label: 'Max cost', render: (row) => money(row.maxCostUsd) },
					{ key: 'tokens', label: 'Max tokens', render: (row) => text(row.maxTokens, 'none') },
					{ key: 'period', label: 'Period', render: (row) => text(row.period) },
					{ key: 'action', label: 'Action on exceed', render: (row) => text(row.actionOnExceed) },
					{ key: 'enabled', label: 'Enabled', render: (row) => boolLabel(row.enabled) },
				]} />
			</Surface>

			<Surface title="Provider Limits">
				<DataTable rows={gateway.providerLimits} empty={<EmptyState title="No provider limits" body="Provider limit records appear after seeds or rate-limit events." />} columns={[
					{ key: 'provider', label: 'Provider', render: (row) => text(row.providerId) },
					{ key: 'model', label: 'Model', render: (row) => text(row.model) },
					{ key: 'rpm', label: 'RPM', render: (row) => text(row.rpm) },
					{ key: 'tpm', label: 'TPM', render: (row) => text(row.tpm) },
					{ key: 'dailyRequests', label: 'Daily requests', render: (row) => text(row.dailyRequests) },
					{ key: 'dailyTokens', label: 'Daily tokens', render: (row) => text(row.dailyTokens) },
					{ key: 'monthlyBudget', label: 'Monthly budget', render: (row) => money(row.monthlyBudgetUsd) },
					{ key: 'cooldown', label: 'Cooldown', render: (row) => text(row.cooldownUntil) },
					{ key: 'last429', label: 'Last 429', render: (row) => text(row.last429At) },
					{ key: 'strategy', label: 'Unknown limit strategy', render: (row) => text(row.unknownLimitStrategy) },
				]} />
			</Surface>

			<Surface title="Routing Decisions">
				<div className="field">
					<label htmlFor="routing-decision-filter">Routing decision filter</label>
					<input id="routing-decision-filter" className="input" value={decisionFilter} onChange={(event) => setDecisionFilter(event.target.value)} placeholder="Filter role, task, mode, provider, model or reason" />
				</div>
				<DataTable rows={filteredDecisions} empty={<EmptyState title="No routing decisions" body="Route preview and execution decisions are audited here." />} columns={[
					{ key: 'time', label: 'Time', render: (row) => text(row.createdAt) },
					{ key: 'role', label: 'Role', render: (row) => text(row.role) },
					{ key: 'task', label: 'Task type', render: (row) => text(row.taskType) },
					{ key: 'mode', label: 'Mode', render: (row) => text(row.mode) },
					{ key: 'provider', label: 'Selected provider', render: (row) => text(row.selectedProvider) },
					{ key: 'model', label: 'Selected model', render: (row) => text(row.selectedModel) },
					{ key: 'runtime', label: 'Selected runtime', render: (row) => text(row.selectedRuntime) },
					{ key: 'effort', label: 'Effort', render: (row) => text(row.selectedEffort) },
					{ key: 'cost', label: 'Estimated cost', render: (row) => money(row.estimatedCostUsd) },
					{ key: 'reason', label: 'Reason', render: (row) => text(row.decisionReason) },
				]} />
			</Surface>

			<Surface title="CLI Sessions">
				<div className="grid two">
					<DataTable rows={gateway.cliRuntimes} empty={<EmptyState title="No CLI runtimes" body="Codex, Claude Code, OpenHands and SWE-agent adapters are optional." />} columns={[
						{ key: 'runtime', label: 'Runtime', render: (row) => text(row.runtime) },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(text(row.status))}>{text(row.status)}</Badge> },
						{ key: 'executable', label: 'Executable', render: (row) => <SecretSafeValue value={row.executable} /> },
						{ key: 'message', label: 'Message', render: (row) => text(row.message) },
					]} />
					<DataTable rows={gateway.cliSessions} empty={<EmptyState title="No CLI sessions" body="CLI session records appear only after policy-approved runtime execution." />} columns={[
						{ key: 'runtime', label: 'Runtime', render: (row) => text(row.runtime) },
						{ key: 'workspace', label: 'Workspace', render: (row) => text(row.workspaceId) },
						{ key: 'agent', label: 'Agent', render: (row) => text(row.agentId) },
						{ key: 'workflow', label: 'Workflow', render: (row) => text(row.workflowRunId) },
						{ key: 'status', label: 'Status', render: (row) => text(row.status) },
						{ key: 'command', label: 'Command summary', render: (row) => <SecretSafeValue value={Array.isArray(row.command) ? row.command.join(' ') : row.command} /> },
						{ key: 'started', label: 'Started', render: (row) => text(row.startedAt) },
						{ key: 'finished', label: 'Finished', render: (row) => text(row.finishedAt) },
						{ key: 'usage', label: 'Usage', render: (row) => text(row.usageLedgerId) },
						{ key: 'artifacts', label: 'Artifacts', render: (row) => listLabel([row.stdoutArtifactId, row.stderrArtifactId, row.logsArtifactId].filter(Boolean)) },
						{ key: 'error', label: 'Error', render: (row) => <SecretSafeValue value={row.error} /> },
					]} />
				</div>
			</Surface>

			<Surface title="Benchmarks">
				<DataTable rows={gateway.benchmarks} empty={<EmptyState title="insufficient data" body="Benchmarks require repeated task outcomes before success rate, QA pass rate, cost, latency or rework rate can be shown." />} columns={[
					{ key: 'provider', label: 'Provider', render: (row) => text(row.providerId) },
					{ key: 'model', label: 'Model', render: (row) => text(row.model) },
					{ key: 'role', label: 'Role', render: (row) => text(row.role) },
					{ key: 'attempts', label: 'Tasks attempted', render: (row) => text(row.tasksAttempted, '0') },
					{ key: 'success', label: 'Success rate', render: (row) => row.successRate === null || row.successRate === undefined ? 'insufficient data' : `${Number(row.successRate).toFixed(2)}%` },
					{ key: 'qa', label: 'QA pass rate', render: (row) => row.qaPassRate === null || row.qaPassRate === undefined ? 'insufficient data' : `${Number(row.qaPassRate).toFixed(2)}%` },
					{ key: 'cost', label: 'Avg cost', render: (row) => row.avgCost === null || row.avgCost === undefined ? 'unknown' : money(row.avgCost) },
					{ key: 'latency', label: 'Avg latency', render: (row) => text(row.avgLatencyMs) },
					{ key: 'rework', label: 'Rework rate', render: (row) => row.reworkRate === null || row.reworkRate === undefined ? 'insufficient data' : `${Number(row.reworkRate).toFixed(2)}%` },
					{ key: 'last', label: 'Last used', render: (row) => text(row.lastUsedAt) },
				]} />
			</Surface>

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
