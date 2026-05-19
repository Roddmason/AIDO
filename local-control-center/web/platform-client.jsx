import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
	Background,
	Controls,
	MarkerType,
	ReactFlow,
} from '@xyflow/react';
import dagre from 'dagre';
import * as Tooltip from '@radix-ui/react-tooltip';
import {
	Activity,
	Bot,
	Boxes,
	Database,
	FolderPlus,
	KeyRound,
	Monitor,
	Plug,
	RefreshCw,
	ScrollText,
	Server,
	ShieldCheck,
	Sparkles,
	Users,
	Video,
	Workflow,
	Zap,
} from 'lucide-react';
import { sileo } from 'sileo';

const PLATFORM_SECTIONS = new Set([
	'overview',
	'projects',
	'sessions',
	'jobs',
	'teams',
	'ide',
	'prompts',
	'memory',
	'integrations',
	'design',
	'settings',
]);

const GRAPH_NODE_WIDTH = 190;
const GRAPH_NODE_HEIGHT = 64;

export function isPlatformSection(section) {
	return PLATFORM_SECTIONS.has(section);
}

function describeError(error) {
	return error instanceof Error ? error.message : String(error);
}

function statusTone(status) {
	switch (status) {
		case 'present':
		case 'ready':
		case 'available':
		case 'configured':
		case 'active':
		case 'completed':
			return 'green';
		case 'available-via-docker':
		case 'running':
			return 'blue';
		case 'missing-secret':
		case 'docker-missing':
		case 'missing':
		case 'queued':
		case 'approval_required':
			return 'yellow';
		case 'failed':
		case 'blocked':
			return 'red';
		default:
			return 'gray';
	}
}

function splitList(value) {
	return String(value || '')
		.split(/[\n,]/)
		.map(item => item.trim())
		.filter(Boolean);
}

function parseJson(value, fallback) {
	try {
		return value ? JSON.parse(value) : fallback;
	} catch {
		return fallback;
	}
}

