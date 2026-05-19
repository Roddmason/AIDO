import { useMemo, useState } from 'react';

import { createModelPolicy, createWorkflow } from '../api/client';
import type { Overview, RuntimeProviders } from '../api/types';
import { Badge, DataTable, EmptyState, PageHeader, Surface } from '../components/primitives';
import { toneForStatus } from '../lib/format';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;

export function CommandCenterPage({ overview, mutate }: { overview: Overview; mutate: Mutate }) {
	const project = overview.projects[0];
	return (
		<>
			<PageHeader kicker="Operator lane" title="Command Center" summary="Start safe SDLC workflows and inspect pending human decisions without bypassing policy." />
			<Surface title="Workflow intake">
				<div className="inline">
					<button
						className="button primary"
						disabled={!project}
						onClick={() => project && void mutate((token) => createWorkflow(token, project.id, `AIDO workflow ${new Date().toISOString()}`))}
					>
						Create workflow
					</button>
					<Badge>{project ? project.name : 'no project'}</Badge>
				</div>
			</Surface>
			<Surface title="Critical queue">
				<DataTable
					rows={overview.actionRequests.filter((item) => item.status === 'pending')}
					empty={<EmptyState title="No pending commands" body="Risky actions will stop here until a human records a reason." />}
					columns={[
						{ key: 'action', label: 'Action', render: (row) => <span className="mono">{row.actionType}</span> },
						{ key: 'risk', label: 'Risk', render: (row) => <Badge tone={toneForStatus(row.riskLevel)}>{row.riskLevel}</Badge> },
						{ key: 'command', label: 'Command', render: (row) => <span className="mono">{row.command || 'n/a'}</span> },
					]}
				/>
			</Surface>
		</>
	);
}

export function WorkspacesPage({ overview }: { overview: Overview }) {
	return (
		<>
			<PageHeader kicker="Isolation" title="Workspaces" summary="Task-owned workspace allocations prevent agents from sharing one mutable working tree." />
			<div className="grid two">
				<Surface title="Projects">
					<DataTable rows={overview.projects} empty={<EmptyState title="No projects" body="The runtime project is created automatically at startup." />} columns={[
						{ key: 'name', label: 'Name', render: (row) => row.name },
						{ key: 'path', label: 'Path', render: (row) => <span className="mono">{row.path}</span> },
					]} />
				</Surface>
				<Surface title="Allocated workspaces">
					<DataTable rows={overview.runtimeWorkspaces} empty={<EmptyState title="No isolated workspaces" body="Workflow implementation steps will allocate workspaces." />} columns={[
						{ key: 'task', label: 'Task', render: (row) => <span className="mono">{String(row.taskId ?? '')}</span> },
						{ key: 'owner', label: 'Owner', render: (row) => String(row.ownerAgentId ?? '') },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					]} />
				</Surface>
			</div>
		</>
	);
}

