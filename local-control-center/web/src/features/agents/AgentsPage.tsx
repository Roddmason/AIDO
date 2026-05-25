import { useEffect, useMemo, useState } from 'react';

import { createAgentProfile, getModelGatewayProviders, getModelGatewayRolePolicies, getModelGatewayRoutingProfiles } from '../../api/client';
import type { AgentRole, AgentRuntimeMode, Overview, PermissionProfile, RuntimeProviders } from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { toneForStatus } from '../../lib/format';

const runtimeModeOptions: AgentRuntimeMode[] = ['api', 'cli', 'ollama', 'hybrid', 'manual', 'internal_mock'];
const fallbackRoutingProfiles = ['free_first', 'cost_controlled', 'balanced_best_value', 'max_performance', 'manual_by_profile', 'local_private'];
const fallbackProviders = ['internal_mock', 'nvidia_nim', 'ollama', 'codex_cli', 'claude_code_cli', 'openhands', 'swe_agent', 'manual'];
const fallbackRuntimes = ['api', 'cli', 'local', 'gateway', 'manual', 'codex_cli', 'claude_code_cli', 'openhands', 'swe_agent'];
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

export function AgentsPage({
	overview,
	runtimeProviders,
	mutate,
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	mutate: <T>(operation: (token: string) => Promise<T>) => Promise<T>;
}) {
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
	const [gatewayCatalog, setGatewayCatalog] = useState({ providers: fallbackProviders, routingProfiles: fallbackRoutingProfiles, rolePolicies: ['developer'] });
	const [error, setError] = useState('');
	const modes =
		runtimeProviders?.runtimeModes.filter((mode): mode is AgentRuntimeMode => runtimeModeOptions.includes(mode as AgentRuntimeMode)) ??
		(['internal_mock', 'manual'] satisfies AgentRuntimeMode[]);
	const runtimeOptions = useMemo(() => Array.from(new Set([...modes, ...fallbackRuntimes])), [modes]);

	useEffect(() => {
		let mounted = true;
		Promise.all([getModelGatewayProviders(), getModelGatewayRoutingProfiles(), getModelGatewayRolePolicies()])
			.then(([providers, profiles, policies]) => {
				if (!mounted) return;
				setGatewayCatalog({
					providers: Array.from(new Set([...fallbackProviders, ...providers.providers.map((item) => String(item.providerId ?? '')).filter(Boolean)])),
					routingProfiles: Array.from(new Set([...fallbackRoutingProfiles, ...profiles.routingProfiles.map((item) => String(item.id ?? item.name ?? '')).filter(Boolean)])),
					rolePolicies: Array.from(new Set(['developer', ...policies.rolePolicies.map((item) => String(item.id ?? item.role ?? '')).filter(Boolean)])),
				});
			})
			.catch(() => {
				if (mounted) setGatewayCatalog({ providers: fallbackProviders, routingProfiles: fallbackRoutingProfiles, rolePolicies: ['developer'] });
			});
		return () => {
			mounted = false;
		};
	}, []);

	const createProfile = () => {
		if (!/^[a-z0-9_-]{3,64}$/.test(profileId)) {
			setError('Use lowercase letters, numbers, dashes or underscores.');
			return;
		}
		if (!name.trim()) {
			setError('Display name is required.');
			return;
		}
		const tokenLimit = Number(maxTokensPerRun);
		const approvalThreshold = requiresApprovalOverUsd.trim() ? Number(requiresApprovalOverUsd) : null;
		if (!Number.isInteger(tokenLimit) || tokenLimit < 0 || tokenLimit > 200000) {
			setError('Max tokens per run must be an integer between 0 and 200000.');
			return;
		}
		if (approvalThreshold !== null && (!Number.isFinite(approvalThreshold) || approvalThreshold < 0)) {
			setError('Approval threshold must be zero or positive.');
			return;
		}
		setError('');
		void mutate((token) =>
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
		);
	};

	return (
		<>
			<PageHeader
				kicker="Contracts, not characters"
				title="Agents"
				summary="Profiles define runtime mode, permission profile, model policy and output contract. CLI/API/Ollama are choices behind the same governance layer."
			/>
			<div className="grid two">
				<Surface title="Create strict profile">
					<div className="field">
						<label htmlFor="profile-id">Profile id</label>
						<input
							id="profile-id"
							className="input"
							value={profileId}
							pattern="[a-z0-9_-]{3,64}"
							onChange={(event) => setProfileId(event.target.value)}
						/>
						<span className="field-help">Stable id used by workflows and audit records.</span>
					</div>
					<div className="field">
						<label htmlFor="profile-name">Display name</label>
						<input
							id="profile-name"
							className="input"
							value={name}
							onChange={(event) => setName(event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="agent-role">Role</label>
						<select id="agent-role" className="select" value={role} onChange={(event) => setRole(event.target.value as AgentRole)}>
							{agentRoles.map((agentRole) => <option key={agentRole} value={agentRole}>{agentRole}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="routing-profile">Routing profile</label>
						<select id="routing-profile" className="select" value={routingProfileId} onChange={(event) => setRoutingProfileId(event.target.value)}>
							{gatewayCatalog.routingProfiles.map((profile) => <option key={profile} value={profile}>{profile}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="role-model-policy">Role model policy</label>
						<select id="role-model-policy" className="select" value={roleModelPolicyId} onChange={(event) => setRoleModelPolicyId(event.target.value)}>
							{gatewayCatalog.rolePolicies.map((policy) => <option key={policy} value={policy}>{policy}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="runtime-mode">Runtime mode</label>
						<select id="runtime-mode" className="select" value={runtimeMode} onChange={(event) => setRuntimeMode(event.target.value as AgentRuntimeMode)}>
							{modes.map((mode) => (
								<option key={mode} value={mode}>{mode}</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="permission-profile">Permission profile</label>
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
						<label htmlFor="allowed-tool">Allowed tool</label>
						<select id="allowed-tool" className="select" value={allowedTool} onChange={(event) => setAllowedTool(event.target.value)}>
							<option value="shell">shell</option>
							<option value="mcp">mcp</option>
							<option value="openhands">openhands</option>
							<option value="swe_agent">swe_agent</option>
							<option value="policy.evaluate">policy.evaluate</option>
							<option value="evidence.create">evidence.create</option>
						</select>
					</div>
					<div className="field">
						<label htmlFor="allowed-provider">Allowed provider</label>
						<select id="allowed-provider" className="select" value={allowedProvider} onChange={(event) => setAllowedProvider(event.target.value)}>
							{gatewayCatalog.providers.map((provider) => <option key={provider} value={provider}>{provider}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="allowed-runtime">Allowed runtime</label>
						<select id="allowed-runtime" className="select" value={allowedRuntime} onChange={(event) => setAllowedRuntime(event.target.value)}>
							{runtimeOptions.map((runtime) => <option key={runtime} value={runtime}>{runtime}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="agent-max-tokens">Max tokens per run</label>
						<input id="agent-max-tokens" className="input" type="number" min="0" max="200000" value={maxTokensPerRun} onChange={(event) => setMaxTokensPerRun(event.target.value)} />
					</div>
					<div className="field">
						<label htmlFor="agent-approval-threshold">Approval threshold USD</label>
						<input id="agent-approval-threshold" className="input" type="number" min="0" step="0.01" value={requiresApprovalOverUsd} onChange={(event) => setRequiresApprovalOverUsd(event.target.value)} />
					</div>
					<div className="inline">
						<label className="checkbox-row" htmlFor="agent-allow-remote"><input id="agent-allow-remote" type="checkbox" checked={allowRemote} onChange={(event) => setAllowRemote(event.target.checked)} />Allow remote</label>
						<label className="checkbox-row" htmlFor="agent-allow-cli"><input id="agent-allow-cli" type="checkbox" checked={allowCli} onChange={(event) => setAllowCli(event.target.checked)} />Allow CLI</label>
						<label className="checkbox-row" htmlFor="agent-allow-api"><input id="agent-allow-api" type="checkbox" checked={allowApi} onChange={(event) => setAllowApi(event.target.checked)} />Allow API</label>
					</div>
					{error ? <div className="form-error" role="alert">{error}</div> : null}
					<button className="button primary" onClick={createProfile}>Save agent profile</button>
				</Surface>
				<Surface title="Runtime detection">
					<div className="stack">
						<div className="inline">Ollama <Badge tone={runtimeProviders?.ollama.available ? 'ok' : 'warn'}>{runtimeProviders?.ollama.available ? 'ready' : 'not detected'}</Badge></div>
						<div className="inline">CLI adapters <Badge tone={runtimeProviders?.cli.available ? 'ok' : 'warn'}>{runtimeProviders?.cli.available ? 'available' : 'not detected'}</Badge></div>
						<div className="inline">API adapters <Badge tone="ok">catalogued</Badge></div>
					</div>
				</Surface>
			</div>
			<Surface title="Agent profiles">
				<DataTable
					rows={overview.agentProfiles}
					empty={<EmptyState title="No agent profiles" body="Create a strict profile before running real implementation work." />}
					columns={[
						{ key: 'name', label: 'Name', render: (row) => row.name },
						{ key: 'role', label: 'Role', render: (row) => <span className="mono">{row.role}</span> },
						{ key: 'runtime', label: 'Runtime', render: (row) => <Badge>{row.runtimeMode ?? row.runtimeType ?? 'internal_mock'}</Badge> },
						{ key: 'routing', label: 'Routing', render: (row) => <span className="mono">{row.routingProfileId ?? 'default'}</span> },
						{ key: 'providers', label: 'Providers', render: (row) => Array.isArray(row.allowedProviders) && row.allowedProviders.length ? row.allowedProviders.join(', ') : 'policy default' },
						{ key: 'limits', label: 'Limits', render: (row) => `${row.maxTokensPerRun ?? 0} tokens / $${Number(row.requiresApprovalOverUsd ?? row.maxCostPerRun ?? 0).toFixed(2)}` },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status ?? 'active'}</Badge> },
					]}
				/>
			</Surface>
		</>
	);
}