function formatDateTime(value) {
	if (!value) {
		return '';
	}
	const date = new Date(value);
	return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function truncateText(value, maxLength = 180) {
	const text = typeof value === 'string' ? value : JSON.stringify(value || {});
	if (text.length <= maxLength) {
		return text;
	}
	return `${text.slice(0, maxLength - 3)}...`;
}

function summarizeJobPayload(job) {
	const payload = job?.payload || {};
	if (payload.idea) {
		return `idea: ${truncateText(payload.idea)}`;
	}
	if (payload.prompt) {
		return `prompt: ${truncateText(payload.prompt)}`;
	}
	if (payload.pipelineId) {
		return `pipeline: ${payload.pipelineId}`;
	}
	if (payload.chatId) {
		return `chat: ${payload.chatId}`;
	}
	return truncateText(payload);
}

function latestJobEvent(events, jobId) {
	return (events || []).find(event => event.jobId === jobId) || null;
}

function relatedJobRefs(job) {
	const payload = job?.payload || {};
	return [
		payload.pipelineId ? { section: 'pipelines', label: `pipeline:${payload.pipelineId}` } : null,
		payload.chatId ? { section: 'chats', label: `chat:${payload.chatId}` } : null,
		payload.sessionId ? { section: 'sessions', label: `session:${payload.sessionId}` } : null,
		payload.promptPackId ? { section: 'prompts', label: `prompt:${payload.promptPackId}` } : null,
	].filter(Boolean);
}

async function platformFetch(path, { method = 'GET', token = '', body } = {}) {
	const response = await fetch(path, {
		method,
		headers: {
			'Content-Type': 'application/json',
			...(token ? { 'X-Local-Control-Token': token } : {}),
		},
		body: body === undefined ? undefined : JSON.stringify(body),
	});
	if (!response.ok) {
		const payload = await response.json().catch(() => ({}));
		throw new Error(payload.error || `Request failed (${response.status})`);
	}
	return response.json();
}

function usePlatformControlPlane() {
	const [overview, setOverview] = useState(null);
	const [token, setToken] = useState('');
	const [loading, setLoading] = useState(true);
	const [busy, setBusy] = useState(false);

	const refresh = useCallback(async () => {
		const [handshake, nextOverview] = await Promise.all([
			platformFetch('/api/v1/security/handshake'),
			platformFetch('/api/v1/overview'),
		]);
		setToken(handshake.token || '');
		setOverview(nextOverview);
		setLoading(false);
		return nextOverview;
	}, []);

	const mutate = useCallback(
		async (path, body, { method = 'POST' } = {}) => {
			setBusy(true);
			try {
				const result = await platformFetch(path, { method, token, body });
				await refresh();
				return result;
			} finally {
				setBusy(false);
			}
		},
		[refresh, token],
	);

	useEffect(() => {
		void refresh().catch(error => {
			setLoading(false);
			sileo.error({
				title: 'Platform API unavailable',
				description: describeError(error),
			});
		});
	}, [refresh]);

	useEffect(() => {
		if (typeof window === 'undefined' || typeof window.EventSource !== 'function') {
			return undefined;
		}
		const stream = new window.EventSource('/api/v1/events');
		stream.addEventListener('snapshot', event => {
			try {
				setOverview(JSON.parse(event.data));
			} catch {
				// Ignore malformed local stream frames.
			}
		});
		stream.onerror = () => {
			stream.close();
		};
		return () => stream.close();
	}, []);

	return {
		overview,
		token,
		loading,
		busy,
		refresh,
		mutate,
	};
}

function StatusPill({ status }) {
	return <span className={`platform-status ${statusTone(status)}`}>{status || 'unknown'}</span>;
}

function Metric({ icon: Icon, label, value, meta }) {
	return (
		<div className="platform-metric">
			<div className="platform-metric-icon" aria-hidden="true">
				<Icon size={18} />
			</div>
			<div>
				<strong>{value}</strong>
				<span>{label}</span>
				{meta ? <small>{meta}</small> : null}
			</div>
		</div>
	);
}

function PlatformPanel({ title, subtitle, actions, children }) {
	return (
		<section className="platform-panel">
			<div className="platform-panel-head">
				<div>
					<h3>{title}</h3>
					{subtitle ? <p>{subtitle}</p> : null}
				</div>
				{actions ? <div className="platform-actions">{actions}</div> : null}
			</div>
			{children}
		</section>
	);
}

function IconButton({ title, onClick, disabled, children }) {
	return (
		<Tooltip.Provider delayDuration={220}>
			<Tooltip.Root>
				<Tooltip.Trigger asChild>
					<button
						type="button"
						className="platform-icon-button"
						onClick={onClick}
						disabled={disabled}
						aria-label={title}
					>
						{children}
					</button>
				</Tooltip.Trigger>
				<Tooltip.Portal>
					<Tooltip.Content className="platform-tooltip" sideOffset={8}>
						{title}
						<Tooltip.Arrow className="platform-tooltip-arrow" />
					</Tooltip.Content>
				</Tooltip.Portal>
			</Tooltip.Root>
		</Tooltip.Provider>
	);
}

function PlatformHeader({ overview, onRefresh, busy }) {
	const activeProject = overview?.projects?.[0] || null;
	const providers = overview?.providers || [];
	const unavailableProviders = providers.filter(provider =>
		['missing', 'missing-secret', 'docker-missing'].includes(provider.status),
	);

	return (
		<div className="platform-command-bar">
			<div className="platform-command-main">
				<span className="platform-eyebrow">Local multi-IA control plane</span>
				<strong>{activeProject?.name || 'No project selected'}</strong>
				<span>{activeProject?.path || overview?.activeWorkspacePath || '127.0.0.1 only'}</span>
			</div>
			<div className="platform-command-search">
				<input placeholder="Command, project, provider or prompt" aria-label="Search platform context" />
			</div>
			<div className="platform-command-health">
				<StatusPill status={unavailableProviders.length ? 'degraded' : 'ready'} />
				<IconButton title="Refresh platform state" onClick={onRefresh} disabled={busy}>
					<RefreshCw size={16} />
				</IconButton>
			</div>
		</div>
	);
}

function PlatformOverview({ overview }) {
	const providers = overview?.providers || [];
	const configured = providers.filter(provider =>
		['available', 'configured', 'ready', 'available-via-docker'].includes(provider.status),
	).length;

	return (
		<div className="platform-grid">
			<Metric icon={FolderPlus} label="Projects" value={overview.projects.length} meta="registered locally" />
			<Metric icon={Users} label="Teams" value={overview.teams.length} meta="per project/version" />
			<Metric icon={Bot} label="Agents" value={overview.agents.length} meta="provider scoped" />
			<Metric icon={Plug} label="Providers" value={`${configured}/${providers.length}`} meta="available or configured" />
			<Metric icon={Database} label="Memory" value={overview.memoryItems.length} meta="versioned items" />
			<Metric icon={Workflow} label="Jobs" value={overview.jobs.length} meta="queued/evented" />
			<PlatformPanel title="Execution graph" subtitle="Project, teams, providers and job queue topology.">
				<PlatformPipelineGraph overview={overview} />
			</PlatformPanel>
			<PlatformPanel title="Recent audit" subtitle="Local state changes persisted to SQLite.">
				<div className="platform-list">
					{overview.auditEvents.slice(0, 8).map(event => (
						<div className="platform-row" key={event.id}>
							<div>
								<strong>{event.action}</strong>
								<span>{event.target || event.projectId || 'local'}</span>
							</div>
							<small>{event.createdAt}</small>
						</div>
					))}
					{overview.auditEvents.length === 0 ? <EmptyPlatformState title="No audit events yet" /> : null}
				</div>
			</PlatformPanel>
		</div>
	);
}

function PlatformPipelineGraph({ overview }) {
	const { nodes, edges } = useMemo(() => {
		const graph = new dagre.graphlib.Graph();
		graph.setDefaultEdgeLabel(() => ({}));
		graph.setGraph({ rankdir: 'LR', nodesep: 44, ranksep: 64 });

		const project = overview.projects[0];
		const graphNodes = [];
		const graphEdges = [];
		function addNode(id, data) {
			graph.setNode(id, { width: GRAPH_NODE_WIDTH, height: GRAPH_NODE_HEIGHT });
			graphNodes.push({
				id,
				type: 'default',
				data,
				position: { x: 0, y: 0 },
				className: `platform-flow-node ${data.tone || 'gray'}`,
			});
		}
		function addEdge(source, target, label = '') {
			graph.setEdge(source, target);
			graphEdges.push({
				id: `${source}-${target}`,
				source,
				target,
				label,
				markerEnd: { type: MarkerType.ArrowClosed },
			});
		}

		addNode('project', { label: project?.name || 'Project', sublabel: project?.templateId || 'other', tone: 'blue' });
		overview.teams.slice(0, 6).forEach(team => {
			const teamId = `team-${team.id}`;
			addNode(teamId, { label: team.name, sublabel: team.version, tone: 'green' });
			addEdge('project', teamId);
			const agent = overview.agents.find(entry => entry.teamId === team.id);
			if (agent) {
				const providerId = `provider-${agent.providerId}`;
				if (!graphNodes.some(node => node.id === providerId)) {
					const provider = overview.providers.find(entry => entry.id === agent.providerId);
					addNode(providerId, {
						label: provider?.label || agent.providerId,
						sublabel: provider?.status || 'provider',
						tone: statusTone(provider?.status),
					});
				}
				addEdge(teamId, providerId, agent.name);
			}
		});
		if (overview.jobs.length) {
			addNode('jobs', { label: 'Job queue', sublabel: `${overview.jobs.length} queued/run`, tone: 'yellow' });
			addEdge('project', 'jobs');
		}

		dagre.layout(graph);
		return {
			nodes: graphNodes.map(node => {
				const position = graph.node(node.id);
				return {
					...node,
					position: {
						x: position.x - GRAPH_NODE_WIDTH / 2,
						y: position.y - GRAPH_NODE_HEIGHT / 2,
					},
				};
			}),
			edges: graphEdges,
		};
	}, [overview]);

	return (
		<div className="platform-flow">
			<ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false} nodesConnectable={false}>
				<Background gap={20} size={1} />
				<Controls showInteractive={false} />
			</ReactFlow>
		</div>
	);
}