export function PolicySecurityPage({ overview }: { overview: Overview }) {
	return (
		<>
			<PageHeader kicker="Permission engine" title="Policy & Security" summary="Command classification, path boundaries, human gates and sandbox posture for every sensitive action." />
			<div className="grid two">
				<Surface title="Policy decisions">
					<DataTable rows={overview.permissionDecisions} empty={<EmptyState title="No policy decisions" body="Tool calls and command evaluations are recorded here." />} columns={[
						{ key: 'decision', label: 'Decision', render: (row) => <Badge tone={toneForStatus(String(row.decision ?? ''))}>{String(row.decision ?? '')}</Badge> },
						{ key: 'risk', label: 'Risk', render: (row) => String(row.riskLevel ?? '') },
						{ key: 'command', label: 'Command', render: (row) => <span className="mono">{String(row.command ?? '')}</span> },
					]} />
				</Surface>
				<Surface title="Permission grants">
					<DataTable rows={overview.permissionGrants} empty={<EmptyState title="No grants" body="Approved sensitive actions create one-use execution grants." />} columns={[
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
						{ key: 'tool', label: 'Tool', render: (row) => <span className="mono">{String(row.tool ?? '')}</span> },
						{ key: 'command', label: 'Command', render: (row) => <span className="mono">{String(row.command ?? '')}</span> },
						{ key: 'revokeReason', label: 'Revoked', render: (row) => String(row.revokeReason ?? '') },
					]} />
				</Surface>
				<Surface title="Sandbox profiles">
					<DataTable rows={overview.sandboxProfiles} empty={<EmptyState title="No profiles" body="Docker sandbox catalog and resource limits appear here." />} columns={[
						{ key: 'id', label: 'Profile', render: (row) => <span className="mono">{String(row.id ?? '')}</span> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
						{ key: 'images', label: 'Images', render: (row) => Array.isArray(row.allowedImages) ? row.allowedImages.length : 0 },
						{ key: 'limits', label: 'Limits', render: (row) => <span className="mono">{String(row.memory ?? '')} / {String(row.cpus ?? '')} cpu</span> },
					]} />
				</Surface>
				<Surface title="Tool-call execution">
					<DataTable rows={overview.agentToolCalls} empty={<EmptyState title="No tool calls" body="Agent runtime tool calls appear after policy evaluation." />} columns={[
						{ key: 'tool', label: 'Tool', render: (row) => <span className="mono">{String(row.toolName ?? '')}</span> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
						{ key: 'execution', label: 'Execution', render: (row) => {
							const payload = row.payload as Record<string, unknown> | undefined;
							return <span className="mono">{String(payload?.execution ?? 'not_recorded')}</span>;
						} },
						{ key: 'command', label: 'Command', render: (row) => {
							const payload = row.payload as Record<string, unknown> | undefined;
							return <span className="mono">{String(payload?.command ?? '')}</span>;
						} },
					]} />
				</Surface>
			</div>
		</>
	);
}

export function MemoryPage({ overview, retrievalStatus }: { overview: Overview; retrievalStatus: Record<string, unknown> | null }) {
	return (
		<>
			<PageHeader kicker="Semantic context" title="Memory & Retrieval" summary="SQLite is canonical. FAISS, NumPy and future vector stores are rebuildable indexes, not source of truth." />
			<div className="grid two">
				<Surface title="Retrieval backend">
					<div className="metric-value">{String(retrievalStatus?.mode ?? retrievalStatus?.backend ?? 'unknown')}</div>
					<div className="metric-label">{String(retrievalStatus?.degraded ? 'degraded fallback' : 'operational')}</div>
				</Surface>
				<Surface title="Memory items">
					<div className="metric-value">{overview.memoryItems.length}</div>
					<div className="metric-label">records in SQLite</div>
				</Surface>
			</div>
		</>
	);
}

export function EvidencePage({ overview }: { overview: Overview }) {
	return (
		<>
			<PageHeader kicker="Proof before approval" title="Evidence & QA" summary="QA cannot be accepted without test results, artifacts and explicit verdict records." />
			<div className="grid two">
				<Surface title="Evidence packages">
					<DataTable rows={overview.evidencePackages} empty={<EmptyState title="No evidence packages" body="Workflow QA steps will produce evidence before review." />} columns={[
						{ key: 'task', label: 'Task', render: (row) => <span className="mono">{String(row.taskId ?? '')}</span> },
						{ key: 'verdict', label: 'Verdict', render: (row) => <Badge tone={toneForStatus(String(row.qaVerdict ?? ''))}>{String(row.qaVerdict ?? '')}</Badge> },
						{ key: 'agent', label: 'Agent', render: (row) => String(row.agentId ?? '') },
						{ key: 'diffs', label: 'Diff refs', render: (row) => String(Array.isArray(row.diffRefs) ? row.diffRefs.length : 0) },
					]} />
				</Surface>
				<Surface title="Test result records">
					<DataTable rows={overview.testResultRecords} empty={<EmptyState title="No test results" body="Evidence packages record command-level QA results." />} columns={[
						{ key: 'command', label: 'Command', render: (row) => <span className="mono">{String(row.command ?? '')}</span> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
						{ key: 'evidence', label: 'Evidence', render: (row) => <span className="mono">{String(row.evidencePackageId ?? '')}</span> },
					]} />
				</Surface>
			</div>
		</>
	);
}

export function ModelGatewayPage({
	overview,
	runtimeProviders,
	mutate,
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	mutate: Mutate;
}) {
	const totalCost = overview.costUsage.reduce((sum, row) => sum + Number(row.amountUsd ?? 0), 0);
	const [policyId, setPolicyId] = useState('implementation_default');
	const [policyName, setPolicyName] = useState('Implementation Default');
	const [provider, setProvider] = useState('internal_mock');
	const [model, setModel] = useState('mock');
	const [maxCostUsd, setMaxCostUsd] = useState('1');
	const [maxTokens, setMaxTokens] = useState('4000');
	const [allowRemote, setAllowRemote] = useState(false);
	const [allowLocal, setAllowLocal] = useState(true);
	const [error, setError] = useState('');
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
	const modelOptions = providerCatalog.find((item) => item.provider === provider)?.models ?? [];
	const savePolicy = () => {
		if (!/^[a-z0-9_-]{3,64}$/.test(policyId)) {
			setError('Policy id must use lowercase letters, numbers, dashes or underscores.');
			return;
		}
		if (!policyName.trim()) {
			setError('Policy name is required.');
			return;
		}
		if (!modelOptions.includes(model)) {
			setError('Select a model from the catalog for the selected provider.');
			return;
		}
		const maxCost = Number(maxCostUsd);
		const tokenLimit = Number(maxTokens);
		if (!Number.isFinite(maxCost) || maxCost < 0) {
			setError('Maximum cost must be zero or a positive number.');
			return;
		}
		if (!Number.isInteger(tokenLimit) || tokenLimit < 512 || tokenLimit > 200000) {
			setError('Maximum tokens must be an integer between 512 and 200000.');
			return;
		}
		setError('');
		void mutate((token) =>
			createModelPolicy(token, {
				id: policyId,
				name: policyName.trim(),
				preferred: [{ provider, model }],
				fallback: [],
				maxCostUsd: maxCost,
				maxTokens: tokenLimit,
				temperature: 0.2,
				allowRemote,
				allowLocal,
			}),
		);
	};
	return (
		<>
			<PageHeader kicker="Model routing" title="Model Gateway" summary="Provider catalogs, runtime modes, fallbacks and cost usage without free-form unsafe provider inputs." />
			<div className="grid two">
				<Surface title="Providers">
					<DataTable rows={overview.modelProviders} empty={<EmptyState title="No providers" body="Provider catalog seeds at startup." />} columns={[
						{ key: 'id', label: 'Provider', render: (row) => <span className="mono">{row.id}</span> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
						{ key: 'remote', label: 'Remote', render: (row) => (row.allowRemote ? 'allowed' : 'local/manual') },
					]} />
				</Surface>
				<Surface title="Ollama catalog">
					<div className="inline"><Badge tone={runtimeProviders?.ollama.available ? 'ok' : 'warn'}>{runtimeProviders?.ollama.available ? 'ready' : 'not detected'}</Badge></div>
					<div className="mono">{runtimeProviders?.ollama.models.join(', ') || runtimeProviders?.ollama.reason || 'No local model list available'}</div>
				</Surface>
			</div>
			<div className="grid two">
				<Surface title="Strict model policy form">
					<div className="field">
						<label htmlFor="model-policy-id">Policy id</label>
						<input
							id="model-policy-id"
							className="input"
							value={policyId}
							pattern="[a-z0-9_-]{3,64}"
							onChange={(event) => setPolicyId(event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="model-policy-name">Policy name</label>
						<input
							id="model-policy-name"
							className="input"
							value={policyName}
							onChange={(event) => setPolicyName(event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="preferred-provider">Preferred provider</label>
						<select
							id="preferred-provider"
							className="select"
							value={provider}
							onChange={(event) => {
								const nextProvider = event.target.value;
								const nextModels = providerCatalog.find((item) => item.provider === nextProvider)?.models ?? [];
								setProvider(nextProvider);
								setModel(nextModels[0] ?? '');
								setAllowRemote(Boolean(providerCatalog.find((item) => item.provider === nextProvider)?.remote));
								setAllowLocal(!providerCatalog.find((item) => item.provider === nextProvider)?.remote);
							}}
						>
							{providerCatalog.map((item) => (
								<option key={item.provider} value={item.provider} disabled={item.models.length === 0}>
									{item.provider}
								</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="model-catalog">Model</label>
						<select id="model-catalog" className="select" value={model} onChange={(event) => setModel(event.target.value)}>
							{modelOptions.map((item) => (
								<option key={item} value={item}>{item}</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="max-cost-usd">Maximum cost USD</label>
						<input
							id="max-cost-usd"
							className="input"
							type="number"
							min="0"
							step="0.01"
							value={maxCostUsd}
							onChange={(event) => setMaxCostUsd(event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="max-tokens">Maximum tokens</label>
						<input
							id="max-tokens"
							className="input"
							type="number"
							min="512"
							max="200000"
							step="1"
							value={maxTokens}
							onChange={(event) => setMaxTokens(event.target.value)}
						/>
					</div>
					<label className="checkbox-row" htmlFor="allow-remote">
						<input id="allow-remote" type="checkbox" checked={allowRemote} onChange={(event) => setAllowRemote(event.target.checked)} />
						Allow remote providers
					</label>
					<label className="checkbox-row" htmlFor="allow-local">
						<input id="allow-local" type="checkbox" checked={allowLocal} onChange={(event) => setAllowLocal(event.target.checked)} />
						Allow local providers
					</label>
					{error ? <div className="form-error" role="alert">{error}</div> : null}
					<button className="button primary" type="button" onClick={savePolicy}>Save model policy</button>
				</Surface>
				<Surface title="Cost ledger">
					<div className="metric-value">${totalCost.toFixed(4)}</div>
					<div className="metric-label">recorded model usage</div>
				</Surface>
				<Surface title="Model policies">
					<DataTable rows={overview.modelPolicies} empty={<EmptyState title="No model policies" body="Model policies define allowed providers, fallback chains and budgets." />} columns={[
						{ key: 'id', label: 'Policy', render: (row) => <span className="mono">{String(row.id ?? '')}</span> },
						{ key: 'budget', label: 'Budget', render: (row) => `$${Number(row.maxCostUsd ?? 0).toFixed(2)}` },
						{ key: 'remote', label: 'Remote', render: (row) => (row.allowRemote ? 'allowed' : 'blocked') },
					]} />
				</Surface>
			</div>
			<Surface title="Model calls">
				<DataTable rows={overview.modelCalls} empty={<EmptyState title="No model calls" body="Agent runs and model gateway preparations are recorded here." />} columns={[
					{ key: 'provider', label: 'Provider', render: (row) => <span className="mono">{String(row.provider ?? '')}</span> },
					{ key: 'model', label: 'Model', render: (row) => <span className="mono">{String(row.model ?? '')}</span> },
					{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					{ key: 'cost', label: 'Cost', render: (row) => `$${Number(row.costUsd ?? 0).toFixed(4)}` },
				]} />
			</Surface>
		</>
	);
}

export function GovernancePage({ overview }: { overview: Overview }) {
	return (
		<>
			<PageHeader kicker="Engineering judgement" title="Governance" summary="Risks, decisions and next steps are operational records, not comments buried in chat." />
			<div className="grid three">
				<Surface title="Risks"><div className="metric-value">{overview.riskRegister.length}</div></Surface>
				<Surface title="Decisions"><div className="metric-value">{overview.architectureDecisions.length}</div></Surface>
				<Surface title="Next steps"><div className="metric-value">{overview.nextSteps.length}</div></Surface>
			</div>
			<div className="grid three">
				<Surface title="Risk register">
					<DataTable rows={overview.riskRegister} empty={<EmptyState title="No risks" body="Open technical and product risks appear here." />} columns={[
						{ key: 'title', label: 'Risk', render: (row) => String(row.title ?? '') },
						{ key: 'severity', label: 'Severity', render: (row) => <Badge tone={toneForStatus(String(row.severity ?? ''))}>{String(row.severity ?? '')}</Badge> },
					]} />
				</Surface>
				<Surface title="Decision records">
					<DataTable rows={overview.architectureDecisions} empty={<EmptyState title="No decisions" body="Architecture decisions should be explicit and linked to risks." />} columns={[
						{ key: 'title', label: 'Decision', render: (row) => String(row.title ?? '') },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					]} />
				</Surface>
				<Surface title="Next steps">
					<DataTable rows={overview.nextSteps} empty={<EmptyState title="No next steps" body="Mitigations and follow-up work appear here." />} columns={[
						{ key: 'title', label: 'Step', render: (row) => String(row.title ?? '') },
						{ key: 'priority', label: 'Priority', render: (row) => <Badge tone={toneForStatus(String(row.priority ?? ''))}>{String(row.priority ?? '')}</Badge> },
					]} />
				</Surface>
			</div>
		</>
	);
}

export function IntegrationsPage({ overview }: { overview: Overview }) {
	return (
		<>
			<PageHeader kicker="External tools" title="Integrations" summary="MCP, IDE, Git and automation integrations are optional adapters, never hidden core dependencies." />
			<Surface title="IDE connections">
				<DataTable rows={overview.auditEvents.filter((row) => String(row.action ?? '').includes('ide'))} empty={<EmptyState title="No integration events" body="Integration activity appears in audit records." />} columns={[
					{ key: 'action', label: 'Action', render: (row) => <span className="mono">{String(row.action ?? '')}</span> },
					{ key: 'target', label: 'Target', render: (row) => String(row.target ?? '') },
				]} />
			</Surface>
		</>
	);
}

export function AuditPage({ overview }: { overview: Overview }) {
	return (
		<>
			<PageHeader kicker="Traceability" title="Audit Log" summary="Every mutation needs actor, target, payload and event correlation." />
			<Surface title="Audit records">
				<DataTable rows={overview.auditEvents} empty={<EmptyState title="No audit records" body="Mutating API calls will be recorded here." />} columns={[
					{ key: 'action', label: 'Action', render: (row) => <span className="mono">{String(row.action ?? '')}</span> },
					{ key: 'actor', label: 'Actor', render: (row) => String(row.actor ?? '') },
					{ key: 'target', label: 'Target', render: (row) => String(row.target ?? '') },
				]} />
			</Surface>
		</>
	);
}

export function SettingsPage() {
	return (
		<>
			<PageHeader kicker="Local runtime" title="Settings" summary="Windows-native runtime controls. PNPM and uv remain the package managers for this repository." />
			<Surface title="Runtime defaults">
				<div className="stack">
					<span>Backend: FastAPI v1</span>
					<span>Frontend: Vite + React + TypeScript</span>
					<span>Autostart: user-scoped Task Scheduler</span>
				</div>
			</Surface>
		</>
	);
}
