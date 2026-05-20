import { useMemo, useState } from 'react';

import {
	createArchitectureDecision,
	createModelPolicy,
	createNextStep,
	createRisk,
	createWorkflowWithBody,
	fetchEvidenceArtifact,
	registerMcpServer,
	updateSandboxProfile,
} from '../api/client';
import type { ArtifactPayload } from '../api/client';
import type { Dictionary, Overview, RuntimeProviders } from '../api/types';
import { Badge, DataTable, Drawer, EmptyState, PageHeader, Surface } from '../components/primitives';
import { artifactDisplayName, artifactMimeType, artifactSizeLabel } from '../lib/artifacts';
import { toneForStatus } from '../lib/format';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;

export function CommandCenterPage({ overview, mutate }: { overview: Overview; mutate: Mutate }) {
	const project = overview.projects[0];
	const [workflowTitle, setWorkflowTitle] = useState(`AIDO workflow ${new Date().toISOString()}`);
	const [workflowKind, setWorkflowKind] = useState('idea_to_pr');
	const [error, setError] = useState('');
	const saveWorkflow = () => {
		const title = workflowTitle.trim();
		if (!title) {
			setError('Workflow title is required.');
			return;
		}
		if (!project) {
			setError('A project is required before creating a workflow.');
			return;
		}
		setError('');
		void mutate((token) =>
			createWorkflowWithBody(token, {
				projectId: project.id,
				title,
				kind: workflowKind,
				metadata: { source: 'command_center_form' },
			}),
		);
	};
	return (
		<>
			<PageHeader kicker="Operator lane" title="Command Center" summary="Start safe SDLC workflows and inspect pending human decisions without bypassing policy." />
			<Surface title="Workflow intake">
				<div className="form-grid">
					<div className="field">
						<label htmlFor="workflow-title">Workflow title</label>
						<input id="workflow-title" className="input" value={workflowTitle} maxLength={180} onChange={(event) => setWorkflowTitle(event.target.value)} />
					</div>
					<div className="field">
						<label htmlFor="workflow-kind">Workflow kind</label>
						<select id="workflow-kind" className="select" value={workflowKind} onChange={(event) => setWorkflowKind(event.target.value)}>
							<option value="idea_to_pr">idea_to_pr</option>
							<option value="project_discovery">project_discovery</option>
							<option value="issue_to_pr">issue_to_pr</option>
							<option value="qa_validation">qa_validation</option>
							<option value="release_candidate">release_candidate</option>
						</select>
					</div>
					<div className="inline">
						<button className="button primary" type="button" disabled={!project} onClick={saveWorkflow}>
							Create workflow
						</button>
						<Badge>{project ? project.name : 'no project'}</Badge>
					</div>
					{error ? <div className="form-error" role="alert">{error}</div> : null}
				</div>
			</Surface>
			<Surface title="Recent workflows">
				<DataTable rows={overview.workflows.slice(0, 6)} empty={<EmptyState title="No workflows" body="Create an intake workflow to start the SDLC lane." />} columns={[
					{ key: 'title', label: 'Workflow', render: (row) => String(row.title ?? '') },
					{ key: 'kind', label: 'Kind', render: (row) => <span className="mono">{String(row.kind ?? '')}</span> },
					{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
				]} />
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

export function PolicySecurityPage({ overview, mutate }: { overview: Overview; mutate: Mutate }) {
	const defaultProfile = String(overview.sandboxProfiles[0]?.id ?? 'default_docker');
	const [profileId, setProfileId] = useState(defaultProfile);
	const [selectedRevision, setSelectedRevision] = useState<Dictionary | null>(null);
	const [sandboxReason, setSandboxReason] = useState('');
	const [sandboxImage, setSandboxImage] = useState('python:3.12-slim');
	const [sandboxMemory, setSandboxMemory] = useState('512m');
	const [sandboxCpus, setSandboxCpus] = useState('1');
	const [sandboxTimeout, setSandboxTimeout] = useState('120');
	const [error, setError] = useState('');
	const saveSandboxProfile = () => {
		if (!sandboxReason.trim()) {
			setError('Sandbox update reason is required.');
			return;
		}
		if (!sandboxImage.trim() || /\s/.test(sandboxImage)) {
			setError('Sandbox allowed image must be a catalog image without spaces.');
			return;
		}
		const timeoutSeconds = Number(sandboxTimeout);
		if (!Number.isInteger(timeoutSeconds) || timeoutSeconds < 1 || timeoutSeconds > 900) {
			setError('Sandbox timeout seconds must be between 1 and 900.');
			return;
		}
		setError('');
		void mutate((token) =>
			updateSandboxProfile(token, profileId, {
				reason: sandboxReason.trim(),
				allowedImages: [sandboxImage.trim()],
				allowedNetworks: ['none'],
				defaultNetwork: 'none',
				memory: sandboxMemory.trim(),
				cpus: sandboxCpus.trim(),
				timeoutSeconds,
				status: 'active',
			}),
		);
	};
	return (
		<>
			<PageHeader kicker="Permission engine" title="Policy & Security" summary="Command classification, path boundaries, human gates and sandbox posture for every sensitive action." />
			<div className="grid two">
				<Surface title="Strict sandbox profile form">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="sandbox-profile">Sandbox profile</label>
							<select id="sandbox-profile" className="select" value={profileId} onChange={(event) => setProfileId(event.target.value)}>
								{overview.sandboxProfiles.map((profile) => (
									<option key={String(profile.id)} value={String(profile.id)}>{String(profile.id)}</option>
								))}
							</select>
						</div>
						<div className="field">
							<label htmlFor="sandbox-reason">Sandbox update reason</label>
							<input id="sandbox-reason" className="input" value={sandboxReason} onChange={(event) => setSandboxReason(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="sandbox-image">Sandbox allowed image</label>
							<input id="sandbox-image" className="input" value={sandboxImage} onChange={(event) => setSandboxImage(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="sandbox-memory">Sandbox memory limit</label>
							<input id="sandbox-memory" className="input" value={sandboxMemory} onChange={(event) => setSandboxMemory(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="sandbox-cpus">Sandbox CPU limit</label>
							<input id="sandbox-cpus" className="input" value={sandboxCpus} onChange={(event) => setSandboxCpus(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="sandbox-timeout">Sandbox timeout seconds</label>
							<input id="sandbox-timeout" className="input" type="number" min="1" max="900" value={sandboxTimeout} onChange={(event) => setSandboxTimeout(event.target.value)} />
						</div>
						{error ? <div className="form-error" role="alert">{error}</div> : null}
						<button className="button primary" type="button" onClick={saveSandboxProfile}>Save sandbox profile</button>
					</div>
				</Surface>
				<Surface title="Policy decisions">
					<DataTable rows={overview.permissionDecisions} empty={<EmptyState title="No policy decisions" body="Tool calls and command evaluations are recorded here." />} columns={[
						{ key: 'decision', label: 'Decision', render: (row) => <Badge tone={toneForStatus(String(row.decision ?? ''))}>{String(row.decision ?? '')}</Badge> },
						{ key: 'risk', label: 'Risk', render: (row) => String(row.riskLevel ?? '') },
						{ key: 'command', label: 'Command', render: (row) => <span className="mono">{String(row.command ?? '')}</span> },
					]} />
				</Surface>
				<Surface title="Policy revisions">
					<DataTable rows={overview.policyRevisions} empty={<EmptyState title="No revisions" body="Policy and sandbox changes will create explicit revision records." />} columns={[
						{ key: 'subject', label: 'Subject', render: (row) => <span className="mono">{String(row.subjectId ?? '')}</span> },
						{ key: 'version', label: 'Version', render: (row) => <Badge>v{String(row.version ?? '')}</Badge> },
						{ key: 'fields', label: 'Changed', render: (row) => Array.isArray(row.changedFields) ? row.changedFields.join(', ') : '' },
						{
							key: 'diff',
							label: 'Diff',
							render: (row) => (
								<button className="button" type="button" aria-label={`View policy revision diff for ${String(row.subjectId ?? '')}`} onClick={() => setSelectedRevision(row)}>
									View diff
								</button>
							),
						},
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
			<Drawer label="Policy revision diff" open={Boolean(selectedRevision)} onClose={() => setSelectedRevision(null)}>
				<div className="drawer-body">
					{selectedRevision ? (
						<>
							<div className="stack">
								<div className="inline">
									<Badge>{String(selectedRevision.subjectType ?? '')}</Badge>
									<Badge>v{String(selectedRevision.version ?? '')}</Badge>
								</div>
								<h3 className="artifact-title">{String(selectedRevision.subjectId ?? '')}</h3>
								<div className="muted">{String(selectedRevision.reason ?? '')}</div>
							</div>
							<div className="diff-grid" role="table" aria-label="Policy revision changed fields">
								<div className="diff-row diff-head" role="row">
									<div role="columnheader">Field</div>
									<div role="columnheader">Before</div>
									<div role="columnheader">After</div>
								</div>
								{(Array.isArray(selectedRevision.changedFields) ? selectedRevision.changedFields : []).map((field) => {
									const previous = selectedRevision.previous as Record<string, unknown> | undefined;
									const updated = selectedRevision.updated as Record<string, unknown> | undefined;
									return (
										<div className="diff-row" role="row" key={String(field)}>
											<div role="cell" className="mono">{String(field)}</div>
											<div role="cell" className="diff-before">{JSON.stringify(previous?.[String(field)] ?? null)}</div>
											<div role="cell" className="diff-after">{JSON.stringify(updated?.[String(field)] ?? null)}</div>
										</div>
									);
								})}
							</div>
						</>
					) : null}
				</div>
			</Drawer>
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

export function EvidencePage({ overview, token }: { overview: Overview; token: string }) {
	const [previewArtifact, setPreviewArtifact] = useState<Dictionary | null>(null);
	const [previewPayload, setPreviewPayload] = useState<ArtifactPayload | null>(null);
	const [previewLoadingId, setPreviewLoadingId] = useState('');
	const [previewError, setPreviewError] = useState('');
	const openPreview = async (artifact: Dictionary) => {
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setPreviewError('Artifact metadata is incomplete.');
			return;
		}
		setPreviewArtifact(artifact);
		setPreviewPayload(null);
		setPreviewError('');
		setPreviewLoadingId(artifactId);
		try {
			const payload = await fetchEvidenceArtifact(token, evidenceId, artifactId);
			setPreviewPayload(payload);
		} catch (error) {
			setPreviewError(error instanceof Error ? error.message : 'Artifact preview failed.');
		} finally {
			setPreviewLoadingId('');
		}
	};
	const downloadPreview = () => {
		if (!previewArtifact || !previewPayload) return;
		const url = URL.createObjectURL(previewPayload.blob);
		const link = document.createElement('a');
		link.href = url;
		link.download = artifactDisplayName(previewArtifact);
		document.body.appendChild(link);
		link.click();
		link.remove();
		URL.revokeObjectURL(url);
	};
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
				<Surface title="Artifacts">
					<DataTable rows={overview.artifacts} empty={<EmptyState title="No artifacts" body="Evidence artifacts can be previewed only through token-protected v1 endpoints." />} columns={[
						{ key: 'name', label: 'Name', render: (row) => artifactDisplayName(row) },
						{ key: 'kind', label: 'Kind', render: (row) => <span className="mono">{String(row.kind ?? '')}</span> },
						{ key: 'size', label: 'Size', render: (row) => artifactSizeLabel(row) },
						{ key: 'hash', label: 'Hash', render: (row) => <span className="mono">{String(row.hash ?? '').slice(0, 12)}</span> },
						{
							key: 'action',
							label: 'Action',
							render: (row) => {
								const name = artifactDisplayName(row);
								const loading = previewLoadingId === String(row.id ?? '');
								return (
									<button className="button" type="button" aria-label={`Preview artifact ${name}`} disabled={loading} onClick={() => void openPreview(row)}>
										{loading ? 'Opening' : 'Preview'}
									</button>
								);
							},
						},
					]} />
				</Surface>
			</div>
			<Drawer label="Artifact preview" open={Boolean(previewArtifact)} onClose={() => {
				setPreviewArtifact(null);
				setPreviewPayload(null);
				setPreviewError('');
			}}>
				<div className="drawer-body">
					{previewArtifact ? (
						<>
							<div className="stack">
								<div className="inline">
									<Badge>{String(previewArtifact.kind ?? 'artifact')}</Badge>
									<Badge>{artifactMimeType(previewArtifact, previewPayload)}</Badge>
									<Badge>{artifactSizeLabel(previewArtifact)}</Badge>
								</div>
								<h3 className="artifact-title">{artifactDisplayName(previewArtifact)}</h3>
								<div className="mono">sha256 {String(previewPayload?.hash || previewArtifact.hash || 'not recorded')}</div>
							</div>
							{previewError ? <div className="form-error" role="alert">{previewError}</div> : null}
							{previewPayload?.text ? (
								<pre className="artifact-preview">{previewPayload.text}</pre>
							) : (
								<EmptyState title={previewLoadingId ? 'Loading artifact' : 'Binary or empty artifact'} body="Non-text artifacts remain downloadable, but are not rendered inline." />
							)}
							<button className="button primary" type="button" disabled={!previewPayload} aria-label={`Download artifact ${artifactDisplayName(previewArtifact)}`} onClick={downloadPreview}>
								Download artifact
							</button>
						</>
					) : null}
				</div>
			</Drawer>
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

export function GovernancePage({ overview, mutate }: { overview: Overview; mutate: Mutate }) {
	const project = overview.projects[0];
	const [riskTitle, setRiskTitle] = useState('');
	const [riskSeverity, setRiskSeverity] = useState('medium');
	const [riskMitigation, setRiskMitigation] = useState('');
	const [decisionTitle, setDecisionTitle] = useState('');
	const [decisionStatus, setDecisionStatus] = useState('proposed');
	const [decisionContext, setDecisionContext] = useState('');
	const [decisionText, setDecisionText] = useState('');
	const [nextStepTitle, setNextStepTitle] = useState('');
	const [nextStepPriority, setNextStepPriority] = useState('medium');
	const [error, setError] = useState('');
	const saveRisk = () => {
		if (!riskTitle.trim()) {
			setError('Risk title is required.');
			return;
		}
		if (['high', 'critical'].includes(riskSeverity) && !riskMitigation.trim()) {
			setError('High and critical risks require mitigation.');
			return;
		}
		if (!project) {
			setError('A project is required before creating governance records.');
			return;
		}
		setError('');
		void mutate((token) =>
			createRisk(token, {
				projectId: project.id,
				title: riskTitle.trim(),
				severity: riskSeverity,
				status: 'open',
				mitigation: riskMitigation.trim(),
				owner: 'technical_lead',
			}),
		);
	};
	const saveDecision = () => {
		if (!decisionTitle.trim()) {
			setError('Decision title is required.');
			return;
		}
		if (decisionStatus === 'accepted' && (!decisionContext.trim() || !decisionText.trim())) {
			setError('Accepted decisions require context and decision text.');
			return;
		}
		if (!project) {
			setError('A project is required before creating governance records.');
			return;
		}
		setError('');
		void mutate((token) =>
			createArchitectureDecision(token, {
				projectId: project.id,
				title: decisionTitle.trim(),
				status: decisionStatus,
				context: decisionContext.trim(),
				decision: decisionText.trim(),
				consequences: [],
			}),
		);
	};
	const saveNextStep = () => {
		if (!nextStepTitle.trim()) {
			setError('Next step title is required.');
			return;
		}
		if (!project) {
			setError('A project is required before creating governance records.');
			return;
		}
		setError('');
		void mutate((token) =>
			createNextStep(token, {
				projectId: project.id,
				title: nextStepTitle.trim(),
				priority: nextStepPriority,
				status: 'planned',
				owner: 'technical_lead',
			}),
		);
	};
	return (
		<>
			<PageHeader kicker="Engineering judgement" title="Governance" summary="Risks, decisions and next steps are operational records, not comments buried in chat." />
			<div className="grid three">
				<Surface title="Risks"><div className="metric-value">{overview.riskRegister.length}</div></Surface>
				<Surface title="Decisions"><div className="metric-value">{overview.architectureDecisions.length}</div></Surface>
				<Surface title="Next steps"><div className="metric-value">{overview.nextSteps.length}</div></Surface>
			</div>
			<Surface title="Strict record forms">
				<div className="grid three">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="risk-title">Risk title</label>
							<input id="risk-title" className="input" value={riskTitle} onChange={(event) => setRiskTitle(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="risk-severity">Risk severity</label>
							<select id="risk-severity" className="select" value={riskSeverity} onChange={(event) => setRiskSeverity(event.target.value)}>
								<option value="low">low</option>
								<option value="medium">medium</option>
								<option value="high">high</option>
								<option value="critical">critical</option>
							</select>
						</div>
						<div className="field">
							<label htmlFor="risk-mitigation">Risk mitigation</label>
							<input id="risk-mitigation" className="input" value={riskMitigation} onChange={(event) => setRiskMitigation(event.target.value)} />
						</div>
						<button className="button primary" type="button" onClick={saveRisk}>Save risk</button>
					</div>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="decision-title">Decision title</label>
							<input id="decision-title" className="input" value={decisionTitle} onChange={(event) => setDecisionTitle(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="decision-status">Decision status</label>
							<select id="decision-status" className="select" value={decisionStatus} onChange={(event) => setDecisionStatus(event.target.value)}>
								<option value="proposed">proposed</option>
								<option value="accepted">accepted</option>
								<option value="rejected">rejected</option>
								<option value="superseded">superseded</option>
								<option value="deprecated">deprecated</option>
							</select>
						</div>
						<div className="field">
							<label htmlFor="decision-context">Decision context</label>
							<input id="decision-context" className="input" value={decisionContext} onChange={(event) => setDecisionContext(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="decision-text">Decision text</label>
							<input id="decision-text" className="input" value={decisionText} onChange={(event) => setDecisionText(event.target.value)} />
						</div>
						<button className="button primary" type="button" onClick={saveDecision}>Save decision</button>
					</div>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="next-step-title">Next step title</label>
							<input id="next-step-title" className="input" value={nextStepTitle} onChange={(event) => setNextStepTitle(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="next-step-priority">Next step priority</label>
							<select id="next-step-priority" className="select" value={nextStepPriority} onChange={(event) => setNextStepPriority(event.target.value)}>
								<option value="low">low</option>
								<option value="medium">medium</option>
								<option value="high">high</option>
								<option value="urgent">urgent</option>
							</select>
						</div>
						<button className="button primary" type="button" onClick={saveNextStep}>Save next step</button>
					</div>
				</div>
				{error ? <div className="form-error" role="alert">{error}</div> : null}
			</Surface>
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

export function IntegrationsPage({ overview, mutate }: { overview: Overview; mutate: Mutate }) {
	const [serverId, setServerId] = useState('mcp_local');
	const [command, setCommand] = useState('python -m local_mcp_server');
	const [transport, setTransport] = useState('stdio');
	const [error, setError] = useState('');
	const registerServer = () => {
		if (!/^[a-z0-9][a-z0-9_-]{2,63}$/.test(serverId)) {
			setError('MCP server id must use lowercase letters, numbers, dashes or underscores.');
			return;
		}
		if (!command.trim()) {
			setError('MCP command is required.');
			return;
		}
		if (/[;&|<>`\r\n]/.test(command)) {
			setError('MCP command must be a single argv-style command without shell operators.');
			return;
		}
		setError('');
		void mutate((token) => registerMcpServer(token, { id: serverId, command: command.trim(), transport, metadata: { source: 'integrations_form' } }));
	};
	return (
		<>
			<PageHeader kicker="External tools" title="Integrations" summary="MCP, IDE, Git and automation integrations are optional adapters, never hidden core dependencies." />
			<div className="grid two">
				<Surface title="Strict MCP registration form">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="mcp-server-id">MCP server id</label>
							<input id="mcp-server-id" className="input" value={serverId} pattern="[a-z0-9][a-z0-9_-]{2,63}" onChange={(event) => setServerId(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="mcp-command">MCP command</label>
							<input id="mcp-command" className="input" value={command} onChange={(event) => setCommand(event.target.value)} />
							<div className="field-help">Stored as argv-style config; execution still goes through broker, policy and sandbox.</div>
						</div>
						<div className="field">
							<label htmlFor="mcp-transport">MCP transport</label>
							<select id="mcp-transport" className="select" value={transport} onChange={(event) => setTransport(event.target.value)}>
								<option value="stdio">stdio</option>
							</select>
						</div>
						{error ? <div className="form-error" role="alert">{error}</div> : null}
						<button className="button primary" type="button" onClick={registerServer}>Register MCP server</button>
					</div>
				</Surface>
				<Surface title="Registered MCP servers">
					<DataTable rows={overview.mcpServers} empty={<EmptyState title="No MCP servers" body="Register local stdio MCP servers before runtime adapters can call them." />} columns={[
						{ key: 'id', label: 'Server', render: (row) => <span className="mono">{String(row.id ?? '')}</span> },
						{ key: 'transport', label: 'Transport', render: (row) => String(row.transport ?? '') },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					]} />
				</Surface>
				<Surface title="IDE connections">
					<DataTable rows={overview.auditEvents.filter((row) => String(row.action ?? '').includes('ide'))} empty={<EmptyState title="No integration events" body="Integration activity appears in audit records." />} columns={[
						{ key: 'action', label: 'Action', render: (row) => <span className="mono">{String(row.action ?? '')}</span> },
						{ key: 'target', label: 'Target', render: (row) => String(row.target ?? '') },
					]} />
				</Surface>
			</div>
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