function ProjectsView({ overview, mutate, busy }) {
	const [draft, setDraft] = useState({
		name: '',
		path: '',
		templateId: overview.projectTemplates[0]?.id || 'other',
		mode: 'create',
	});

	async function submit() {
		if (!draft.path.trim()) {
			return;
		}
		await sileo.promise(
			mutate('/api/v1/projects', {
				name: draft.name.trim(),
				path: draft.path.trim(),
				templateId: draft.templateId,
				createDirectory: draft.mode === 'create',
				registerExisting: draft.mode === 'register',
			}),
			{
				loading: { title: 'Saving project', description: 'Updating SQLite and project registry.' },
				success: { title: 'Project saved', description: 'The project was added to the local control plane.' },
				error: error => ({ title: 'Project failed', description: describeError(error) }),
			},
		);
		setDraft(current => ({ ...current, name: '', path: '' }));
	}

	return (
		<div className="platform-grid">
			<PlatformPanel title="Create or register project" subtitle="Creation makes the folder; registration keeps an existing folder intact.">
				<div className="platform-form-grid">
					<label>
						<span>Name</span>
						<input value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} placeholder="API Project" />
					</label>
					<label>
						<span>Path</span>
						<input value={draft.path} onChange={event => setDraft({ ...draft, path: event.target.value })} placeholder="H:\\Proyectos\\Personales\\api-project" />
					</label>
					<label>
						<span>Template</span>
						<select value={draft.templateId} onChange={event => setDraft({ ...draft, templateId: event.target.value })}>
							{overview.projectTemplates.map(template => (
								<option key={template.id} value={template.id}>
									{template.label}
								</option>
							))}
						</select>
					</label>
					<label>
						<span>Mode</span>
						<select value={draft.mode} onChange={event => setDraft({ ...draft, mode: event.target.value })}>
							<option value="create">Create folder</option>
							<option value="register">Register existing</option>
						</select>
					</label>
				</div>
				<div className="platform-actions end">
					<button type="button" className="btn btn-primary" disabled={busy || !draft.path.trim()} onClick={() => void submit()}>
						<FolderPlus size={16} />
						Save project
					</button>
				</div>
			</PlatformPanel>
			<PlatformPanel title="Registered projects" subtitle="Canonical project records backed by SQLite.">
				<div className="platform-list">
					{overview.projects.map(project => (
						<div className="platform-row" key={project.id}>
							<div>
								<strong>{project.name}</strong>
								<span>{project.path}</span>
							</div>
							<div className="platform-row-actions">
								<span className="pill mono">{project.templateId}</span>
								<StatusPill status={project.source} />
							</div>
						</div>
					))}
				</div>
			</PlatformPanel>
		</div>
	);
}

