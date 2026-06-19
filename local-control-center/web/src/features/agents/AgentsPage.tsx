/**
 * Agents console: defines strict agent profiles (runtime mode, permission profile,
 * routing, model policy, provider/tool allowlists and budget limits) and shows live
 * runtime detection. Profile creation is blocked until the model-gateway catalogs
 * (providers, routing profiles, role policies) and runtime modes all load, so a profile
 * can never reference an option the backend hasn't confirmed exists.
 */
import { useEffect, useMemo, useState } from 'react';

import {
	createAgentProfile,
	getModelGatewayProviders,
	getModelGatewayRolePolicies,
	getModelGatewayRoutingProfiles,
	getRuntimeProviders,
} from '../../api/client';
import type {
	AgentRole,
	AgentRuntimeMode,
	Overview,
	PermissionProfile,
	RuntimeProviders,
} from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { type AsyncError, resolveAsyncError, toAsyncError } from '../../lib/asyncError';
import { toneForStatus } from '../../lib/format';

const runtimeModeOptions: AgentRuntimeMode[] = ['api', 'cli', 'ollama', 'hybrid', 'manual'];
const agentRoles: AgentRole[] = [
	'analyst',
	'product_owner',
	'technical_lead',
	'technical_lead_shadow',
	'developer',
	'backend_engineer',
	'frontend_engineer',
	'implementer',
	'qa',
	'qa_reviewer',
	'security_reviewer',
	'release_manager',
];

function recordTimestamp(record: { updatedAt?: string; createdAt?: string }) {
	const parsed = Date.parse(String(record.updatedAt ?? record.createdAt ?? ''));
	return Number.isNaN(parsed) ? 0 : parsed;
}

function upsertNewestById<T extends { id: string; updatedAt?: string; createdAt?: string }>(
	records: T[],
	incoming: T,
) {
	const existing = records.find((record) => record.id === incoming.id);
	if (existing && recordTimestamp(existing) > recordTimestamp(incoming)) return records;
	return existing
		? records.map((record) => (record.id === incoming.id ? incoming : record))
		: [incoming, ...records];
}

function mergeNewestById<T extends { id: string; updatedAt?: string; createdAt?: string }>(
	current: T[],
	incoming: T[],
) {
	return incoming.reduce((merged, record) => upsertNewestById(merged, record), current);
}

function numericLabel(value: unknown, fallback: string) {
	if (value === null || value === undefined || value === '') return fallback;
	const number = Number(value);
	return Number.isFinite(number) ? String(number) : fallback;
}

function moneyLabel(value: unknown, fallback: string) {
	if (value === null || value === undefined || value === '') return fallback;
	const number = Number(value);
	return Number.isFinite(number) ? `$${number.toFixed(2)}` : fallback;
}

/**
 * Agents route page. Loads runtime providers and the model-gateway catalogs on mount,
 * gates the create form on their availability (`catalogAvailable`), and validates token
 * and approval-threshold bounds before the gated profile write.
 */
