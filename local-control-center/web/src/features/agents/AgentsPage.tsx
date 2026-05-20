import { useState } from 'react';

import { createAgentProfile } from '../../api/client';
import type { AgentRole, AgentRuntimeMode, Overview, PermissionProfile, RuntimeProviders } from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { toneForStatus } from '../../lib/format';

const runtimeModeOptions: AgentRuntimeMode[] = ['api', 'cli', 'ollama', 'hybrid', 'manual', 'internal_mock'];

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
	const [role, setRole] = useState<AgentRole>('implementer');
	const [runtimeMode, setRuntimeMode] = useState<AgentRuntimeMode>('hybrid');
	const [permissionProfile, setPermissionProfile] = useState<PermissionProfile>('dev_safe');
	const [allowedTool, setAllowedTool] = useState('shell');
	const [error, setError] = useState('');
	const modes =
		runtimeProviders?.runtimeModes.filter((mode): mode is AgentRuntimeMode => runtimeModeOptions.includes(mode as AgentRuntimeMode)) ??
		(['internal_mock', 'manual'] satisfies AgentRuntimeMode[]);

	const createProfile = () => {
		if (!/^[a-z0-9_-]{3,64}$/.test(profileId)) {
			setError('Use lowercase letters, numbers, dashes or underscores.');
			return;
		}
		if (!name.trim()) {
			setError('Display name is required.');
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
				permissionProfile,
				allowedTools: [allowedTool],
				maxRuntimeSeconds: runtimeMode === 'manual' ? 300 : 900,
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
							<option value="product_owner">product_owner</option>
							<option value="technical_lead">technical_lead</option>
							<option value="implementer">implementer</option>
							<option value="qa_reviewer">qa_reviewer</option>
							<option value="security_reviewer">security_reviewer</option>
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
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status ?? 'active'}</Badge> },
					]}
				/>
			</Surface>
		</>
	);
}