function SessionsView({ overview }) {
	return (
		<PlatformPanel title="Sessions" subtitle="Named local threads imported from legacy state and ready for job/event linkage.">
			<div className="platform-list">
				{overview.sessions.map(session => {
					const project = overview.projects.find(entry => entry.id === session.projectId);
					const team = overview.teams.find(entry => entry.id === session.teamId);
					return (
						<div className="platform-row" key={session.id}>
							<div>
								<strong>{session.name}</strong>
								<span>{project?.name || session.projectId} / {team?.name || 'unassigned'}</span>
							</div>
							<StatusPill status={session.status} />
						</div>
					);
				})}
				{overview.sessions.length === 0 ? <EmptyPlatformState title="No platform sessions imported yet" /> : null}
			</div>
		</PlatformPanel>
	);
}

function JobsView({ overview, mutate, busy }) {
	const jobs = overview.jobs || [];
	const events = overview.events || [];
	const auditEvents = overview.auditEvents || [];
	const actionRequests = overview.actionRequests || [];
	const countByStatus = status => jobs.filter(job => job.status === status).length;
	const pendingActionCount = actionRequests.filter(action => action.status === 'pending').length;
	const blockedStatuses = new Set(['completed', 'cancelled', 'running']);

	async function approveJob(job) {
		await sileo.promise(
			mutate(`/api/v1/jobs/${job.id}/approve`, { reason: 'Approved from dashboard' }),
			{
				loading: { title: 'Approving job', description: job.kind },
				success: { title: 'Job approved', description: 'The worker can claim it on the next cycle.' },
				error: error => ({ title: 'Approval failed', description: describeError(error) }),
			},
		);
	}

	async function cancelJob(job) {
		await sileo.promise(
			mutate(`/api/v1/jobs/${job.id}/cancel`, { reason: 'Cancelled from dashboard' }),
			{
				loading: { title: 'Cancelling job', description: job.kind },
				success: { title: 'Job cancelled', description: 'Cancelled jobs are excluded from worker claims.' },
				error: error => ({ title: 'Cancel failed', description: describeError(error) }),
			},
		);
	}

	async function retryJob(job) {
		await sileo.promise(
			mutate(`/api/v1/jobs/${job.id}/retry`, { reason: 'Retried from dashboard' }),
			{
				loading: { title: 'Retrying job', description: job.kind },
				success: { title: 'Job queued', description: 'The job is back in the durable queue.' },
				error: error => ({ title: 'Retry failed', description: describeError(error) }),
			},
		);
	}

	async function approveAction(action) {
		await sileo.promise(
			mutate(`/api/v1/jobs/${action.jobId}/actions/${action.id}/approve`, { reason: 'Approved from dashboard' }),
			{
				loading: { title: 'Approving action', description: action.actionType },
				success: { title: 'Action approved', description: 'The job can continue once all actions are approved.' },
				error: error => ({ title: 'Action approval failed', description: describeError(error) }),
			},
		);
	}

	async function denyAction(action) {
		await sileo.promise(
			mutate(`/api/v1/jobs/${action.jobId}/actions/${action.id}/deny`, { reason: 'Denied from dashboard' }),
			{
				loading: { title: 'Denying action', description: action.actionType },
				success: { title: 'Action denied', description: 'The related job was cancelled.' },
				error: error => ({ title: 'Action denial failed', description: describeError(error) }),
			},
		);
	}

	return (
		<div className="platform-grid">
			<Metric icon={Workflow} label="Total jobs" value={jobs.length} meta="durable SQLite queue" />
			<Metric icon={Zap} label="Queued/running" value={`${countByStatus('queued')}/${countByStatus('running')}`} meta="claimable or leased" />
			<Metric icon={ShieldCheck} label="Approvals" value={pendingActionCount} meta="granular actions pending" />
			<Metric icon={Activity} label="Failures" value={countByStatus('failed')} meta="retry candidates" />
			<PlatformPanel title="Jobs and approvals" subtitle="Queue state, leases, latest event and manual control gates.">
				<div className="platform-list">
					{jobs.map(job => {
						const latestEvent = latestJobEvent(events, job.id);
						const relatedRefs = relatedJobRefs(job);
						const pendingActions = actionRequests.filter(action => action.jobId === job.id && action.status === 'pending');
						const canApprove = job.status === 'approval_required' && pendingActions.length === 0;
						const canCancel = !blockedStatuses.has(job.status);
						const canRetry = ['failed', 'cancelled'].includes(job.status);
						return (
							<div className="platform-row wide platform-job-row" key={job.id}>
								<div>
									<div className="platform-job-title">
										<strong>{job.kind}</strong>
										<span className="pill mono">{job.id.slice(0, 8)}</span>
									</div>
									<span className="platform-job-payload mono">{summarizeJobPayload(job)}</span>
									<div className="platform-job-meta">
										<span>Created {formatDateTime(job.createdAt) || 'unknown'}</span>
										<span>Updated {formatDateTime(job.updatedAt) || 'unknown'}</span>
										{job.leaseOwner ? <span>Lease {job.leaseOwner} until {formatDateTime(job.leaseExpiresAt)}</span> : <span>No active lease</span>}
										{latestEvent ? <span>Latest {latestEvent.type} at {formatDateTime(latestEvent.createdAt)}</span> : <span>No job event yet</span>}
									</div>
									{relatedRefs.length ? (
										<div className="pill-row">
											{relatedRefs.map(ref => (
												<a className="pill mono" href={`#${ref.section}`} key={`${job.id}-${ref.label}`}>
													{ref.label}
												</a>
											))}
										</div>
									) : null}
									{pendingActions.length ? (
										<div className="platform-list compact action-request-list">
											{pendingActions.map(action => (
												<div className="platform-row" key={action.id}>
													<div>
														<strong>{action.actionType}</strong>
														<span className="mono">{action.command || action.id}</span>
													</div>
													<div className="platform-row-actions">
														<span className="pill mono">{action.riskLevel}</span>
														<button type="button" className="btn btn-primary btn-compact" disabled={busy} onClick={() => void approveAction(action)}>
															<ShieldCheck size={15} />
															Approve action
														</button>
														<button type="button" className="btn btn-secondary btn-compact" disabled={busy} onClick={() => void denyAction(action)}>
															Deny
														</button>
													</div>
												</div>
											))}
										</div>
									) : null}
								</div>
								<div className="platform-row-actions">
									<StatusPill status={job.status} />
									{canApprove ? (
										<button type="button" className="btn btn-primary btn-compact" disabled={busy} onClick={() => void approveJob(job)}>
											<ShieldCheck size={15} />
											Approve
										</button>
									) : null}
									{canCancel ? (
										<button type="button" className="btn btn-secondary btn-compact" disabled={busy} onClick={() => void cancelJob(job)}>
											Cancel
										</button>
									) : null}
									{canRetry ? (
										<button type="button" className="btn btn-secondary btn-compact" disabled={busy} onClick={() => void retryJob(job)}>
											<RefreshCw size={15} />
											Retry
										</button>
									) : null}
								</div>
							</div>
						);
					})}
					{jobs.length === 0 ? <EmptyPlatformState title="No jobs in the durable queue" /> : null}
				</div>
			</PlatformPanel>
			<PlatformPanel title="Recent job activity" subtitle="Worker events and approval audit entries persisted by the platform store.">
				<div className="platform-list compact">
					{events.filter(event => event.jobId).slice(0, 8).map(event => (
						<div className="platform-row" key={event.id}>
							<div>
								<strong>{event.type}</strong>
								<span>{event.jobId}</span>
							</div>
							<small>{formatDateTime(event.createdAt)}</small>
						</div>
					))}
					{auditEvents.filter(event => event.action.startsWith('job.') || event.action.startsWith('action.')).slice(0, 6).map(event => (
						<div className="platform-row" key={event.id}>
							<div>
								<strong>{event.action}</strong>
								<span>{event.target}</span>
							</div>
							<small>{formatDateTime(event.createdAt)}</small>
						</div>
					))}
					{events.filter(event => event.jobId).length === 0 && auditEvents.filter(event => event.action.startsWith('job.') || event.action.startsWith('action.')).length === 0 ? (
						<EmptyPlatformState title="No job activity recorded yet" />
					) : null}
				</div>
			</PlatformPanel>
		</div>
	);
}