export function AgentsPage({
	overview,
	runtimeProviders,
	mutate,
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	mutate: <T>(
		operation: (token: string) => Promise<T>,
		options?: { awaitRefresh?: boolean },
	) => Promise<T>;
}) {
	const { t } = useI18n();
	const [agentProfiles, setAgentProfiles] = useState(overview.agentProfiles);
	const [profileId, setProfileId] = useState('agent-hybrid');
	const [name, setName] = useState('Hybrid Implementer');
	const [role, setRole] = useState<AgentRole>('developer');
	const [runtimeMode, setRuntimeMode] = useState<AgentRuntimeMode>('hybrid');
	const [permissionProfile, setPermissionProfile] = useState<PermissionProfile>('dev_safe');
	const [allowedTool, setAllowedTool] = useState('shell');
	const [routingProfileId, setRoutingProfileId] = useState('balanced_best_value');
	const [roleModelPolicyId, setRoleModelPolicyId] = useState('developer');
	const [allowedProvider, setAllowedProvider] = useState('codex_cli');
	const [allowedRuntime, setAllowedRuntime] = useState('cli');
	const [maxTokensPerRun, setMaxTokensPerRun] = useState('200000');
	const [requiresApprovalOverUsd, setRequiresApprovalOverUsd] = useState('2.00');
	const [allowRemote, setAllowRemote] = useState(true);
	const [allowCli, setAllowCli] = useState(true);
	const [allowApi, setAllowApi] = useState(true);
	const [gatewayCatalog, setGatewayCatalog] = useState<{
		providers: string[];
		routingProfiles: string[];
		rolePolicies: string[];
	}>({ providers: [], routingProfiles: [], rolePolicies: [] });
	const [runtimeProviderState, setRuntimeProviderState] = useState<RuntimeProviders | null>(
		runtimeProviders,
	);
	const [error, setError] = useState('');
	const [runtimeProviderError, setRuntimeProviderError] = useState<AsyncError | null>(null);
	const [gatewayCatalogError, setGatewayCatalogError] = useState<AsyncError | null>(null);
	const runtimeProviderErrorText = resolveAsyncError(runtimeProviderError, t);
	const gatewayCatalogErrorText = resolveAsyncError(gatewayCatalogError, t);
	const [profileBusy, setProfileBusy] = useState(false);
	const modes =
		runtimeProviderState?.runtimeModes.filter((mode): mode is AgentRuntimeMode =>
			runtimeModeOptions.includes(mode as AgentRuntimeMode),
		) ?? [];
	const runtimeOptions = useMemo(() => Array.from(new Set(modes)), [modes]);
	const runtimeRows = runtimeProviderState?.providers ?? [];
	const developerAgent = runtimeProviderState?.developerAgent ?? null;
	const catalogAvailable =
		!gatewayCatalogErrorText &&
		!runtimeProviderErrorText &&
		gatewayCatalog.providers.length > 0 &&
		gatewayCatalog.routingProfiles.length > 0 &&
		gatewayCatalog.rolePolicies.length > 0 &&
		runtimeOptions.length > 0;

	useEffect(() => {
		setAgentProfiles((current) => mergeNewestById(current, overview.agentProfiles));
	}, [overview.agentProfiles]);

	useEffect(() => {
		if (runtimeProviders) setRuntimeProviderState(runtimeProviders);
	}, [runtimeProviders]);

	useEffect(() => {
		const controller = new AbortController();
		getRuntimeProviders(controller.signal)
			.then((providers) => {
				setRuntimeProviderError(null);
				setRuntimeProviderState(providers);
			})
			.catch((loadError) => {
				if (!controller.signal.aborted) {
					setRuntimeProviderError(
						toAsyncError(
							loadError,
							'app.agents.errRuntimeDiscovery',
							'Runtime provider discovery failed.',
						),
					);
				}
			});
		return () => {
			controller.abort();
		};
	}, []);

	useEffect(() => {
		let mounted = true;
		Promise.all([
			getModelGatewayProviders(),
			getModelGatewayRoutingProfiles(),
			getModelGatewayRolePolicies(),
		])
			.then(([providers, profiles, policies]) => {
				if (!mounted) return;
				setGatewayCatalogError(null);
				setGatewayCatalog({
					providers: Array.from(
						new Set(
							providers.providers.map((item) => String(item.providerId ?? '')).filter(Boolean),
						),
					),
					routingProfiles: Array.from(
						new Set(
							profiles.routingProfiles
								.map((item) => String(item.id ?? item.name ?? ''))
								.filter(Boolean),
						),
					),
					rolePolicies: Array.from(
						new Set(
							policies.rolePolicies
								.map((item) => String(item.id ?? item.role ?? ''))
								.filter(Boolean),
						),
					),
				});
			})
			.catch((loadError) => {
				if (!mounted) return;
				setGatewayCatalog({ providers: [], routingProfiles: [], rolePolicies: [] });
				setGatewayCatalogError(
					toAsyncError(
						loadError,
						'app.agents.errGatewayCatalogDiscovery',
						'Model gateway catalog discovery failed.',
					),
				);
			});
		return () => {
			mounted = false;
		};
	}, []);

	const createProfile = async () => {
		if (!/^[a-z0-9_-]{3,64}$/.test(profileId)) {
			setError(
				t(
					'ui.static.use.lowercase.letters.numbers.dashes.or.underscores.da56bfbd',
					'Use lowercase letters, numbers, dashes or underscores.',
				),
			);
			return;
		}
		if (!name.trim()) {
			setError(t('ui.static.display.name.is.required.9802a662', 'Display name is required.'));
			return;
		}
		if (!catalogAvailable) {
			setError(
				t(
					'ui.static.configuration.required.agent.profile.catalogs',
					'configuration_required: Provider, routing, role policy and runtime catalogs must load before saving an agent profile.',
				),
			);
			return;
		}
		const tokenLimit = Number(maxTokensPerRun);
		const approvalThreshold = requiresApprovalOverUsd.trim()
			? Number(requiresApprovalOverUsd)
			: null;
		if (!Number.isInteger(tokenLimit) || tokenLimit < 0 || tokenLimit > 200000) {
			setError(
				t(
					'ui.static.max.tokens.per.run.must.be.an.integer.between.0.and.200000.9abcec39',
					'Max tokens per run must be an integer between 0 and 200000.',
				),
			);
			return;
		}
		if (
			approvalThreshold !== null &&
			(!Number.isFinite(approvalThreshold) || approvalThreshold < 0)
		) {
			setError(
				t(
					'ui.static.approval.threshold.must.be.zero.or.positive.2036fa64',
					'Approval threshold must be zero or positive.',
				),
			);
			return;
		}
		setError('');
		setProfileBusy(true);
		try {
			const result = await mutate(
				(token) =>
					createAgentProfile(token, {
						id: profileId,
						name: name.trim(),
						role,
						runtimeMode,
						modelPolicyId: 'implementation_default',
						routingProfileId,
						roleModelPolicyId,
						allowedProviders: allowedProvider ? [allowedProvider] : [],
						allowedRuntimes: allowedRuntime ? [allowedRuntime] : [],
						permissionProfile,
						allowedTools: [allowedTool],
						maxTokensPerRun: tokenLimit,
						maxRuntimeSeconds: runtimeMode === 'manual' ? 300 : 900,
						allowRemote,
						allowCli,
						allowApi,
						requiresApprovalOverUsd: approvalThreshold,
					}),
				{ awaitRefresh: false },
			);
			setAgentProfiles((current) => upsertNewestById(current, result.agentProfile));
		} catch (saveError) {
			setError(
				saveError instanceof Error
					? saveError.message
					: t('app.agents.errProfileSave', 'Agent profile save failed.'),
			);
		} finally {
			setProfileBusy(false);
		}
	};

	return (
		<>
			<PageHeader
				kicker={t('ui.static.contracts.not.characters.607de7e9', 'Contracts, not characters')}
				title={t('app.nav.agents', 'Agents')}
				summary={t(
					'ui.static.profiles.define.runtime.mode.permission.profile.model.policy.bdde429c',
					'Profiles define runtime mode, permission profile, model policy and output contract. CLI/API/Ollama are choices behind the same governance layer.',
				)}
			/>
			<div className="grid two">
				<Surface title={t('ui.static.create.strict.profile.9798d5ff', 'Create strict profile')}>
					<div className="field">
						<label htmlFor="profile-id">{t('ui.static.profile.id.25961d68', 'Profile id')}</label>
						<input
							id="profile-id"
							className="input"
							value={profileId}
							pattern="[a-z0-9_-]{3,64}"
							onChange={(event) => setProfileId(event.target.value)}
						/>
						<span className="field-help">
							{t(
								'ui.static.stable.id.used.by.workflows.and.audit.records.98754f7e',
								'Stable id used by workflows and audit records.',
							)}
						</span>
					</div>
					<div className="field">
						<label htmlFor="profile-name">
							{t('ui.static.display.name.c7874aaa', 'Display name')}
						</label>
						<input
							id="profile-name"
							className="input"
							value={name}
							onChange={(event) => setName(event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="agent-role">{t('ui.static.role.c3f104d1', 'Role')}</label>
						<select
							id="agent-role"
							className="select"
							value={role}
							onChange={(event) => setRole(event.target.value as AgentRole)}
						>
							{agentRoles.map((agentRole) => (
								<option key={agentRole} value={agentRole}>
									{agentRole}
								</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="routing-profile">
							{t('ui.static.routing.profile.d7fd5dd6', 'Routing profile')}
						</label>
						<select
							id="routing-profile"
							className="select"
							value={routingProfileId}
							disabled={!catalogAvailable}
							onChange={(event) => setRoutingProfileId(event.target.value)}
						>
							{gatewayCatalog.routingProfiles.map((profile) => (
								<option key={profile} value={profile}>
									{profile}
								</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="role-model-policy">
							{t('ui.static.role.model.policy.b581d6ef', 'Role model policy')}
						</label>
						<select
							id="role-model-policy"
							className="select"
							value={roleModelPolicyId}
							disabled={!catalogAvailable}
							onChange={(event) => setRoleModelPolicyId(event.target.value)}
						>
							{gatewayCatalog.rolePolicies.map((policy) => (
								<option key={policy} value={policy}>
									{policy}
								</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="runtime-mode">
							{t('ui.static.runtime.mode.b05d4374', 'Runtime mode')}
						</label>
						<select
							id="runtime-mode"
							className="select"
							value={runtimeMode}
							disabled={!catalogAvailable}
							onChange={(event) => setRuntimeMode(event.target.value as AgentRuntimeMode)}
						>
							{modes.map((mode) => (
								<option key={mode} value={mode}>
									{mode}
								</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="permission-profile">
							{t('ui.static.permission.profile.f3526260', 'Permission profile')}
						</label>
						<select
							id="permission-profile"
							className="select"
							value={permissionProfile}
							onChange={(event) => setPermissionProfile(event.target.value as PermissionProfile)}
						>
							<option value="plan">plan</option>
							<option value="dev_safe">dev_safe</option>
							<option value="qa">qa</option>
							<option value="release">release</option>
						</select>
					</div>
					<div className="field">
						<label htmlFor="allowed-tool">
							{t('ui.static.allowed.tool.bf01285b', 'Allowed tool')}
						</label>
						<select
							id="allowed-tool"
							className="select"
							value={allowedTool}
							onChange={(event) => setAllowedTool(event.target.value)}
						>
							<option value="shell">shell</option>
							<option value="mcp">mcp</option>
							<option value="openhands">openhands</option>
							<option value="swe_agent">swe_agent</option>
							<option value="policy.evaluate">policy.evaluate</option>
							<option value="evidence.create">evidence.create</option>
						</select>
					</div>
					<div className="field">
						<label htmlFor="allowed-provider">
							{t('ui.static.allowed.provider.62f3ef08', 'Allowed provider')}
						</label>
						<select
							id="allowed-provider"
							className="select"
							value={allowedProvider}
							disabled={!catalogAvailable}
							onChange={(event) => setAllowedProvider(event.target.value)}
						>
							{gatewayCatalog.providers.map((provider) => (
								<option key={provider} value={provider}>
									{provider}
								</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="allowed-runtime">
							{t('ui.static.allowed.runtime.1d6c55bd', 'Allowed runtime')}
						</label>
						<select
							id="allowed-runtime"
							className="select"
							value={allowedRuntime}
							disabled={!catalogAvailable}
							onChange={(event) => setAllowedRuntime(event.target.value)}
						>
							{runtimeOptions.map((runtime) => (
								<option key={runtime} value={runtime}>
									{runtime}
								</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="agent-max-tokens">
							{t('ui.static.max.tokens.per.run.0c48d61a', 'Max tokens per run')}
						</label>
						<input
							id="agent-max-tokens"
							className="input"
							type="number"
							min="0"
							max="200000"
							value={maxTokensPerRun}
							onChange={(event) => setMaxTokensPerRun(event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="agent-approval-threshold">
							{t('ui.static.approval.threshold.usd.8086e7e3', 'Approval threshold USD')}
						</label>
						<input
							id="agent-approval-threshold"
							className="input"
							type="number"
							min="0"
							step="0.01"
							value={requiresApprovalOverUsd}
							onChange={(event) => setRequiresApprovalOverUsd(event.target.value)}
						/>
					</div>
					<div className="inline">
						<label className="checkbox-row" htmlFor="agent-allow-remote">
							<input
								id="agent-allow-remote"
								type="checkbox"
								checked={allowRemote}
								onChange={(event) => setAllowRemote(event.target.checked)}
							/>
							{t('ui.static.allow.remote.042d39a7', 'Allow remote')}
						</label>
						<label className="checkbox-row" htmlFor="agent-allow-cli">
							<input
								id="agent-allow-cli"
								type="checkbox"
								checked={allowCli}
								onChange={(event) => setAllowCli(event.target.checked)}
							/>
							{t('ui.static.allow.cli.bfd5afe8', 'Allow CLI')}
						</label>
						<label className="checkbox-row" htmlFor="agent-allow-api">
							<input
								id="agent-allow-api"
								type="checkbox"
								checked={allowApi}
								onChange={(event) => setAllowApi(event.target.checked)}
							/>
							{t('ui.static.allow.api.33a34d74', 'Allow API')}
						</label>
					</div>
					{gatewayCatalogErrorText || runtimeProviderErrorText ? (
						<div className="form-error" role="alert">
							configuration_required: {gatewayCatalogErrorText || runtimeProviderErrorText}
						</div>
					) : null}
					{error ? (
						<div className="form-error" role="alert">
							{error}
						</div>
					) : null}
					<button
						className="button primary"
						disabled={profileBusy || !catalogAvailable}
						onClick={() => {
							void createProfile();
						}}
					>
						{t('ui.static.save.agent.profile.27d8ba89', 'Save agent profile')}
					</button>
				</Surface>
				<Surface title={t('ui.static.runtime.detection.0348713b', 'Runtime detection')}>
					{developerAgent ? (
						<div className="status-strip">
							<div>
								<div className="eyebrow">{t('ui.static.developeragent', 'DeveloperAgent')}</div>
								<div className="inline">
									<Badge tone={developerAgent.executable ? 'ok' : 'warn'}>
										{developerAgent.executable
											? t('app.modelGateway.runtime.executable', 'executable')
											: t('app.modelGateway.runtime.notExecutable', 'not executable')}
									</Badge>
									<Badge>
										{developerAgent.selectedRuntimeId ??
											t('app.modelGateway.routePreview.noRuntime', 'no runtime')}
									</Badge>
								</div>
							</div>
							<div className="stack compact">
								<span className="mono">
									{developerAgent.contract.requiredRuntimeCapabilities.join(', ')}
								</span>
								<span>{developerAgent.reason}</span>
							</div>
						</div>
					) : null}
					<DataTable
						rows={runtimeRows}
						empty={
							<EmptyState
								title={t('ui.static.no.runtime.providers.c1247c5c', 'No runtime providers')}
								body={
									runtimeProviderErrorText ||
									t(
										'app.agents.runtimeProvidersDiscoveryPending',
										'Runtime providers are not executable until provider discovery returns status.',
									)
								}
							/>
						}
						columns={[
							{
								key: 'provider',
								label: t('ui.static.provider.7ceee3f3', 'Provider'),
								render: (row) => <span className="mono">{row.id}</span>,
							},
							{
								key: 'kind',
								label: t('app.workbench.evidence.colKind', 'Kind'),
								render: (row) => <Badge>{row.kind}</Badge>,
							},
							{
								key: 'state',
								label: t('ui.static.state.46a2a41c', 'State'),
								render: (row) => (
									<div className="inline">
										<Badge tone={row.detected ? 'ok' : 'warn'}>
											{row.detected
												? t('app.modelGateway.runtime.detected', 'detected')
												: t('app.modelGateway.runtime.notDetected', 'not detected')}
										</Badge>
										<Badge tone={row.configured ? 'ok' : 'warn'}>
											{row.configured
												? t('app.modelGateway.runtime.configured', 'configured')
												: t('app.modelGateway.runtime.unconfigured', 'unconfigured')}
										</Badge>
										<Badge tone={row.available ? 'ok' : 'warn'}>
											{row.available
												? t('app.modelGateway.runtime.available', 'available')
												: t('app.statusBar.unavailable', 'unavailable')}
										</Badge>
										<Badge tone={row.executable ? 'ok' : 'warn'}>
											{row.executable
												? t('app.modelGateway.runtime.executable', 'executable')
												: t('app.modelGateway.runtime.notExecutable', 'not executable')}
										</Badge>
									</div>
								),
							},
							{
								key: 'capabilities',
								label: t('ui.static.capabilities.ca09c54b', 'Capabilities'),
								render: (row) =>
									row.capabilities?.length
										? row.capabilities.join(', ')
										: t('app.runtime.card.none', 'none'),
							},
							{
								key: 'requiredConfiguration',
								label: t('ui.static.required.config.4e5f80c9', 'Required config'),
								render: (row) =>
									row.requiredConfiguration?.length ? row.requiredConfiguration.join(', ') : 'n/a',
							},
							{
								key: 'reason',
								label: t('ui.static.reason.f219cc06', 'Reason'),
								render: (row) => String(row.reason ?? ''),
							},
						]}
					/>
				</Surface>
			</div>
			<Surface title={t('ui.static.agent.profiles.307157c5', 'Agent profiles')}>
				<DataTable
					rows={agentProfiles}
					empty={
						<EmptyState
							title={t('ui.static.no.agent.profiles.056f31a8', 'No agent profiles')}
							body={t(
								'ui.static.create.a.strict.profile.before.running.real.implementation.w.6b48ff2f',
								'Create a strict profile before running real implementation work.',
							)}
						/>
					}
					columns={[
						{ key: 'name', label: t('ui.static.name.709a2322', 'Name'), render: (row) => row.name },
						{
							key: 'role',
							label: t('ui.static.role.c3f104d1', 'Role'),
							render: (row) => <span className="mono">{row.role}</span>,
						},
						{
							key: 'runtime',
							label: t('ui.static.runtime.c4740e4c', 'Runtime'),
							render: (row) => <Badge>{row.runtimeMode ?? row.runtimeType ?? 'unassigned'}</Badge>,
						},
						{
							key: 'routing',
							label: t('ui.static.routing.7d15dd1b', 'Routing'),
							render: (row) => <span className="mono">{row.routingProfileId ?? 'default'}</span>,
						},
						{
							key: 'providers',
							label: t('ui.static.providers.87b7c08b', 'Providers'),
							render: (row) =>
								Array.isArray(row.allowedProviders) && row.allowedProviders.length
									? row.allowedProviders.join(', ')
									: t('app.agents.providersPolicyDefault', 'policy default'),
						},
						{
							key: 'limits',
							label: t('ui.static.limits.61a0ae3b', 'Limits'),
							render: (row) =>
								`${numericLabel(row.maxTokensPerRun, t('app.runtime.card.unknown', 'unknown'))} tokens / ${moneyLabel(row.requiresApprovalOverUsd ?? row.maxCostPerRun, t('app.runtime.card.unknown', 'unknown'))}`,
						},
						{
							key: 'status',
							label: t('ui.static.status.bae7d5be', 'Status'),
							render: (row) => (
								<Badge tone={toneForStatus(row.status)}>{row.status ?? 'active'}</Badge>
							),
						},
					]}
				/>
			</Surface>
		</>
	);
}
