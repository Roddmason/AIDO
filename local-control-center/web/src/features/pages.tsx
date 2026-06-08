import { useEffect, useMemo, useState } from 'react';

import {
	createArchitectureDecision,
	createModelGatewayRolePolicy,
	createNextStep,
	createRisk,
	downloadEvidenceArtifact,
	fetchEvidenceArtifact,
	getEvidenceDetail,
	registerMcpServer,
	runIssueToPatch,
	updateRisk,
	updateSandboxProfile,
} from '../api/client';
import type { ArtifactPayload, EvidenceDetailResponse, IssueToPatchResponse } from '../api/client';
import type {
	ArchitectureDecisionStatus,
	Artifact,
	Dictionary,
	McpTransport,
	NextStepPriority,
	Overview,
	PolicyRevision,
	Project,
	RetrievalStatus,
	RiskSeverity,
	RiskStatus,
	RuntimeProvider,
	RuntimeProviders,
} from '../api/types';
import { Badge, DataTable, Drawer, EmptyState, PageHeader, Surface } from '../components/primitives';
import { artifactDisplayName, artifactMimeType, artifactSizeLabel } from '../lib/artifacts';
import { toneForStatus } from '../lib/format';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;

const issueQaPresets = [
	{ id: 'none', label: 'No QA command', commands: [] as string[][] },
	{ id: 'python-tests', label: 'Python tests', commands: [['uv', 'run', 'pytest', '-q']] },
	{ id: 'web-tests', label: 'Web tests', commands: [['corepack', 'pnpm@10.24.0', 'run', 'test:web']] },
	{ id: 'quality', label: 'Quality suite', commands: [['corepack', 'pnpm@10.24.0', 'run', 'quality']] },
];

const issueRuntimeCapabilities = new Set(['issue_to_patch', 'code_edit']);
const issueTimelineOrder = ['created', 'workspace_allocated', 'runtime_selected', 'running', 'qa_running', 'evidence_ready', 'awaiting_approval'] as const;

type TimelineStatus = 'pending' | 'done' | 'active' | 'blocked' | 'failed';

function runtimeSupportsIssueToPatch(runtime: RuntimeProvider) {
	const kind = String(runtime.kind ?? '').toLowerCase();
	return kind !== 'test' && kind !== 'simulation' && (runtime.capabilities ?? []).some((capability) => issueRuntimeCapabilities.has(capability));
}

function runtimeIsExecutableIssueRuntime(runtime: RuntimeProvider) {
	return runtimeSupportsIssueToPatch(runtime) && runtime.executable === true;
}

function activeProjects(projects: Project[]) {
	return projects.filter((project) => String(project.status ?? 'active') === 'active');
}

function objectRecord(value: unknown) {
	return value && typeof value === 'object' ? (value as Record<string, unknown>) : undefined;
}

function buildIssueTimeline(result: IssueToPatchResponse | null, issueBusy: boolean, hasExecutableRuntime: boolean) {
	if (!result) {
		return [
			{ id: 'created', status: issueBusy ? 'done' : 'pending', detail: issueBusy ? 'request submitted' : 'not started' },
			{ id: 'workspace_allocated', status: 'pending', detail: 'waiting for workflow run' },
			{ id: 'runtime_selected', status: hasExecutableRuntime ? 'pending' : 'blocked', detail: hasExecutableRuntime ? 'waiting for run' : 'runtime_unavailable' },
			{ id: 'running', status: 'pending', detail: 'waiting for executable runtime' },
			{ id: 'qa_running', status: 'pending', detail: 'waiting for runtime output' },
			{ id: 'evidence_ready', status: 'pending', detail: 'waiting for artifact package' },
			{ id: 'awaiting_approval', status: 'pending', detail: 'waiting for evidence' },
			{ id: 'completed/failed', status: 'pending', detail: 'no terminal verdict' },
		] as Array<{ id: string; status: TimelineStatus; detail: string }>;
	}
	const rawResult = result as unknown as Record<string, unknown>;
	const status = String(result.status ?? '');
	const workflowRun = objectRecord(result.workflowRun);
	const workspace = objectRecord(result.workspace);
	const runtime = objectRecord(result.runtime);
	const runtimeResult = objectRecord(result.runtimeResult);
	const evidence = objectRecord(result.evidencePackage);
	const qaResults = Array.isArray(result.qaResults) ? result.qaResults : [];
	const isRuntimeUnavailable = status === 'runtime_unavailable' || status === 'unavailable';
	const isFailed = isRuntimeUnavailable || status === 'failed' || String(workflowRun?.status ?? '') === 'failed';
	const isCompleted = status === 'completed' || String(workflowRun?.status ?? '') === 'completed';
	const awaitingApproval = Boolean(rawResult.actionRequest) || (Boolean(rawResult.approvalRequired) && (status === 'evidence_ready' || String(workflowRun?.status ?? '') === 'awaiting_permission'));
	const stageDetails: Record<(typeof issueTimelineOrder)[number], string> = {
		created: String(workflowRun?.id ?? objectRecord(result.workflow)?.id ?? 'workflow not recorded'),
		workspace_allocated: String(workspace?.id ?? 'workspace not allocated'),
		runtime_selected: String(runtime?.id ?? 'runtime not selected'),
		running: String(runtimeResult?.status ?? (status || 'not started')),
		qa_running: qaResults.length ? String(objectRecord(qaResults[0])?.status ?? objectRecord(qaResults[0])?.verdict ?? 'qa_recorded') : 'qa_not_run',
		evidence_ready: String(evidence?.id ?? 'evidence not created'),
		awaiting_approval: awaitingApproval ? 'approval gate open' : 'no pending approval',
	};
	const stageStatuses: Record<(typeof issueTimelineOrder)[number], TimelineStatus> = {
		created: workflowRun?.id || objectRecord(result.workflow)?.id ? 'done' : isFailed ? 'failed' : 'pending',
		workspace_allocated: workspace?.id ? 'done' : isFailed ? 'failed' : 'pending',
		runtime_selected: isRuntimeUnavailable ? 'failed' : runtime?.id ? 'done' : isFailed ? 'failed' : 'pending',
		running: isFailed ? 'failed' : isCompleted || runtimeResult ? 'done' : issueBusy ? 'active' : 'pending',
		qa_running: qaResults.length ? (String(objectRecord(qaResults[0])?.status ?? objectRecord(qaResults[0])?.verdict ?? '') === 'failed' ? 'failed' : 'done') : 'pending',
		evidence_ready: evidence?.id ? 'done' : isFailed ? 'failed' : 'pending',
		awaiting_approval: awaitingApproval ? 'active' : isCompleted ? 'done' : 'pending',
	};
	const terminalId = isCompleted ? 'completed' : isFailed ? 'failed' : 'completed/failed';
	const terminalStatus: TimelineStatus = isCompleted ? 'done' : isFailed ? 'failed' : 'pending';
	return [
		...issueTimelineOrder.map((id) => ({ id, status: stageStatuses[id], detail: stageDetails[id] })),
		{ id: terminalId, status: terminalStatus, detail: status || String(workflowRun?.status ?? 'not terminal') },
	];
}