function TeamsView({ overview }) {
	return (
		<div className="platform-card-grid">
			{overview.teams.map(team => {
				const agents = overview.agents.filter(agent => agent.teamId === team.id);
				return (
					<article className="platform-card" key={team.id}>
						<div className="platform-card-head">
							<div>
								<strong>{team.name}</strong>
								<span>{team.version}</span>
							</div>
							<Users size={18} />
						</div>
						<div className="pill-row">
							{team.capabilities.map(capability => (
								<span className="pill mono" key={capability}>{capability}</span>
							))}
						</div>
						<div className="platform-list compact">
							{agents.map(agent => (
								<div className="platform-row" key={agent.id}>
									<div>
										<strong>{agent.name}</strong>
										<span>{agent.role}</span>
									</div>
									<span className="pill mono">{agent.providerId}</span>
								</div>
							))}
						</div>
					</article>
				);
			})}
		</div>
	);
}

function PromptsView({ overview, mutate, busy }) {
	const [draft, setDraft] = useState({
		name: 'Team execution prompt',
		mode: 'manual',
		optimizer: 'typeless-compatible',
		body: '',
		providerIds: 'codex, claudecode',
	});
	const projectId = overview.projects[0]?.id || '';

	async function submit() {
		await sileo.promise(
			mutate('/api/v1/prompts', {
				projectId,
				name: draft.name,
				mode: draft.mode,
				optimizer: draft.optimizer,
				body: draft.body,
				appliesTo: { providerIds: splitList(draft.providerIds), teamIds: [] },
			}),
			{
				loading: { title: 'Saving prompt', description: 'Versioning template and evaluator settings.' },
				success: { title: 'Prompt saved', description: 'The template is available to project providers.' },
				error: error => ({ title: 'Prompt failed', description: describeError(error) }),
			},
		);
		setDraft(current => ({ ...current, body: '' }));
	}

	return (
		<div className="platform-grid">
			<PlatformPanel title="Prompt editor" subtitle="Manual mode is exact; auto mode creates an optimization job for provider-specific use.">
				<div className="platform-form-grid">
					<label>
						<span>Name</span>
						<input value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} />
					</label>
					<label>
						<span>Mode</span>
						<select value={draft.mode} onChange={event => setDraft({ ...draft, mode: event.target.value })}>
							<option value="manual">Manual</option>
							<option value="auto">Auto optimize</option>
						</select>
					</label>
					<label>
						<span>Optimizer</span>
						<input value={draft.optimizer} onChange={event => setDraft({ ...draft, optimizer: event.target.value })} />
					</label>
					<label>
						<span>Providers</span>
						<input value={draft.providerIds} onChange={event => setDraft({ ...draft, providerIds: event.target.value })} />
					</label>
				</div>
				<textarea value={draft.body} onChange={event => setDraft({ ...draft, body: event.target.value })} placeholder="Write the provider/team/project prompt contract." />
				<div className="platform-actions end">
					<button type="button" className="btn btn-primary" disabled={busy || !draft.body.trim()} onClick={() => void submit()}>
						<ScrollText size={16} />
						Save version
					</button>
				</div>
			</PlatformPanel>
			<PlatformPanel title="Prompt templates" subtitle="Latest version per template. Older versions remain queryable through the store.">
				<div className="platform-list">
					{overview.promptTemplates.map(prompt => (
						<div className="platform-row" key={prompt.id}>
							<div>
								<strong>{prompt.name}</strong>
								<span>{prompt.optimizer || 'no optimizer'}</span>
							</div>
							<div className="platform-row-actions">
								<StatusPill status={prompt.mode} />
								<span className="pill mono">v{prompt.version}</span>
							</div>
						</div>
					))}
					{overview.promptTemplates.length === 0 ? <EmptyPlatformState title="No prompt templates yet" /> : null}
				</div>
			</PlatformPanel>
		</div>
	);
}

