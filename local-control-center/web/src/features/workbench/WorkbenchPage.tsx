import {
	Bot,
	CheckCircle2,
	ClipboardCheck,
	Code2,
	FileCheck2,
	FolderKanban,
	GitBranch,
	History,
	ListChecks,
	Rocket,
	ShieldCheck,
	TerminalSquare,
	Workflow,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { LucideIcon } from 'lucide-react';

import { createChat, createPipeline, createSession } from '../../api/client';
import type { ChatCreateResponse, PipelineCreateResponse, SessionCreateResponse } from '../../api/client';
import type { Overview, Project, RuntimeProviders } from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { shortId, toneForStatus } from '../../lib/format';

type Mutate = <T>(operation: (token: string) => Promise<T>, options?: { awaitRefresh?: boolean }) => Promise<T>;
type JsonRecord = Record<string, unknown>;

type WorkbenchPageProps = {
	overview: Overview;
	selectedProject: Project | null;
	runtimeProviders: RuntimeProviders | null;
	mutate: Mutate;
	onSelectProject: (projectId: string) => void;
	onCreateProject: () => void;
	onOpenCommandCenter: () => void;
	onOpenWorkflows: () => void;
	onOpenJobs: () => void;
	onOpenWorkspaces: () => void;
	onOpenEvidence: () => void;
};

type ExpertBlueprint = {
	role: string;
	labelKey: string;
	label: string;
	laneKey: string;
	lane: string;
	responsibilityKey: string;
	responsibility: string;
	icon: LucideIcon;
};

const expertBlueprints: ExpertBlueprint[] = [
	{
		role: 'product_owner',
		labelKey: 'app.workbench.team.role.jp',
		label: 'JP / Product Owner',
		laneKey: 'app.workbench.team.lane.direction',
		lane: 'Direction',
		responsibilityKey: 'app.workbench.team.role.jpBody',
		responsibility: 'Backlog, priority and acceptance criteria for long-running work.',
		icon: ClipboardCheck,
	},
	{
		role: 'technical_lead',
		labelKey: 'app.workbench.team.role.tl',
		label: 'Technical Lead',
		laneKey: 'app.workbench.team.lane.guidance',
		lane: 'Guidance',
		responsibilityKey: 'app.workbench.team.role.tlBody',
		responsibility: 'Technical strategy, handoffs and quality gates across agents.',
		icon: Workflow,
	},
	{
		role: 'architect_agent',
		labelKey: 'app.workbench.team.role.architect',
		label: 'Architecture',
		laneKey: 'app.workbench.team.lane.design',
		lane: 'Design',
		responsibilityKey: 'app.workbench.team.role.architectBody',
		responsibility: 'Architecture decisions, environment assumptions and risk framing.',
		icon: ListChecks,
	},
	{
		role: 'developer',
		labelKey: 'app.workbench.team.role.developer',
		label: 'Engineering',
		laneKey: 'app.workbench.team.lane.build',
		lane: 'Build',
		responsibilityKey: 'app.workbench.team.role.developerBody',
		responsibility: 'Frontend, backend and implementation work inside the selected workspace.',
		icon: Code2,
	},
	{
		role: 'devops',
		labelKey: 'app.workbench.team.role.devops',
		label: 'DevOps',
		laneKey: 'app.workbench.team.lane.environment',
		lane: 'Environment',
		responsibilityKey: 'app.workbench.team.role.devopsBody',
		responsibility: 'Local runtime, scripts, containers and release readiness.',
		icon: TerminalSquare,
	},
	{
		role: 'qa_reviewer',
		labelKey: 'app.workbench.team.role.qa',
		label: 'QA',
		laneKey: 'app.workbench.team.lane.validation',
		lane: 'Validation',
		responsibilityKey: 'app.workbench.team.role.qaBody',
		responsibility: 'Tests, evidence packages and regression verdicts.',
		icon: FileCheck2,
	},
	{
		role: 'security_reviewer',
		labelKey: 'app.workbench.team.role.security',
		label: 'Security',
		laneKey: 'app.workbench.team.lane.guardrails',
		lane: 'Guardrails',
		responsibilityKey: 'app.workbench.team.role.securityBody',
		responsibility: 'Policy, permissions, sandbox posture and sensitive-output review.',
		icon: ShieldCheck,
	},
];

const deliveryStageBlueprints = [
	{ id: 'intake', labelKey: 'app.workbench.delivery.intake', label: 'Intake', owner: 'JP' },
	{ id: 'planning', labelKey: 'app.workbench.delivery.planning', label: 'Planning', owner: 'TL' },
	{ id: 'architecture', labelKey: 'app.workbench.delivery.architecture', label: 'Architecture and environment', owner: 'Architecture + DevOps' },
	{ id: 'implementation', labelKey: 'app.workbench.delivery.implementation', label: 'Implementation', owner: 'Engineering' },
	{ id: 'validation', labelKey: 'app.workbench.delivery.validation', label: 'QA and security', owner: 'QA + Security' },
	{ id: 'delivery', labelKey: 'app.workbench.delivery.delivery', label: 'Deliverables', owner: 'TL + JP' },
];
const NEW_SESSION_ID = '__new_work_session__';

function activeProjects(projects: Project[]) {
	return projects.filter((project) => String(project.status ?? 'active') === 'active');
}

function firstLine(value: string) {
	return value.trim().split(/\r?\n/)[0]?.slice(0, 96) || 'Workbench intake';
}

function formatTime(value: string | undefined) {
	if (!value) return 'not recorded';
	const parsed = Date.parse(value);
	return Number.isNaN(parsed) ? value : new Date(parsed).toLocaleString();
}

function asRecord(value: unknown): JsonRecord {
	return value && typeof value === 'object' && !Array.isArray(value) ? (value as JsonRecord) : {};
}

function textValue(value: unknown, fallback = 'n/a') {
	if (value === null || value === undefined) return fallback;
	const text = String(value).trim();
	return text || fallback;
}

function sortByUpdatedAt<T extends object>(rows: T[]) {
	return [...rows].sort((left, right) => {
		const leftRecord = asRecord(left);
		const rightRecord = asRecord(right);
		const leftDate = Date.parse(textValue(leftRecord.updatedAt ?? leftRecord.createdAt ?? leftRecord.startedAt ?? leftRecord.completedAt, ''));
		const rightDate = Date.parse(textValue(rightRecord.updatedAt ?? rightRecord.createdAt ?? rightRecord.startedAt ?? rightRecord.completedAt, ''));
		return (Number.isNaN(rightDate) ? 0 : rightDate) - (Number.isNaN(leftDate) ? 0 : leftDate);
	});
}

function branchFromOverview(project: Project | null, overview: Overview) {
	if (!project) return 'not detected';
	const projectWorkspaces = sortByUpdatedAt(overview.runtimeWorkspaces.filter((workspace) => workspace.projectId === project.id));
	for (const workspace of projectWorkspaces) {
		const metadata = asRecord(workspace.metadata);
		const branch = metadata.branchName ?? metadata.branch ?? metadata.baseBranch;
		if (branch) return textValue(branch);
	}
	const workflowRuns = sortByUpdatedAt(overview.workflowRuns.filter((run) => run.projectId === project.id));
	for (const run of workflowRuns) {
		const metadata = asRecord(run.metadata);
		const diffSummary = asRecord(metadata.diffSummary);
		const branch = metadata.branch ?? metadata.branchName ?? diffSummary.branch ?? diffSummary.baseBranch;
		if (branch) return textValue(branch);
	}
	return 'not detected';
}

function roleMatches(blueprintRole: string, roleValue: string, idOrName = '') {
	const normalizedRole = roleValue.toLowerCase();
	const normalizedName = idOrName.toLowerCase();
	if (blueprintRole === 'architect_agent') {
		return normalizedRole.includes('architect') || normalizedName.includes('architect');
	}
	if (blueprintRole === 'developer') {
		return ['developer', 'backend_engineer', 'frontend_engineer', 'implementer'].includes(normalizedRole);
	}
	if (blueprintRole === 'qa_reviewer') {
		return normalizedRole === 'qa' || normalizedRole === 'qa_reviewer';
	}
	return normalizedRole === blueprintRole;
}

function stageName(stage: JsonRecord) {
	return textValue(stage.name ?? stage.id ?? stage.stage, 'stage');
}

function stageStatus(stage: JsonRecord) {
	return textValue(stage.status, 'pending');
}

export function WorkbenchPage({
	overview,
	selectedProject,
	runtimeProviders,
	mutate,
	onSelectProject,
	onCreateProject,
	onOpenCommandCenter,
	onOpenWorkflows,
	onOpenJobs,
	onOpenWorkspaces,
	onOpenEvidence,
}: WorkbenchPageProps) {
	const { t } = useI18n();
	const [prompt, setPrompt] = useState('');
	const [selectedSessionId, setSelectedSessionId] = useState('');
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');
	const [created, setCreated] = useState<{ sessionId: string; chatId: string; pipelineId: string } | null>(null);
	const projects = useMemo(() => activeProjects(overview.projects), [overview.projects]);
	const project = selectedProject ?? projects[0] ?? null;
	const projectTeams = useMemo(
		() => (project ? overview.teams.filter((team) => team.projectId === project.id) : []),
		[overview.teams, project],
	);
	const teamIds = useMemo(() => new Set(projectTeams.map((team) => team.id)), [projectTeams]);
	const primaryTeam = projectTeams[0] ?? null;
	const projectAgents = useMemo(
		() => overview.agents.filter((agent) => teamIds.has(agent.teamId)),
		[overview.agents, teamIds],
	);
	const projectSessions = useMemo(
		() => sortByUpdatedAt(project ? overview.sessions.filter((session) => session.projectId === project.id) : []),
		[overview.sessions, project],
	);
	const activeSession = selectedSessionId === NEW_SESSION_ID
		? null
		: projectSessions.find((session) => session.id === selectedSessionId) ?? projectSessions[0] ?? null;
	const projectChats = useMemo(
		() => sortByUpdatedAt(project ? overview.chats.filter((chat) => chat.projectId === project.id) : []),
		[overview.chats, project],
	);
	const sessionChats = activeSession
		? projectChats.filter((chat) => chat.sessionId === activeSession.id)
		: projectChats;
	const projectPipelines = useMemo(
		() => sortByUpdatedAt(project ? overview.pipelines.filter((pipeline) => pipeline.projectId === project.id) : []),
		[overview.pipelines, project],
	);
	const sessionPipelines = activeSession
		? projectPipelines.filter((pipeline) => pipeline.sessionId === activeSession.id || !pipeline.sessionId)
		: projectPipelines;
	const projectWorkflows = useMemo(
		() => sortByUpdatedAt(project ? overview.workflows.filter((workflow) => workflow.projectId === project.id) : []),
		[overview.workflows, project],
	);
	const projectWorkspaces = useMemo(
		() => sortByUpdatedAt(project ? overview.runtimeWorkspaces.filter((workspace) => workspace.projectId === project.id) : []),
		[overview.runtimeWorkspaces, project],
	);
	const projectEvidence = useMemo(
		() => sortByUpdatedAt(project ? overview.evidencePackages.filter((evidence) => evidence.projectId === project.id) : []),
		[overview.evidencePackages, project],
	);
	const projectArtifacts = useMemo(
		() => sortByUpdatedAt(project ? overview.artifacts.filter((artifact) => artifact.projectId === project.id) : []),
		[overview.artifacts, project],
	);
	const projectAgentRuns = useMemo(
		() => sortByUpdatedAt(project ? overview.agentRuns.filter((run) => run.projectId === project.id) : []),
		[overview.agentRuns, project],
	);
	const pendingApprovals = project ? overview.actionRequests.filter((item) => item.projectId === project.id && item.status === 'pending').length : 0;
	const executableRuntimes = runtimeProviders?.providers.filter((provider) => provider.executable).length ?? 0;
	const latestWorkflow = projectWorkflows[0] ?? null;
	const latestPipeline = sessionPipelines[0] ?? projectPipelines[0] ?? null;
	const latestEvidence = projectEvidence[0] ?? null;
	const detectedBranch = branchFromOverview(project, overview);
	const branch = detectedBranch === 'not detected' ? t('app.workbench.workspace.branchNotDetected', 'not detected') : detectedBranch;
	const chatDisabled = busy || !project || !prompt.trim();

	useEffect(() => {
		if (!project) {
			setSelectedSessionId('');
			return;
		}
		if (selectedSessionId === NEW_SESSION_ID) return;
		if (selectedSessionId && projectSessions.some((session) => session.id === selectedSessionId)) return;
		setSelectedSessionId(projectSessions[0]?.id ?? '');
	}, [project?.id, projectSessions, selectedSessionId, project]);

	const teamRows = expertBlueprints.map((blueprint) => {
		const matchingProfiles = overview.agentProfiles.filter((profile) =>
			roleMatches(blueprint.role, profile.role, `${profile.id} ${profile.name}`),
		);
		const matchingCatalogAgents = projectAgents.filter((agent) =>
			roleMatches(blueprint.role, agent.role, `${agent.id} ${agent.name}`),
		);
		const profileIds = new Set(matchingProfiles.map((profile) => profile.id));
		const matchingRuns = projectAgentRuns.filter((run) => {
			const metadata = asRecord(run.metadata);
			const input = asRecord(run.input);
			const profileId = textValue(metadata.agentProfileId ?? input.agentProfileId, '');
			const agentId = textValue(metadata.agentId ?? input.agentId, '');
			return profileIds.has(profileId) || roleMatches(blueprint.role, profileId, agentId);
		});
		const latestRun = matchingRuns[0] ?? null;
		const configured = matchingProfiles.length > 0 || matchingCatalogAgents.length > 0 || matchingRuns.length > 0;
		const runtime = matchingProfiles[0]?.runtimeMode ?? matchingCatalogAgents[0]?.providerId ?? t('app.workbench.team.notConnected', 'not connected');
		return {
			...blueprint,
			configured,
			runtime,
			status: latestRun?.status ?? (configured ? 'configured' : t('app.workbench.team.notConnected', 'not connected')),
			activity: latestRun ? `${shortId(latestRun.id)} - ${formatTime(latestRun.updatedAt)}` : '',
		};
	});

	const deliveryRows = deliveryStageBlueprints.map((stage, index) => {
		const pipelineStage = latestPipeline?.stages.map(asRecord).find((item) => {
			const normalized = stageName(item).toLowerCase();
			return normalized.includes(stage.id) || normalized.includes(stage.label.toLowerCase().split(' ')[0]);
		});
		const status = pipelineStage ? stageStatus(pipelineStage) : index === 0 && latestPipeline ? latestPipeline.status : 'pending';
		return { ...stage, status };
	});

	const submitPrompt = async () => {
		if (!project) {
			setError(t('app.workbench.error.noProject', 'Select a workspace folder before starting intake.'));
			return;
		}
		const trimmedPrompt = prompt.trim();
		if (!trimmedPrompt) {
			setError(t('app.workbench.error.noPrompt', 'Write a prompt before starting intake.'));
			return;
		}
		setBusy(true);
		setError('');
		setCreated(null);
		try {
			const result = await mutate(async (token) => {
				let session = activeSession;
				if (!session) {
					const sessionResult: SessionCreateResponse = await createSession(token, {
						projectId: project.id,
						teamId: primaryTeam?.id,
						name: firstLine(trimmedPrompt),
					});
					session = sessionResult.session;
				}
				const chatResult: ChatCreateResponse = await createChat(token, {
					projectId: project.id,
					sessionId: session.id,
					prompt: trimmedPrompt,
					title: firstLine(trimmedPrompt),
				});
				const pipelineResult: PipelineCreateResponse = await createPipeline(token, {
					projectId: project.id,
					sessionId: session.id,
					chatId: chatResult.chat.id,
					title: firstLine(trimmedPrompt),
					stages: [
						{ id: 'intake', status: 'created', owner: 'product_owner', source: 'workbench_chat' },
						{ id: 'planning', status: 'pending', owner: 'technical_lead' },
						{ id: 'architecture', status: 'pending', owner: 'architect_agent' },
						{ id: 'implementation', status: 'pending', owner: 'developer' },
						{ id: 'validation', status: 'pending', owner: 'qa_reviewer' },
						{ id: 'delivery', status: 'pending', owner: 'release_manager' },
					],
				});
				return { session, chat: chatResult.chat, pipeline: pipelineResult.pipeline };
			});
			setCreated({ sessionId: result.session.id, chatId: result.chat.id, pipelineId: result.pipeline.id });
			setSelectedSessionId(result.session.id);
			setPrompt('');
		} catch (submitError) {
			setError(submitError instanceof Error ? submitError.message : t('app.workbench.error.submitFailed', 'Workbench intake failed.'));
		} finally {
			setBusy(false);
		}
	};

	return (
		<>
			<PageHeader
				kicker={t('app.workbench.kicker', 'AI project workbench')}
				title={t('app.workbench.title', 'Workspace Workbench')}
				summary={t('app.workbench.summary', 'Select a folder workspace, open a work session and coordinate the AI team from intake to evidence-backed delivery.')}
			/>
			<div className="workbench-layout workbench-ide-layout">
				<aside className="workbench-session-rail" aria-label={t('app.workbench.sessions.region', 'Workspace sessions')}>
					<Surface title={t('app.workbench.workspace.title', 'Workspace folder')}>
						<div className="workspace-context">
							<div className="field">
								<label htmlFor="workbench-project">{t('app.workbench.workspace.label', 'Workspace folder')}</label>
								<select
									id="workbench-project"
									className="select workspace-select"
									value={project?.id ?? ''}
									disabled={!projects.length}
									onChange={(event) => onSelectProject(event.target.value)}
								>
									{projects.length ? null : <option value="">{t('app.workbench.workspace.none', 'No active workspace')}</option>}
									{projects.map((item) => (
										<option key={item.id} value={item.id}>{item.name}</option>
									))}
								</select>
							</div>
							<div className="workspace-root-card">
								<span>{t('app.workbench.workspace.rootPath', 'Root path')}</span>
								<strong className="mono">{project ? String(project.path ?? project.id) : t('app.workbench.workspace.missingPath', 'not selected')}</strong>
								<div className="workspace-root-meta">
									<Badge tone={project ? toneForStatus(String(project.status ?? 'active')) : 'warn'}>{project ? String(project.status ?? 'active') : t('app.workbench.workspace.noSelection', 'no selection')}</Badge>
									<Badge><GitBranch aria-hidden="true" size={13} /> {branch}</Badge>
								</div>
							</div>
							<button className="button primary" type="button" onClick={onCreateProject}>
								<FolderKanban aria-hidden="true" size={16} />
								{t('app.workbench.workspace.create', 'Import or create workspace')}
							</button>
						</div>
					</Surface>

					<Surface title={t('app.workbench.sessions.title', 'Work sessions')}>
						<div className="session-list" role="list">
							<button
								className="session-item new-session"
								type="button"
								aria-current={selectedSessionId === NEW_SESSION_ID || !activeSession ? 'page' : undefined}
								onClick={() => setSelectedSessionId(NEW_SESSION_ID)}
							>
								<span><Bot aria-hidden="true" size={15} /> {t('app.workbench.sessions.new', 'New work session')}</span>
								<small>{t('app.workbench.sessions.newHint', 'Starts a chat, session and intake pipeline')}</small>
							</button>
							{projectSessions.map((session) => {
								const sessionChatCount = projectChats.filter((chat) => chat.sessionId === session.id).length;
								return (
									<button
										key={session.id}
										className="session-item"
										type="button"
										aria-current={activeSession?.id === session.id ? 'page' : undefined}
										onClick={() => setSelectedSessionId(session.id)}
									>
										<span><History aria-hidden="true" size={15} /> {session.name}</span>
										<small>{sessionChatCount} {t('app.workbench.sessions.chatCount', 'chats')} - {formatTime(session.updatedAt)}</small>
									</button>
								);
							})}
							{projectSessions.length ? null : (
								<EmptyState
									title={t('app.workbench.sessions.emptyTitle', 'No work sessions')}
									body={t('app.workbench.sessions.emptyBody', 'Your first prompt will create a session for this workspace folder.')}
								/>
							)}
						</div>
					</Surface>
				</aside>

				<section className="workbench-primary" aria-label={t('app.workbench.primaryRegion', 'Chat and progress')}>
					<Surface title={activeSession ? activeSession.name : t('app.workbench.chat.title', 'Start from chat')}>
						<div className="workbench-chat">
							<div className="chat-transcript" aria-label={t('app.workbench.chat.history', 'Session chat history')}>
								{sessionChats.length ? (
									sessionChats.slice(0, 8).map((chat) => (
										<article className="chat-bubble" key={chat.id}>
											<div className="chat-bubble-header">
												<Badge tone={toneForStatus(String(chat.status ?? 'active'))}>{String(chat.status ?? 'active')}</Badge>
												<span className="mono">{shortId(chat.id)}</span>
												<span className="muted">{formatTime(chat.createdAt)}</span>
											</div>
											<strong>{chat.title}</strong>
											<p>{chat.prompt}</p>
										</article>
									))
								) : (
									<EmptyState
										title={t('app.workbench.chat.emptyTitle', 'No chat in this session')}
										body={t('app.workbench.chat.emptyBody', 'Use the composer to start coordinated project work for this workspace.')}
									/>
								)}
							</div>
							<div className="chat-composer">
								<label htmlFor="workbench-chat-prompt">{t('app.workbench.chat.promptLabel', 'Task prompt')}</label>
								<textarea
									id="workbench-chat-prompt"
									className="textarea chat-textarea"
									value={prompt}
									rows={5}
									disabled={!project || busy}
									placeholder={t('app.workbench.chat.placeholder', 'Ask the AI team to plan, implement, test or prepare a long-running delivery inside this workspace.')}
									onChange={(event) => setPrompt(event.target.value)}
								/>
								<div className="chat-composer-actions">
									<button className="button primary" type="button" disabled={chatDisabled} onClick={() => void submitPrompt()}>
										<Rocket aria-hidden="true" size={16} />
										{busy ? t('app.workbench.chat.creating', 'Creating work session') : t('app.workbench.chat.submit', 'Start intake')}
									</button>
									<button
										className="button"
										type="button"
										disabled={!project || busy}
										onClick={() => setPrompt(t('app.workbench.chat.quickPrompt', 'Inspect this workspace, identify the real architecture and propose the smallest safe execution plan with QA evidence.'))}
									>
										<Bot aria-hidden="true" size={16} />
										{t('app.workbench.chat.quickAction', 'Scope with team')}
									</button>
								</div>
								{created ? (
									<div className="form-success" role="status">
										<CheckCircle2 aria-hidden="true" size={16} />
										<span>{t('app.workbench.chat.created', 'Chat intake created')}</span>
										<span className="mono">{shortId(created.sessionId)}</span>
										<span>{t('app.workbench.chat.pipelineLinked', 'pipeline')}</span>
										<span className="mono">{shortId(created.pipelineId)}</span>
									</div>
								) : null}
								{error ? <div className="form-error" role="alert">{error}</div> : null}
							</div>
						</div>
					</Surface>

					<div className="workbench-progress-grid">
						<Surface title={t('app.workbench.delivery.title', 'Project delivery flow')}>
							<ol className="delivery-rail" aria-label={t('app.workbench.delivery.region', 'Project delivery flow')}>
								{deliveryRows.map((stage) => (
									<li className="delivery-step" key={stage.id}>
										<Badge tone={toneForStatus(stage.status)}>{stage.status}</Badge>
										<div>
											<strong>{t(stage.labelKey, stage.label)}</strong>
											<span className="muted">{stage.owner}</span>
										</div>
									</li>
								))}
							</ol>
						</Surface>
						<Surface title={t('app.workbench.progress.title', 'Current progress')}>
							<div className="signal-grid workbench-progress-signals">
								<div className="signal-card">
									<span>{t('app.workbench.signals.workflows', 'Recent workflows')}</span>
									<strong>{projectWorkflows.length}</strong>
								</div>
								<div className="signal-card">
									<span>{t('app.workbench.signals.evidence', 'Evidence packages')}</span>
									<strong>{projectEvidence.length}</strong>
								</div>
								<div className="signal-card">
									<span>{t('app.workbench.signals.approvals', 'Pending approvals')}</span>
									<strong>{pendingApprovals}</strong>
								</div>
								<div className="signal-card">
									<span>{t('app.workbench.signals.workspaces', 'Runtime workspaces')}</span>
									<strong>{projectWorkspaces.length}</strong>
								</div>
							</div>
							<div className="progress-current">
								<div><span>{t('app.workbench.progress.workflow', 'Latest workflow')}</span><strong>{latestWorkflow?.title ?? t('app.workbench.progress.none', 'No workflow yet')}</strong></div>
								<div><span>{t('app.workbench.progress.evidence', 'Latest evidence')}</span><strong>{latestEvidence?.qaVerdict ?? t('app.workbench.progress.noEvidence', 'No evidence yet')}</strong></div>
							</div>
						</Surface>
					</div>
				</section>

				<aside className="workbench-side" aria-label={t('app.workbench.sideRegion', 'AI team and deliverables')}>
					<Surface title={t('app.workbench.team.title', 'AI delivery team')}>
						<div className="team-grid">
							{teamRows.map((member) => {
								const Icon = member.icon;
								return (
									<article className="team-card" key={member.role}>
										<div className="team-card-header">
											<span className="team-icon"><Icon aria-hidden="true" size={15} /></span>
											<div>
												<strong>{t(member.labelKey, member.label)}</strong>
												<span>{t(member.laneKey, member.lane)}</span>
											</div>
											<Badge tone={member.configured ? toneForStatus(member.status) : 'warn'}>{member.status}</Badge>
										</div>
										<p>{t(member.responsibilityKey, member.responsibility)}</p>
										<div className="team-card-meta">
											<span className="mono">{member.runtime}</span>
											{member.activity ? <span className="mono">{member.activity}</span> : null}
										</div>
									</article>
								);
							})}
						</div>
					</Surface>

					<Surface title={t('app.workbench.actions.title', 'Execution lanes')}>
						<div className="command-list">
							<button className="command-item" type="button" onClick={onOpenCommandCenter}>
								<span><TerminalSquare aria-hidden="true" size={15} /> {t('app.workbench.actions.command', 'Run governed command')}</span>
								<small>{t('app.workbench.actions.commandHint', 'issue_to_patch, runtime selection and QA gates')}</small>
							</button>
							<button className="command-item" type="button" onClick={onOpenWorkflows}>
								<span><Workflow aria-hidden="true" size={15} /> {t('app.workbench.actions.workflows', 'Open project workflows')}</span>
								<small>{t('app.workbench.actions.workflowsHint', 'Agile flow, steps, handoffs and current state')}</small>
							</button>
							<button className="command-item" type="button" onClick={onOpenJobs}>
								<span><ClipboardCheck aria-hidden="true" size={15} /> {t('app.workbench.actions.jobs', 'Review approvals')}</span>
								<small>{t('app.workbench.actions.jobsHint', 'human gates, leases and job state')}</small>
							</button>
							<button className="command-item" type="button" onClick={onOpenEvidence}>
								<span><FileCheck2 aria-hidden="true" size={15} /> {t('app.workbench.actions.evidence', 'Inspect evidence')}</span>
								<small>{t('app.workbench.actions.evidenceHint', 'diffs, QA records and artifacts')}</small>
							</button>
							<button className="command-item" type="button" onClick={onOpenWorkspaces}>
								<span><GitBranch aria-hidden="true" size={15} /> {t('app.workbench.actions.workspaces', 'Runtime workspaces')}</span>
								<small>{t('app.workbench.actions.workspacesHint', 'isolated task allocations')}</small>
							</button>
						</div>
					</Surface>

					<Surface title={t('app.workbench.deliverables.title', 'Deliverables and evidence')}>
						<DataTable
							rows={projectArtifacts.slice(0, 5)}
							empty={<EmptyState title={t('app.workbench.deliverables.emptyTitle', 'No deliverables yet')} body={t('app.workbench.deliverables.emptyBody', 'Artifacts, QA reports, patches and evidence manifests appear after real execution.')} />}
							columns={[
								{ key: 'kind', label: t('app.workbench.deliverables.columnKind', 'Kind'), render: (row) => <Badge>{row.kind}</Badge> },
								{ key: 'path', label: t('app.workbench.deliverables.columnPath', 'Path'), render: (row) => <span className="mono">{row.path}</span> },
							]}
						/>
					</Surface>

					<Surface title={t('app.workbench.pipelines.title', 'Session pipelines')}>
						<DataTable
							rows={sessionPipelines.slice(0, 5)}
							empty={<EmptyState title={t('app.workbench.pipelines.emptyTitle', 'No intake pipelines')} body={t('app.workbench.pipelines.emptyBody', 'Chat intake creates the first pipeline record for the selected work session.')} />}
							columns={[
								{ key: 'title', label: t('app.workbench.pipelines.columnTitle', 'Pipeline'), render: (row) => String(row.title ?? '') },
								{ key: 'status', label: t('app.workbench.pipelines.columnStatus', 'Status'), render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
							]}
						/>
					</Surface>
				</aside>
			</div>
		</>
	);
}