export function CommandCenterPage({
	overview,
	selectedProject,
	runtimeProviders,
	mutate,
	onSelectProject,
}: {
	overview: Overview;
	selectedProject: Project | null;
	runtimeProviders: RuntimeProviders | null;
	mutate: Mutate;
	onSelectProject: (projectId: string) => void;
}) {
	const projectOptions = useMemo(() => activeProjects(overview.projects), [overview.projects]);
	const initialProjectId = selectedProject?.id ?? projectOptions[0]?.id ?? '';
	const [selectedProjectId, setSelectedProjectId] = useState(initialProjectId);
	const [issueTitle, setIssueTitle] = useState('');
	const [issueText, setIssueText] = useState('');
	const [targetPath, setTargetPath] = useState('');
	const [preferredRuntime, setPreferredRuntime] = useState('');
	const [qaPreset, setQaPreset] = useState('python-tests');
	const [maxCostUsd, setMaxCostUsd] = useState('');
	const [requireApproval, setRequireApproval] = useState(true);
	const [issueResult, setIssueResult] = useState<IssueToPatchResponse | null>(null);
	const [issueBusy, setIssueBusy] = useState(false);
	const [issueError, setIssueError] = useState('');
	const runtimeRows = runtimeProviders?.providers ?? [];
	const executableRuntimes = useMemo(() => runtimeRows.filter(runtimeIsExecutableIssueRuntime), [runtimeRows]);
	const selectedRuntime = executableRuntimes.find((item) => item.id === preferredRuntime) ?? null;
	const selectedQaPreset = issueQaPresets.find((item) => item.id === qaPreset) ?? issueQaPresets[0];
	const qaMissing = selectedQaPreset.commands.length === 0;
	const project = projectOptions.find((item) => item.id === selectedProjectId) ?? null;
	const unavailableIssueRuntime = runtimeRows.find((runtime) => runtimeSupportsIssueToPatch(runtime) && runtime.executable !== true);
	const runtimeBlockReason = runtimeProviders
		? unavailableIssueRuntime?.reason ?? 'No executable issue_to_patch/code_edit runtime is configured.'
		: 'Runtime provider discovery has not completed.';
	const hasExecutableRuntime = executableRuntimes.length > 0;
	const issueResultRuntime = issueResult?.runtime as Record<string, unknown> | undefined;
	const issueResultQa = issueResult?.qaResults[0] as Record<string, unknown> | undefined;
	const issueResultDiff = issueResult?.diffSummary as Record<string, unknown> | undefined;
	const issueEvidence = issueResult?.evidencePackage as Record<string, unknown> | undefined;
	const issueChangedFiles = Array.isArray(issueResultDiff?.changedFiles) ? issueResultDiff.changedFiles.length : Number(issueResultDiff?.changedFiles ?? 0);
	const issueStatus = String(issueResult?.status ?? '');
	const issueExecutionMode = issueStatus === 'runtime_unavailable' || issueStatus === 'unavailable' || issueResultRuntime?.executable === false ? 'runtime_unavailable' : 'productive_runtime';
	const issueTimeline = buildIssueTimeline(issueResult, issueBusy, hasExecutableRuntime);
	const evidenceId = String(issueEvidence?.id ?? '');
	const artifactIds = Array.isArray(issueEvidence?.artifactIds) ? issueEvidence.artifactIds.map((item) => String(item)) : [];
	const artifactRows = Array.isArray(issueEvidence?.artifacts) ? issueEvidence.artifacts.map((item) => objectRecord(item)).filter(Boolean) as Array<Record<string, unknown>> : [];
	const diffRefs = [
		...(Array.isArray(issueEvidence?.diffRefs) ? issueEvidence.diffRefs.map((item) => String(item)) : []),
		...artifactRows
			.filter((artifact) => String(artifact.kind ?? artifact.name ?? artifact.id ?? '').toLowerCase().includes('diff'))
			.map((artifact) => String(artifact.id ?? artifact.name ?? 'diff')),
		...artifactIds.filter((artifactId) => artifactId.toLowerCase().includes('diff')),
	];
	const runDisabled = !project || issueBusy || qaMissing || !selectedRuntime || !issueTitle.trim() || !issueText.trim();
	const selectProject = (projectId: string) => {
		setSelectedProjectId(projectId);
		const nextProject = projectOptions.find((item) => item.id === projectId);
		if (nextProject) onSelectProject(nextProject.id);
	};
	useEffect(() => {
		setSelectedProjectId((current) => {
			if (current && projectOptions.some((item) => item.id === current)) return current;
			return selectedProject?.id ?? projectOptions[0]?.id ?? '';
		});
	}, [projectOptions, selectedProject?.id]);
	useEffect(() => {
		if (!executableRuntimes.length) {
			setPreferredRuntime('');
			return;
		}
		setPreferredRuntime((current) => executableRuntimes.some((runtime) => runtime.id === current) ? current : executableRuntimes[0].id);
	}, [executableRuntimes]);
	const runPatchWorkflow = async () => {
		const title = issueTitle.trim();
		const bodyText = issueText.trim();
		if (!project) {
			setIssueError('A project is required before running issue_to_patch.');
			return;
		}
		if (!title) {
			setIssueError('Issue title is required.');
			return;
		}
		if (!bodyText) {
			setIssueError('Issue text is required.');
			return;
		}
		if (selectedQaPreset.commands.length === 0) {
			setIssueError('Select a QA preset before running issue_to_patch.');
			return;
		}
		if (!selectedRuntime) {
			setIssueError(runtimeBlockReason);
			return;
		}
		const parsedMaxCost = maxCostUsd.trim() ? Number(maxCostUsd) : undefined;
		if (parsedMaxCost !== undefined && (!Number.isFinite(parsedMaxCost) || parsedMaxCost < 0)) {
			setIssueError('Maximum cost must be zero or a positive number.');
			return;
		}
		setIssueBusy(true);
		setIssueError('');
		setIssueResult(null);
		try {
			const result = await mutate((token) =>
				runIssueToPatch(token, {
					projectId: project.id,
					title,
					issueText: bodyText,
					targetPath: targetPath.trim() || undefined,
					preferredRuntime: selectedRuntime.id,
					qaCommands: selectedQaPreset.commands,
					maxCostUsd: parsedMaxCost,
					requireApproval,
				}),
			);
			setIssueResult(result);
		} catch (submitError) {
			setIssueError(submitError instanceof Error ? submitError.message : 'issue_to_patch failed.');
		} finally {
			setIssueBusy(false);
		}
	};
	return (
		<>
			<PageHeader kicker="Operator lane" title="Command Center" summary="Start safe SDLC workflows and inspect pending human decisions without bypassing policy." />
			<Surface title="issue_to_patch real runtime">
				<div className="form-grid">
					<div className="field">
						<label htmlFor="issue-project">Project</label>
						<select id="issue-project" className="select" value={selectedProjectId} disabled={!projectOptions.length || issueBusy} onChange={(event) => selectProject(event.target.value)}>
							{projectOptions.length ? null : <option value="">No active project</option>}
							{projectOptions.map((item) => (
								<option key={item.id} value={item.id}>{item.name}</option>
							))}
						</select>
						<div className="field-help">{project ? String(project.path ?? project.id) : 'Select an active operational project before running issue_to_patch.'}</div>
					</div>
					<div className="field">
						<label htmlFor="issue-title">Issue title</label>
						<input
							id="issue-title"
							className="input"
							value={issueTitle}
							maxLength={180}
							autoComplete="off"
							disabled={!project}
							onChange={(event) => setIssueTitle(event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="issue-text">Issue text</label>
						<textarea
							id="issue-text"
							className="textarea"
							value={issueText}
							rows={6}
							disabled={!project}
							onChange={(event) => setIssueText(event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="target-path">Target path</label>
						<input id="target-path" className="input" value={targetPath} disabled={!project} placeholder="Optional repository-relative path" onChange={(event) => setTargetPath(event.target.value)} />
					</div>
					<div className="field">
						<label htmlFor="preferred-runtime">Preferred runtime</label>
						<select id="preferred-runtime" className="select" value={preferredRuntime} disabled={!project || !hasExecutableRuntime || issueBusy} onChange={(event) => setPreferredRuntime(event.target.value)}>
							{hasExecutableRuntime ? null : <option value="">No executable runtime</option>}
							{executableRuntimes.map((runtime) => (
								<option key={runtime.id} value={runtime.id}>
									{runtime.id} - {runtime.executable ? 'executable' : runtime.available ? 'available' : 'unavailable'}
								</option>
							))}
						</select>
						{selectedRuntime ? (
							<>
								<div className="inline">
									<Badge tone={selectedRuntime.detected ? 'ok' : 'warn'}>{selectedRuntime.detected ? 'detected' : 'not detected'}</Badge>
									<Badge tone={selectedRuntime.configured ? 'ok' : 'warn'}>{selectedRuntime.configured ? 'configured' : 'not configured'}</Badge>
									<Badge tone={selectedRuntime.available ? 'ok' : 'warn'}>{selectedRuntime.available ? 'available' : 'unavailable'}</Badge>
									<Badge tone={selectedRuntime.executable ? 'ok' : 'warn'}>{selectedRuntime.executable ? 'executable' : 'not executable'}</Badge>
								</div>
								<div className="field-help">
									{`${selectedRuntime.reason}${selectedRuntime.requiredConfiguration?.length ? ` Required: ${selectedRuntime.requiredConfiguration.join(', ')}` : ''}`}
								</div>
							</>
						) : (
							<div className="form-error" role="status">
								<Badge tone="danger">runtime_unavailable</Badge> {runtimeBlockReason}
							</div>
						)}
					</div>
					<div className="field">
						<label htmlFor="qa-preset">QA preset</label>
						<select id="qa-preset" className="select" value={qaPreset} disabled={!project} onChange={(event) => setQaPreset(event.target.value)}>
							{issueQaPresets.map((preset) => (
								<option key={preset.id} value={preset.id}>{preset.label}</option>
							))}
						</select>
						<div className="field-help">{selectedQaPreset.commands.length ? selectedQaPreset.commands.map((command) => command.join(' ')).join(' | ') : 'No QA command selected; issue_to_patch is blocked.'}</div>
					</div>
					<div className="field">
						<label htmlFor="issue-max-cost">Maximum cost USD</label>
						<input id="issue-max-cost" className="input" type="number" min="0" step="0.01" value={maxCostUsd} disabled={!project} onChange={(event) => setMaxCostUsd(event.target.value)} />
					</div>
					<div className="inline">
						<label className="checkbox-row" htmlFor="issue-require-approval">
							<input id="issue-require-approval" type="checkbox" checked={requireApproval} disabled={!project} onChange={(event) => setRequireApproval(event.target.checked)} />
							Require approval before completion
						</label>
					</div>
					<div className="inline">
						<button className="button primary" type="button" disabled={runDisabled} onClick={() => void runPatchWorkflow()}>
							{issueBusy ? 'Running issue_to_patch' : 'Run issue_to_patch'}
						</button>
						<Badge tone={project ? 'ok' : 'warn'}>{project ? project.name : 'no operational project'}</Badge>
						{selectedRuntime ? <Badge tone="ok">{selectedRuntime.id}</Badge> : <Badge tone="danger">runtime_unavailable</Badge>}
						{qaMissing ? <Badge tone="danger">qa_not_selected</Badge> : null}
					</div>
					{issueError ? <div className="form-error" role="alert">{issueError}</div> : null}
					<div className="stack" aria-label="issue_to_patch timeline">
						<h3 className="section-subtitle">Workflow timeline</h3>
						{issueTimeline.map((stage) => (
							<div className="inline" key={stage.id}>
								<Badge tone={stage.status === 'done' ? 'ok' : stage.status === 'active' ? 'info' : stage.status === 'failed' || stage.status === 'blocked' ? 'danger' : undefined}>{stage.status}</Badge>
								<span className="mono">{stage.id}</span>
								<span className="muted">{stage.detail}</span>
							</div>
						))}
					</div>
					{issueResult ? (
						<div className="stack" aria-live="polite">
							<div className="inline">
								<Badge tone={toneForStatus(String(issueResult.status ?? ''))}>{String(issueResult.status ?? '')}</Badge>
								<Badge>{String(issueResultRuntime?.id ?? 'no_runtime')}</Badge>
								<Badge tone={toneForStatus(issueExecutionMode)}>{issueExecutionMode}</Badge>
								<Badge tone={issueResult?.qaResults.length ? toneForStatus(String(issueResultQa?.status ?? issueResultQa?.verdict ?? '')) : 'warn'}>{String(issueResultQa?.status ?? issueResultQa?.verdict ?? 'qa_not_run')}</Badge>
							</div>
							<div className="mono">{String(issueResult.reason ?? issueResultRuntime?.reason ?? 'No runtime reason recorded.')}</div>
							<div className="mono">Evidence {String(issueResult.evidencePackage?.id ?? 'not_created')} / changed files {Number.isFinite(issueChangedFiles) ? issueChangedFiles : 0}</div>
							<div className="inline">
								{evidenceId ? <a className="button" href="#evidence">Evidence package {evidenceId}</a> : <Badge tone="warn">evidence_not_created</Badge>}
								{diffRefs.length ? <a className="button" href="#evidence">Diff {diffRefs.join(', ')}</a> : <Badge tone="warn">diff_not_recorded</Badge>}
								<a className="button" href="#workflows">Workflow run {String(objectRecord(issueResult.workflowRun)?.id ?? 'not_recorded')}</a>
							</div>
						</div>
					) : null}
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
			<Surface title="Allocated workspaces">
				<DataTable rows={overview.runtimeWorkspaces} empty={<EmptyState title="No isolated workspaces" body="Workflow implementation steps will allocate workspaces." />} columns={[
					{ key: 'task', label: 'Task', render: (row) => <span className="mono">{String(row.taskId ?? '')}</span> },
					{ key: 'project', label: 'Project', render: (row) => <span className="mono">{String(row.projectId ?? '')}</span> },
					{ key: 'owner', label: 'Owner', render: (row) => String(row.ownerAgentId ?? '') },
					{ key: 'isolation', label: 'Isolation', render: (row) => <span className="mono">{String(row.isolationType ?? '')}</span> },
					{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
				]} />
			</Surface>
		</>
	);
}

export function PolicySecurityPage({ overview, mutate }: { overview: Overview; mutate: Mutate }) {
	const defaultProfile = String(overview.sandboxProfiles[0]?.id ?? 'default_docker');
	const [profileId, setProfileId] = useState(defaultProfile);
	const [selectedRevision, setSelectedRevision] = useState<PolicyRevision | null>(null);
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
						{ key: 'request', label: 'Request', render: (row) => <span className="mono">{String(row.actionRequestId ?? '')}</span> },
						{ key: 'scope', label: 'Scope', render: (row) => <span className="mono">{String(row.projectId ?? '')} / {String(row.jobId ?? '')} / {String(row.agentId ?? '')}</span> },
						{ key: 'tool', label: 'Tool', render: (row) => <span className="mono">{String(row.tool ?? '')} {String(row.runtimeId ?? '')}</span> },
						{ key: 'command', label: 'Command', render: (row) => <span className="mono">{String(row.command ?? '')} {(row.commandArgv ?? []).join(' ')}</span> },
						{ key: 'path', label: 'Path', render: (row) => <span className="mono">{String(row.workspaceId ?? '')} {String(row.path ?? '')}</span> },
						{ key: 'lifecycle', label: 'Lifecycle', render: (row) => <span>{String(row.grantedBy ?? '')} {String(row.grantedAt ?? '')} / expires {String(row.expiresAt ?? '')} / consumed {String(row.consumedAt ?? 'not consumed')}</span> },
						{ key: 'reason', label: 'Reason', render: (row) => String(row.revokeReason ?? row.reason ?? '') },
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

export function MemoryPage({ overview, retrievalStatus }: { overview: Overview; retrievalStatus: RetrievalStatus | null }) {
	return (
		<>
			<PageHeader kicker="Semantic context" title="Memory & Retrieval" summary="SQLite is canonical. FAISS, NumPy and future vector stores are rebuildable indexes, not source of truth." />
			<div className="grid two">
				<Surface title="Retrieval backend">
					<div className="metric-value">{String(retrievalStatus?.backend ?? 'unknown')}</div>
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

type EvidencePackage = EvidenceDetailResponse['evidencePackage'];

function redactVisibleText(value: unknown, fallback = 'not recorded') {
	const raw = typeof value === 'string'
		? value
		: value === undefined || value === null
			? fallback
			: JSON.stringify(value, null, 2);
	return raw
		.replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, '[redacted]')
		.replace(/\bsk-[A-Za-z0-9_-]{8,}/gi, '[redacted]')
		.replace(/\bghp_[A-Za-z0-9_]{12,}/gi, '[redacted]')
		.replace(/\bgithub_pat_[A-Za-z0-9_]{20,}/gi, '[redacted]')
		.replace(/\bglpat-[A-Za-z0-9_-]{12,}/gi, '[redacted]')
		.replace(/\bxox[baprs]-[A-Za-z0-9-]{10,}/gi, '[redacted]')
		.replace(/\bAKIA[0-9A-Z]{16}\b/g, '[redacted]')
		.replace(/(["']?(?:api[_-]?key|authorization|credential|secret|token|password|client[_-]?secret|clientSecret|private[_-]?key|privateKey|OPENAI_API_KEY)["']?\s*[:=]\s*["']?)[^"',\s}]+(["']?)/gi, '$1[redacted]$2')
		.replace(/([?&](?:api[_-]?key|token|secret)=)[^&\s"]+/gi, '$1[redacted]')
		.replace(/\b(?:api[_-]?key|authorization|credential|secret|token|password|client[_-]?secret|clientSecret|private[_-]?key|privateKey|OPENAI_API_KEY)\s*[:=]\s*"?[^",\s}]+/gi, '[redacted]');
}

function redactedJson(value: unknown, fallback = '[]') {
	return redactVisibleText(value, fallback);
}

function artifactNameLower(artifact: Artifact) {
	return artifactDisplayName(artifact).toLowerCase();
}

function findPatchArtifact(artifacts: Artifact[]) {
	return artifacts.find((artifact) => {
		const name = artifactNameLower(artifact);
		const kind = String(artifact.kind ?? '').toLowerCase();
		return name === 'diff.patch' || name.endsWith('.patch') || kind.includes('patch');
	}) ?? null;
}

function findSecurityArtifact(artifacts: Artifact[]) {
	return artifacts.find((artifact) => {
		const name = artifactNameLower(artifact);
		const kind = String(artifact.kind ?? '').toLowerCase();
		return name === 'security-findings.json' || (name.includes('security') && name.includes('finding')) || kind.includes('security');
	}) ?? null;
}

function hasRealPatchChanges(text: string) {
	const trimmed = text.trim();
	if (!trimmed) return false;
	let inHunk = false;
	for (const line of trimmed.split(/\r?\n/)) {
		if (line.startsWith('diff --git')) inHunk = false;
		if (line.startsWith('@@')) {
			inHunk = true;
			continue;
		}
		if (!inHunk) continue;
		if ((line.startsWith('+') && !line.startsWith('+++')) || (line.startsWith('-') && !line.startsWith('---'))) return true;
	}
	return false;
}

function evidenceDiffChangedFiles(evidence: EvidencePackage | null) {
	const summary = objectRecord(evidence?.diffSummary);
	const changed = Number(summary?.filesChanged ?? summary?.changedFiles ?? summary?.changed_files);
	return Number.isFinite(changed) ? changed : null;
}

function evidenceLink(href: string, label: string, id: unknown) {
	const value = String(id ?? '');
	return value ? <a href={href}>{label} {value}</a> : <span className="muted">not linked</span>;
}

export function EvidencePage({ overview, token }: { overview: Overview; token: string }) {
	const [selectedEvidenceId, setSelectedEvidenceId] = useState('');
	const [detail, setDetail] = useState<EvidenceDetailResponse | null>(null);
	const [detailLoading, setDetailLoading] = useState(false);
	const [detailError, setDetailError] = useState('');
	const [diffPayload, setDiffPayload] = useState<ArtifactPayload | null>(null);
	const [diffLoading, setDiffLoading] = useState(false);
	const [diffError, setDiffError] = useState('');
	const [securityPayload, setSecurityPayload] = useState<ArtifactPayload | null>(null);
	const [securityLoading, setSecurityLoading] = useState(false);
	const [securityError, setSecurityError] = useState('');
	const [previewArtifact, setPreviewArtifact] = useState<Artifact | null>(null);
	const [previewPayload, setPreviewPayload] = useState<ArtifactPayload | null>(null);
	const [previewLoadingId, setPreviewLoadingId] = useState('');
	const [downloadLoadingId, setDownloadLoadingId] = useState('');
	const [previewError, setPreviewError] = useState('');
	useEffect(() => {
		if (!selectedEvidenceId) {
			setDetail(null);
			setDetailError('');
			return;
		}
		const controller = new AbortController();
		setDetail(null);
		setDetailError('');
		setDetailLoading(true);
		void getEvidenceDetail(selectedEvidenceId, controller.signal)
			.then((payload) => setDetail(payload))
			.catch((error) => {
				if (!controller.signal.aborted) setDetailError(error instanceof Error ? error.message : 'Evidence detail failed.');
			})
			.finally(() => {
				if (!controller.signal.aborted) setDetailLoading(false);
			});
		return () => controller.abort();
	}, [selectedEvidenceId]);
	const selectedPackage = detail?.evidencePackage ?? null;
	const detailArtifacts = detail?.artifacts ?? [];
	const patchArtifact = useMemo(() => findPatchArtifact(detailArtifacts), [detailArtifacts]);
	const securityArtifact = useMemo(() => findSecurityArtifact(detailArtifacts), [detailArtifacts]);
	useEffect(() => {
		setDiffPayload(null);
		setDiffError('');
		if (!selectedEvidenceId || !patchArtifact) {
			setDiffLoading(false);
			return;
		}
		const artifactId = String(patchArtifact.id ?? '');
		if (!artifactId) return;
		let active = true;
		setDiffLoading(true);
		void fetchEvidenceArtifact(token, selectedEvidenceId, artifactId)
			.then((payload) => {
				if (active) setDiffPayload(payload);
			})
			.catch((error) => {
				if (active) setDiffError(error instanceof Error ? error.message : 'Diff artifact preview failed.');
			})
			.finally(() => {
				if (active) setDiffLoading(false);
			});
		return () => { active = false; };
	}, [patchArtifact, selectedEvidenceId, token]);
	useEffect(() => {
		setSecurityPayload(null);
		setSecurityError('');
		if (!selectedEvidenceId || !securityArtifact) {
			setSecurityLoading(false);
			return;
		}
		const artifactId = String(securityArtifact.id ?? '');
		if (!artifactId) return;
		let active = true;
		setSecurityLoading(true);
		void fetchEvidenceArtifact(token, selectedEvidenceId, artifactId)
			.then((payload) => {
				if (active) setSecurityPayload(payload);
			})
			.catch((error) => {
				if (active) setSecurityError(error instanceof Error ? error.message : 'Security findings preview failed.');
			})
			.finally(() => {
				if (active) setSecurityLoading(false);
			});
		return () => { active = false; };
	}, [securityArtifact, selectedEvidenceId, token]);
	const openPreview = async (artifact: Artifact) => {
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
	const downloadArtifact = async (artifact: Artifact) => {
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setPreviewError('Artifact metadata is incomplete.');
			return;
		}
		setPreviewError('');
		setDownloadLoadingId(artifactId);
		try {
			await downloadEvidenceArtifact(token, evidenceId, artifactId, artifactDisplayName(artifact));
		} catch (error) {
			setPreviewError(error instanceof Error ? error.message : 'Artifact download failed.');
		} finally {
			setDownloadLoadingId('');
		}
	};
	const diffText = redactVisibleText(diffPayload?.text ?? '', '');
	const patchHasChanges = hasRealPatchChanges(diffPayload?.text ?? '');
	const changedFiles = evidenceDiffChangedFiles(selectedPackage);
	const modelCalls = Array.isArray(selectedPackage?.modelCalls) ? selectedPackage.modelCalls : [];
	const toolCalls = Array.isArray(selectedPackage?.toolCalls) ? selectedPackage.toolCalls : [];
	const packageArtifactRefs = Array.isArray(selectedPackage?.artifacts) ? selectedPackage.artifacts : [];
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
						{
							key: 'detail',
							label: 'Detail',
							render: (row) => {
								const evidenceId = String(row.id ?? '');
								return (
									<button className="button" type="button" aria-label={`View evidence package ${evidenceId}`} disabled={!evidenceId || detailLoading} onClick={() => setSelectedEvidenceId(evidenceId)}>
										{selectedEvidenceId === evidenceId && detailLoading ? 'Loading' : 'View'}
									</button>
								);
							},
						},
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
								const downloading = downloadLoadingId === String(row.id ?? '');
								return (
									<div className="inline" aria-busy={loading || downloading}>
										<button className="button" type="button" aria-label={`Preview artifact ${name}`} disabled={loading} onClick={() => void openPreview(row)}>
											{loading ? 'Opening' : 'Preview'}
										</button>
										<button className="button" type="button" aria-label={`Download artifact ${name}`} disabled={downloading} onClick={() => void downloadArtifact(row)}>
											{downloading ? 'Downloading' : 'Download'}
										</button>
									</div>
								);
							},
						},
					]} />
				</Surface>
			</div>
			<div className="stack">
				{detailError ? <div className="form-error" role="alert">{detailError}</div> : null}
				{selectedEvidenceId && !selectedPackage ? (
					<Surface title="Evidence detail">
						<EmptyState title={detailLoading ? 'Loading evidence detail' : 'Evidence detail unavailable'} body="The detail endpoint must return package metadata, test records and artifacts before evidence can be audited." />
					</Surface>
				) : null}
				{selectedPackage ? (
					<>
						<Surface title="Evidence detail">
							<div className="stack">
								<div className="inline">
									<Badge tone={toneForStatus(String(selectedPackage.qaVerdict ?? ''))}>QA {String(selectedPackage.qaVerdict ?? 'not_started')}</Badge>
									<Badge>{detailArtifacts.length} artifacts</Badge>
									{changedFiles !== null ? <span className="mono">changed files {changedFiles}</span> : null}
								</div>
								<DataTable rows={[
									{ label: 'Evidence package', value: <span className="mono">{String(selectedPackage.id ?? '')}</span> },
									{ label: 'Created at', value: <span className="mono">{String(selectedPackage.createdAt ?? '')}</span> },
									{ label: 'Project', value: <span className="mono">{String(selectedPackage.projectId ?? '')}</span> },
									{ label: 'Workflow run', value: evidenceLink('#workflows', 'Workflow', selectedPackage.workflowRunId) },
									{ label: 'Job', value: evidenceLink('#jobs', 'Job', selectedPackage.jobId) },
									{ label: 'Agent run', value: evidenceLink('#agents', 'Agent run', selectedPackage.agentRunId ?? selectedPackage.agentId) },
									{ label: 'Workspace', value: <span className="mono">{String(selectedPackage.workspaceId ?? 'not linked')}</span> },
									{ label: 'Runtime', value: <span className="mono">{String(selectedPackage.runtimeId ?? 'not linked')}</span> },
									{ label: 'Test plan', value: redactVisibleText(selectedPackage.testPlan, '') },
								]} empty={<EmptyState title="No metadata" body="Evidence detail metadata was not returned." />} columns={[
									{ key: 'label', label: 'Metadata', render: (row) => row.label },
									{ key: 'value', label: 'Value', render: (row) => row.value },
								]} />
								<div>
									<div className="metric-label">Runtime health</div>
									<pre className="artifact-preview">{redactedJson(selectedPackage.runtimeHealth, '{}')}</pre>
								</div>
								<div>
									<div className="metric-label">Hashes</div>
									<pre className="artifact-preview">{redactedJson(selectedPackage.hashes, '{}')}</pre>
								</div>
							</div>
						</Surface>
						<Surface title="Evidence artifacts">
							<DataTable rows={detailArtifacts} empty={<EmptyState title="No artifacts" body="This evidence package has no downloadable artifacts." />} columns={[
								{ key: 'name', label: 'Name', render: (row) => artifactDisplayName(row) },
								{ key: 'kind', label: 'Kind', render: (row) => <span className="mono">{String(row.kind ?? '')}</span> },
								{ key: 'size', label: 'Size', render: (row) => artifactSizeLabel(row) },
								{ key: 'sha256', label: 'SHA-256', render: (row) => <span className="mono">{String(row.hash ?? 'not recorded')}</span> },
								{
									key: 'action',
									label: 'Action',
									render: (row) => {
										const name = artifactDisplayName(row);
										const loading = previewLoadingId === String(row.id ?? '');
										const downloading = downloadLoadingId === String(row.id ?? '');
										return (
											<div className="inline" aria-busy={loading || downloading}>
												<button className="button" type="button" aria-label={`Preview artifact ${name}`} disabled={loading} onClick={() => void openPreview(row)}>
													{loading ? 'Opening' : 'Preview'}
												</button>
												<button className="button" type="button" aria-label={`Download artifact ${name}`} disabled={downloading} onClick={() => void downloadArtifact(row)}>
													{downloading ? 'Downloading' : 'Download'}
												</button>
											</div>
										);
									},
								},
							]} />
							{packageArtifactRefs.length ? (
								<div className="stack">
									<div className="metric-label">Package artifact refs</div>
									<pre className="artifact-preview">{redactedJson(packageArtifactRefs)}</pre>
								</div>
							) : null}
						</Surface>
						<Surface title="Diff viewer">
							<div className="stack">
								<div className="inline">
									<Badge tone={patchArtifact && patchHasChanges ? 'ok' : 'warn'}>{patchArtifact && patchHasChanges ? 'real changes' : 'no real changes'}</Badge>
									{changedFiles !== null ? <span className="mono">changed files {changedFiles}</span> : null}
									{patchArtifact ? <span className="mono">sha256 {String(patchArtifact.hash ?? 'not recorded')}</span> : null}
								</div>
								{diffError ? <div className="form-error" role="alert">{diffError}</div> : null}
								{diffLoading ? <EmptyState title="Loading diff artifact" body="The patch is read through the protected artifact endpoint." /> : null}
								{!patchArtifact ? <EmptyState title="No patch artifact recorded" body="Diff refs without a downloadable patch are not sufficient evidence of code changes." /> : null}
								{patchArtifact && diffPayload && !patchHasChanges ? <EmptyState title="Patch artifact is empty; no changes are proven." body="The evidence package does not prove a real file diff." /> : null}
								{patchArtifact && diffPayload?.text ? <pre className="artifact-preview">{diffText}</pre> : null}
							</div>
						</Surface>
						<Surface title="Security findings">
							<div className="stack">
								{securityError ? <div className="form-error" role="alert">{securityError}</div> : null}
								{securityLoading ? <EmptyState title="Loading security findings" body="Findings are read from the linked artifact." /> : null}
								{!securityArtifact ? <EmptyState title="No security findings artifact" body="No security-findings.json artifact is linked to this evidence package." /> : null}
								{securityArtifact ? (
									<div className="inline">
										<Badge>{artifactDisplayName(securityArtifact)}</Badge>
										<span className="mono">sha256 {String(securityArtifact.hash ?? 'not recorded')}</span>
									</div>
								) : null}
								{securityPayload?.text ? <pre className="artifact-preview">{redactVisibleText(securityPayload.text, '')}</pre> : null}
							</div>
						</Surface>
						<Surface title="Model and tool calls">
							<div className="stack">
								<div className="inline">
									<Badge>{modelCalls.length} model calls</Badge>
									<Badge>{toolCalls.length} tool calls</Badge>
								</div>
								<div>
									<div className="metric-label">Model calls</div>
									<pre className="artifact-preview">{redactedJson(modelCalls)}</pre>
								</div>
								<div>
									<div className="metric-label">Tool calls</div>
									<pre className="artifact-preview">{redactedJson(toolCalls)}</pre>
								</div>
								<div>
									<div className="metric-label">Policy decisions and approvals</div>
									<pre className="artifact-preview">{redactedJson({ policyDecisions: selectedPackage.policyDecisions, approvals: selectedPackage.approvals }, '{}')}</pre>
								</div>
							</div>
						</Surface>
					</>
				) : null}
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
							{downloadLoadingId ? <div className="sr-only" role="status">Downloading artifact</div> : null}
							{previewPayload?.text ? (
								<pre className="artifact-preview">{redactVisibleText(previewPayload.text, '')}</pre>
							) : (
								<EmptyState title={previewLoadingId ? 'Loading artifact' : 'Binary or empty artifact'} body="Non-text artifacts remain downloadable, but are not rendered inline." />
							)}
							<button className="button primary" type="button" disabled={downloadLoadingId === String(previewArtifact.id ?? '')} aria-label={`Download preview artifact ${artifactDisplayName(previewArtifact)}`} onClick={() => void downloadArtifact(previewArtifact)}>
								{downloadLoadingId === String(previewArtifact.id ?? '') ? 'Downloading artifact' : 'Download artifact'}
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
	const [provider, setProvider] = useState('ollama');
	const [model, setModel] = useState('');
	const [maxCostUsd, setMaxCostUsd] = useState('1');
	const [maxTokens, setMaxTokens] = useState('4000');
	const [allowRemote, setAllowRemote] = useState(false);
	const [allowLocal, setAllowLocal] = useState(true);
	const [error, setError] = useState('');
	const providerCatalog = useMemo(
		() => [
			{ provider: 'ollama', models: runtimeProviders?.ollama.models ?? [], remote: false },
		],
		[runtimeProviders],
	);
	const modelOptions = providerCatalog.find((item) => item.provider === provider)?.models ?? [];
	useEffect(() => {
		const currentCatalog = providerCatalog.find((item) => item.provider === provider);
		if (!currentCatalog || (model && currentCatalog.models.includes(model))) return;
		setModel(currentCatalog.models[0] ?? '');
	}, [model, provider, providerCatalog]);
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
			createModelGatewayRolePolicy(token, {
				id: policyId,
				role: policyId,
				routingProfileId: 'balanced_best_value',
				preferred: [{ provider, model }],
				fallback: [],
				maxCostPerTaskUsd: maxCost,
				maxTokensPerRun: tokenLimit,
				allowRemote,
				allowLocal,
				allowCli: false,
				allowApi: true,
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

export function GovernancePage({ overview, selectedProject, mutate }: { overview: Overview; selectedProject: Project | null; mutate: Mutate }) {
	const project = selectedProject;
	const [riskTitle, setRiskTitle] = useState('');
	const [riskSeverity, setRiskSeverity] = useState<RiskSeverity>('medium');
	const [riskMitigation, setRiskMitigation] = useState('');
	const [decisionTitle, setDecisionTitle] = useState('');
	const [decisionStatus, setDecisionStatus] = useState<ArchitectureDecisionStatus>('proposed');
	const [decisionContext, setDecisionContext] = useState('');
	const [decisionText, setDecisionText] = useState('');
	const [nextStepTitle, setNextStepTitle] = useState('');
	const [nextStepPriority, setNextStepPriority] = useState<NextStepPriority>('medium');
	const [governanceFilter, setGovernanceFilter] = useState('');
	const [riskStatusFilter, setRiskStatusFilter] = useState<'all' | RiskStatus>('all');
	const [riskUpdateId, setRiskUpdateId] = useState('');
	const [riskUpdateStatus, setRiskUpdateStatus] = useState<RiskStatus>('monitoring');
	const [error, setError] = useState('');
	const query = governanceFilter.trim().toLowerCase();
	const matchesQuery = (...values: Array<string | null | undefined>) =>
		!query || values.some((value) => String(value ?? '').toLowerCase().includes(query));
	const filteredRisks = overview.riskRegister.filter(
		(risk) =>
			(riskStatusFilter === 'all' || risk.status === riskStatusFilter) &&
			matchesQuery(risk.title, risk.severity, risk.status, risk.owner, risk.mitigation),
	);
	const filteredDecisions = overview.architectureDecisions.filter((decision) =>
		matchesQuery(decision.title, decision.status, decision.context, decision.decision),
	);
	const filteredNextSteps = overview.nextSteps.filter((step) =>
		matchesQuery(step.title, step.priority, step.status, step.owner),
	);
	const selectedRiskId = riskUpdateId || overview.riskRegister[0]?.id || '';
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
	const saveRiskUpdate = () => {
		if (!selectedRiskId) {
			setError('A risk is required before updating status.');
			return;
		}
		setError('');
		void mutate((token) => updateRisk(token, selectedRiskId, { status: riskUpdateStatus }));
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
				<div className="inline">
					<Badge tone={project ? 'ok' : 'warn'}>{project ? project.name : 'no operational project'}</Badge>
					{project ? null : <span className="field-help">Select an active operational project in Settings before creating governance records.</span>}
				</div>
				<div className="grid three">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="risk-title">Risk title</label>
							<input id="risk-title" className="input" value={riskTitle} onChange={(event) => setRiskTitle(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="risk-severity">Risk severity</label>
							<select id="risk-severity" className="select" value={riskSeverity} onChange={(event) => setRiskSeverity(event.target.value as RiskSeverity)}>
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
							<select id="decision-status" className="select" value={decisionStatus} onChange={(event) => setDecisionStatus(event.target.value as ArchitectureDecisionStatus)}>
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
							<select id="next-step-priority" className="select" value={nextStepPriority} onChange={(event) => setNextStepPriority(event.target.value as NextStepPriority)}>
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
			<div className="grid two">
				<Surface title="Governance filters">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="governance-filter">Governance filter</label>
							<input
								id="governance-filter"
								className="input"
								value={governanceFilter}
								onChange={(event) => setGovernanceFilter(event.target.value)}
								placeholder="Filter risks, decisions, and next steps"
							/>
						</div>
						<div className="field">
							<label htmlFor="risk-status-filter">Risk status filter</label>
							<select
								id="risk-status-filter"
								className="select"
								value={riskStatusFilter}
								onChange={(event) => setRiskStatusFilter(event.target.value as 'all' | RiskStatus)}
							>
								<option value="all">all</option>
								<option value="open">open</option>
								<option value="monitoring">monitoring</option>
								<option value="mitigating">mitigating</option>
								<option value="mitigated">mitigated</option>
								<option value="accepted">accepted</option>
								<option value="closed">closed</option>
							</select>
						</div>
					</div>
				</Surface>
				<Surface title="Risk update form">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="risk-update-id">Risk to update</label>
							<select
								id="risk-update-id"
								className="select"
								value={selectedRiskId}
								disabled={!overview.riskRegister.length}
								onChange={(event) => setRiskUpdateId(event.target.value)}
							>
								{overview.riskRegister.map((risk) => (
									<option key={risk.id} value={risk.id}>{risk.title}</option>
								))}
							</select>
						</div>
						<div className="field">
							<label htmlFor="risk-update-status">Risk update status</label>
							<select
								id="risk-update-status"
								className="select"
								value={riskUpdateStatus}
								onChange={(event) => setRiskUpdateStatus(event.target.value as RiskStatus)}
							>
								<option value="open">open</option>
								<option value="monitoring">monitoring</option>
								<option value="mitigating">mitigating</option>
								<option value="mitigated">mitigated</option>
								<option value="accepted">accepted</option>
								<option value="closed">closed</option>
							</select>
						</div>
						<button className="button primary" type="button" disabled={!selectedRiskId} onClick={saveRiskUpdate}>Update risk status</button>
					</div>
				</Surface>
			</div>
			<div className="grid three">
				<Surface title="Risk register">
					<DataTable rows={filteredRisks} empty={<EmptyState title="No risks" body="Open technical and product risks appear here." />} columns={[
						{ key: 'title', label: 'Risk', render: (row) => String(row.title ?? '') },
						{ key: 'severity', label: 'Severity', render: (row) => <Badge tone={toneForStatus(String(row.severity ?? ''))}>{String(row.severity ?? '')}</Badge> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					]} />
				</Surface>
				<Surface title="Decision records">
					<DataTable rows={filteredDecisions} empty={<EmptyState title="No decisions" body="Architecture decisions should be explicit and linked to risks." />} columns={[
						{ key: 'title', label: 'Decision', render: (row) => String(row.title ?? '') },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					]} />
				</Surface>
				<Surface title="Next steps">
					<DataTable rows={filteredNextSteps} empty={<EmptyState title="No next steps" body="Mitigations and follow-up work appear here." />} columns={[
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
	const [transport, setTransport] = useState<McpTransport>('stdio');
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
							<select id="mcp-transport" className="select" value={transport} onChange={(event) => setTransport(event.target.value as McpTransport)}>
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