function MemoryView({ overview, mutate, busy }) {
	const [draft, setDraft] = useState({ scope: 'project', kind: 'note', content: '' });
	const projectId = overview.projects[0]?.id || '';

	async function submit() {
		await sileo.promise(
			mutate('/api/v1/memory', {
				projectId,
				scope: draft.scope,
				scopeId: projectId,
				kind: draft.kind,
				content: draft.content,
				sourceRef: 'ui:memory',
			}),
			{
				loading: { title: 'Saving memory', description: 'Writing a versioned context item.' },
				success: { title: 'Memory saved', description: 'Shared team context was updated.' },
				error: error => ({ title: 'Memory failed', description: describeError(error) }),
			},
		);
		setDraft(current => ({ ...current, content: '' }));
	}

	return (
		<div className="platform-grid">
			<PlatformPanel title="Shared memory" subtitle="Scoped context available to teams and provider adapters.">
				<div className="platform-form-grid">
					<label>
						<span>Scope</span>
						<select value={draft.scope} onChange={event => setDraft({ ...draft, scope: event.target.value })}>
							<option value="project">Project</option>
							<option value="team">Team</option>
							<option value="provider">Provider</option>
						</select>
					</label>
					<label>
						<span>Kind</span>
						<input value={draft.kind} onChange={event => setDraft({ ...draft, kind: event.target.value })} />
					</label>
				</div>
				<textarea value={draft.content} onChange={event => setDraft({ ...draft, content: event.target.value })} placeholder="Persist useful context, constraints, decisions or project facts." />
				<div className="platform-actions end">
					<button type="button" className="btn btn-primary" disabled={busy || !draft.content.trim()} onClick={() => void submit()}>
						<Database size={16} />
						Save memory
					</button>
				</div>
			</PlatformPanel>
			<PlatformPanel title="Memory items" subtitle="Newest items across scopes.">
				<div className="platform-list">
					{overview.memoryItems.map(item => (
						<div className="platform-row wide" key={item.id}>
							<div>
								<strong>{item.kind} / {item.scope}</strong>
								<span>{item.content}</span>
							</div>
							<span className="pill mono">v{item.version}</span>
						</div>
					))}
					{overview.memoryItems.length === 0 ? <EmptyPlatformState title="No memory items yet" /> : null}
				</div>
			</PlatformPanel>
		</div>
	);
}

function IdeView({ overview, mutate, busy }) {
	const [draft, setDraft] = useState({
		editor: 'Codex Desktop',
		workspaceRoot: overview.activeWorkspacePath || '',
		openFiles: '',
		diagnostics: '[]',
	});
	const projectId = overview.projects[0]?.id || '';

	async function submit() {
		await sileo.promise(
			mutate('/api/v1/ide-connections', {
				projectId,
				editor: draft.editor,
				workspaceRoot: draft.workspaceRoot,
				openFiles: splitList(draft.openFiles),
				diagnostics: parseJson(draft.diagnostics, []),
				selection: {},
				terminalContext: {},
			}),
			{
				loading: { title: 'Saving IDE connection', description: 'Updating live workspace context.' },
				success: { title: 'IDE context saved', description: 'Open files and diagnostics are now visible to teams.' },
				error: error => ({ title: 'IDE update failed', description: describeError(error) }),
			},
		);
	}

	return (
		<div className="platform-grid">
			<PlatformPanel title="IDE connection" subtitle="A live IDE bridge should report root, open files, diagnostics, terminal context and selection.">
				<div className="platform-form-grid">
					<label>
						<span>Editor</span>
						<input value={draft.editor} onChange={event => setDraft({ ...draft, editor: event.target.value })} />
					</label>
					<label>
						<span>Workspace root</span>
						<input value={draft.workspaceRoot} onChange={event => setDraft({ ...draft, workspaceRoot: event.target.value })} />
					</label>
				</div>
				<label className="platform-field-full">
					<span>Open files</span>
					<textarea value={draft.openFiles} onChange={event => setDraft({ ...draft, openFiles: event.target.value })} placeholder="One file per line" />
				</label>
				<label className="platform-field-full">
					<span>Diagnostics JSON</span>
					<textarea value={draft.diagnostics} onChange={event => setDraft({ ...draft, diagnostics: event.target.value })} />
				</label>
				<div className="platform-actions end">
					<button type="button" className="btn btn-primary" disabled={busy || !draft.workspaceRoot.trim()} onClick={() => void submit()}>
						<Monitor size={16} />
						Save IDE context
					</button>
				</div>
			</PlatformPanel>
			<PlatformPanel title="Connected IDEs" subtitle="Context is explicit and auditable instead of inferred from arbitrary files.">
				<div className="platform-list">
					{overview.ideConnections.map(connection => (
						<div className="platform-row" key={connection.id}>
							<div>
								<strong>{connection.editor}</strong>
								<span>{connection.workspaceRoot}</span>
							</div>
							<div className="platform-row-actions">
								<StatusPill status={connection.status} />
								<span className="pill mono">{connection.openFiles.length} files</span>
							</div>
						</div>
					))}
					{overview.ideConnections.length === 0 ? <EmptyPlatformState title="No IDE connections yet" /> : null}
				</div>
			</PlatformPanel>
		</div>
	);
}

function IntegrationsView({ overview }) {
	return (
		<div className="platform-card-grid">
			{overview.providers.map(provider => (
				<article className="platform-card" key={provider.id}>
					<div className="platform-card-head">
						<div>
							<strong>{provider.label}</strong>
							<span>{provider.kind}</span>
						</div>
						<Server size={18} />
					</div>
					<div className="platform-row-actions">
						<StatusPill status={provider.status} />
						<span className="pill mono">{provider.models?.[0] || 'no model list'}</span>
					</div>
					<div className="pill-row">
						{provider.capabilities.map(capability => (
							<span className="pill mono" key={capability}>{capability}</span>
						))}
					</div>
					<div className="platform-secret-list">
						{(provider.secretHealth || []).map(secret => (
							<div className="platform-secret" key={secret.id}>
								<KeyRound size={14} />
								<span>{secret.envName || secret.id}</span>
								<StatusPill status={secret.status} />
							</div>
						))}
					</div>
				</article>
			))}
		</div>
	);
}

function DesignLabView({ overview }) {
	const openDesign = overview.openDesign;
	return (
		<div className="platform-grid">
			<PlatformPanel title="Open Design container" subtitle="Isolated daemon/plugin integration. The core only talks through health, execute and artifact mounts.">
				<div className="platform-design-status">
					<div>
						<Sparkles size={22} />
						<strong>{openDesign.label}</strong>
						<span>{openDesign.repository}</span>
					</div>
					<StatusPill status={openDesign.status} />
				</div>
				<table className="kv-table">
					<tbody>
						<tr>
							<th>Health</th>
							<td>{openDesign.healthEndpoint}</td>
						</tr>
						<tr>
							<th>Artifacts</th>
							<td>{openDesign.mountPolicy.artifacts}</td>
						</tr>
						<tr>
							<th>Workspace</th>
							<td>{openDesign.mountPolicy.workspace}</td>
						</tr>
						<tr>
							<th>Secrets</th>
							<td>{openDesign.mountPolicy.secrets}</td>
						</tr>
					</tbody>
				</table>
				{openDesign.reason ? <p className="subtle">{openDesign.reason}</p> : null}
			</PlatformPanel>
			<PlatformPanel title="Motion artifacts" subtitle="HyperFrames remains an artifact generator, not a replacement for normal UI motion.">
				<div className="platform-card-head inline">
					<Video size={20} />
					<div>
						<strong>HyperFrames output lane</strong>
						<span>Videos, title cards, walkthroughs and generated media attach as artifacts.</span>
					</div>
				</div>
				<div className="pill-row">
					{openDesign.capabilities.map(capability => (
						<span className="pill mono" key={capability}>{capability}</span>
					))}
				</div>
			</PlatformPanel>
		</div>
	);
}

function SettingsView({ overview }) {
	return (
		<div className="platform-grid">
			<PlatformPanel title="Local security" subtitle="The dashboard remains loopback-only and mutating routes require a local token.">
				<table className="kv-table">
					<tbody>
						<tr>
							<th>API</th>
							<td>{overview.security.apiVersion}</td>
						</tr>
						<tr>
							<th>Loopback</th>
							<td>{String(overview.security.loopbackOnly)}</td>
						</tr>
						<tr>
							<th>CSRF header</th>
							<td>{overview.security.csrfHeader}</td>
						</tr>
						<tr>
							<th>SQLite</th>
							<td>{overview.dbPath}</td>
						</tr>
					</tbody>
				</table>
			</PlatformPanel>
			<PlatformPanel title="Runtime posture" subtitle="Provider parity is capability-based, not assumed.">
				<div className="platform-list">
					{overview.providers.map(provider => (
						<div className="platform-row" key={provider.id}>
							<div>
								<strong>{provider.label}</strong>
								<span>{provider.sandboxPolicy?.modes?.join(', ') || 'no sandbox modes'}</span>
							</div>
							<StatusPill status={provider.kind} />
						</div>
					))}
				</div>
			</PlatformPanel>
		</div>
	);
}

function EmptyPlatformState({ title }) {
	return (
		<div className="platform-empty">
			<Boxes size={18} />
			<span>{title}</span>
		</div>
	);
}

export function PlatformControlPlane({ section }) {
	const { overview, loading, busy, refresh, mutate } = usePlatformControlPlane();

	if (loading || !overview) {
		return (
			<div className="platform-shell">
				<div className="platform-skeleton" />
				<div className="platform-skeleton short" />
			</div>
		);
	}

	let view = <PlatformOverview overview={overview} />;
	if (section === 'projects') {
		view = <ProjectsView overview={overview} mutate={mutate} busy={busy} />;
	} else if (section === 'sessions') {
		view = <SessionsView overview={overview} />;
	} else if (section === 'jobs') {
		view = <JobsView overview={overview} mutate={mutate} busy={busy} />;
	} else if (section === 'teams') {
		view = <TeamsView overview={overview} />;
	} else if (section === 'ide') {
		view = <IdeView overview={overview} mutate={mutate} busy={busy} />;
	} else if (section === 'prompts') {
		view = <PromptsView overview={overview} mutate={mutate} busy={busy} />;
	} else if (section === 'memory') {
		view = <MemoryView overview={overview} mutate={mutate} busy={busy} />;
	} else if (section === 'integrations') {
		view = <IntegrationsView overview={overview} />;
	} else if (section === 'design') {
		view = <DesignLabView overview={overview} />;
	} else if (section === 'settings') {
		view = <SettingsView overview={overview} />;
	}

	return (
		<div className="platform-shell">
			<PlatformHeader overview={overview} onRefresh={() => void refresh()} busy={busy} />
			{view}
		</div>
	);
}
