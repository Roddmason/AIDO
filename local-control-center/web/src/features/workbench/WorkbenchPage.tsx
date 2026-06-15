/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import {
	Bot,
	CheckCircle2,
	ClipboardCheck,
	Code2,
	FileCheck2,
	FolderKanban,
	GitBranch,
	ListChecks,
	MessageSquare,
	Rocket,
	ShieldCheck,
	SquareTerminal,
	TerminalSquare,
	Users,
	Workflow,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { LucideIcon } from 'lucide-react';

import { createChat, createPipeline, createSession } from '../../api/client';
import type { ChatCreateResponse, IssueToPatchResponse, PipelineCreateResponse, SessionCreateResponse } from '../../api/client';
import type { Overview, Project, RuntimeProviders } from '../../api/types';
import { Badge, Drawer, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { evidenceDiffChangedFiles } from '../../lib/diff';
import { shortId, toneForStatus } from '../../lib/format';
import { TaskComposer } from './TaskComposer';
import { WorkbenchExplorer } from './WorkbenchExplorer';
import { WorkbenchInspector } from './WorkbenchInspector';
import { WorkbenchTabs } from './WorkbenchTabs';
import type { WorkbenchTabId } from './WorkbenchTabs';
import { WorkbenchDiffPanel } from './panels/WorkbenchDiffPanel';
import { WorkbenchEvidencePanel } from './panels/WorkbenchEvidencePanel';
import { LogsPanel } from './panels/LogsPanel';
import { TimelinePanel } from './panels/TimelinePanel';
import { WorkflowTimeline } from './WorkflowTimeline';
import { buildWorkflowTimeline } from './timelineModel';
import {
	activeProjects,
	deriveBlockers,
	objectRecord,
	runtimeIsExecutableIssueRuntime,
	runtimeSupportsIssueToPatch,
	sortByTimeDesc,
} from './workbenchSelectors';

type Mutate = <T>(operation: (token: string) => Promise<T>, options?: { awaitRefresh?: boolean }) => Promise<T>;
type JsonRecord = Record<string, unknown>;
type TaskMode = 'conversation' | 'governed';

type WorkbenchPageProps = {
	overview: Overview;
	selectedProject: Project | null;
	runtimeProviders: RuntimeProviders | null;
	mutate: Mutate;
	token: string;
	onSelectProject: (projectId: string) => void;
	onCreateProject: () => void;
	onOpenJobs: () => void;
	onOpenEvidence: () => void;
	onOpenSettings: () => void;
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
	{ role: 'product_owner', labelKey: 'app.workbench.team.role.jp', label: 'JP / Product Owner', laneKey: 'app.workbench.team.lane.direction', lane: 'Direction', responsibilityKey: 'app.workbench.team.role.jpBody', responsibility: 'Backlog, priority and acceptance criteria for long-running work.', icon: ClipboardCheck },
	{ role: 'technical_lead', labelKey: 'app.workbench.team.role.tl', label: 'Technical Lead', laneKey: 'app.workbench.team.lane.guidance', lane: 'Guidance', responsibilityKey: 'app.workbench.team.role.tlBody', responsibility: 'Technical strategy, handoffs and quality gates across agents.', icon: Workflow },
	{ role: 'architect_agent', labelKey: 'app.workbench.team.role.architect', label: 'Architecture', laneKey: 'app.workbench.team.lane.design', lane: 'Design', responsibilityKey: 'app.workbench.team.role.architectBody', responsibility: 'Architecture decisions, environment assumptions and risk framing.', icon: ListChecks },
	{ role: 'developer', labelKey: 'app.workbench.team.role.developer', label: 'Engineering', laneKey: 'app.workbench.team.lane.build', lane: 'Build', responsibilityKey: 'app.workbench.team.role.developerBody', responsibility: 'Frontend, backend and implementation work inside the selected workspace.', icon: Code2 },
	{ role: 'devops', labelKey: 'app.workbench.team.role.devops', label: 'DevOps', laneKey: 'app.workbench.team.lane.environment', lane: 'Environment', responsibilityKey: 'app.workbench.team.role.devopsBody', responsibility: 'Local runtime, scripts, containers and release readiness.', icon: TerminalSquare },
	{ role: 'qa_reviewer', labelKey: 'app.workbench.team.role.qa', label: 'QA', laneKey: 'app.workbench.team.lane.validation', lane: 'Validation', responsibilityKey: 'app.workbench.team.role.qaBody', responsibility: 'Tests, evidence packages and regression verdicts.', icon: FileCheck2 },
	{ role: 'security_reviewer', labelKey: 'app.workbench.team.role.security', label: 'Security', laneKey: 'app.workbench.team.lane.guardrails', lane: 'Guardrails', responsibilityKey: 'app.workbench.team.role.securityBody', responsibility: 'Policy, permissions, sandbox posture and sensitive-output review.', icon: ShieldCheck },
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

function branchFromOverview(project: Project | null, overview: Overview) {
	if (!project) return 'not detected';
	for (const workspace of sortByTimeDesc(overview.runtimeWorkspaces.filter((workspace) => workspace.projectId === project.id))) {
		const metadata = asRecord(workspace.metadata);
		const branch = metadata.branchName ?? metadata.branch ?? metadata.baseBranch;
		if (branch) return textValue(branch);
	}
	for (const run of sortByTimeDesc(overview.workflowRuns.filter((run) => run.projectId === project.id))) {
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
	if (blueprintRole === 'architect_agent') return normalizedRole.includes('architect') || normalizedName.includes('architect');
	if (blueprintRole === 'developer') return ['developer', 'backend_engineer', 'frontend_engineer', 'implementer'].includes(normalizedRole);
	if (blueprintRole === 'qa_reviewer') return normalizedRole === 'qa' || normalizedRole === 'qa_reviewer';
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
	token,
	onSelectProject,
	onCreateProject,
	onOpenJobs,
	onOpenEvidence,
	onOpenSettings,
}: WorkbenchPageProps) {
	const { t } = useI18n();
	const [prompt, setPrompt] = useState('');
	const [selectedSessionId, setSelectedSessionId] = useState('');
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');
	const [created, setCreated] = useState<{ sessionId: string; chatId: string; pipelineId: string } | null>(null);
	const [activeTab, setActiveTab] = useState<WorkbenchTabId>('task');
	const [taskMode, setTaskMode] = useState<TaskMode>('conversation');
	const [issueResult, setIssueResult] = useState<IssueToPatchResponse | null>(null);
	const [issueBusy, setIssueBusy] = useState(false);
	const [selectedRunId, setSelectedRunId] = useState('');
	const [teamOpen, setTeamOpen] = useState(false);

	const projects = useMemo(() => activeProjects(overview.projects), [overview.projects]);
	const project = selectedProject ?? projects[0] ?? null;

	const projectTeams = useMemo(() => (project ? overview.teams.filter((team) => team.projectId === project.id) : []), [overview.teams, project]);
	const teamIds = useMemo(() => new Set(projectTeams.map((team) => team.id)), [projectTeams]);
	const primaryTeam = projectTeams[0] ?? null;
	const projectAgents = useMemo(() => overview.agents.filter((agent) => teamIds.has(agent.teamId)), [overview.agents, teamIds]);
	const projectSessions = useMemo(() => sortByTimeDesc(project ? overview.sessions.filter((session) => session.projectId === project.id) : []), [overview.sessions, project]);
	const activeSession = selectedSessionId === NEW_SESSION_ID ? null : projectSessions.find((session) => session.id === selectedSessionId) ?? projectSessions[0] ?? null;
	const projectChats = useMemo(() => sortByTimeDesc(project ? overview.chats.filter((chat) => chat.projectId === project.id) : []), [overview.chats, project]);
	const sessionChats = activeSession ? projectChats.filter((chat) => chat.sessionId === activeSession.id) : projectChats;
	const projectPipelines = useMemo(() => sortByTimeDesc(project ? overview.pipelines.filter((pipeline) => pipeline.projectId === project.id) : []), [overview.pipelines, project]);
	const sessionPipelines = activeSession ? projectPipelines.filter((pipeline) => pipeline.sessionId === activeSession.id || !pipeline.sessionId) : projectPipelines;
	const projectWorkflows = useMemo(() => sortByTimeDesc(project ? overview.workflows.filter((workflow) => workflow.projectId === project.id) : []), [overview.workflows, project]);
	const projectWorkflowRuns = useMemo(() => sortByTimeDesc(project ? overview.workflowRuns.filter((run) => run.projectId === project.id) : []), [overview.workflowRuns, project]);
	const projectWorkflowEvents = useMemo(() => sortByTimeDesc(project ? overview.workflowEvents.filter((event) => event.projectId === project.id) : []), [overview.workflowEvents, project]);
	const projectEvents = useMemo(() => sortByTimeDesc(project ? overview.events.filter((event) => event.projectId === project.id) : []), [overview.events, project]);
	const projectWorkspaces = useMemo(() => sortByTimeDesc(project ? overview.runtimeWorkspaces.filter((workspace) => workspace.projectId === project.id) : []), [overview.runtimeWorkspaces, project]);
	const projectEvidence = useMemo(() => sortByTimeDesc(project ? overview.evidencePackages.filter((evidence) => evidence.projectId === project.id) : []), [overview.evidencePackages, project]);
	const projectArtifacts = useMemo(() => sortByTimeDesc(project ? overview.artifacts.filter((artifact) => artifact.projectId === project.id) : []), [overview.artifacts, project]);
	const projectTestResults = useMemo(() => (project ? overview.testResultRecords.filter((result) => result.projectId === project.id) : []), [overview.testResultRecords, project]);
	const projectAgentRuns = useMemo(() => sortByTimeDesc(project ? overview.agentRuns.filter((run) => run.projectId === project.id) : []), [overview.agentRuns, project]);
	const pendingApprovals = useMemo(() => (project ? overview.actionRequests.filter((item) => item.projectId === project.id && item.status === 'pending') : []), [overview.actionRequests, project]);

	const runtimeRows = runtimeProviders?.providers ?? [];
	const executableRuntimes = useMemo(() => runtimeRows.filter(runtimeIsExecutableIssueRuntime), [runtimeRows]);
	const hasExecutableRuntime = executableRuntimes.length > 0;
	const unavailableIssueRuntime = runtimeRows.find((runtime) => runtimeSupportsIssueToPatch(runtime) && runtime.executable !== true);
	const runtimeBlockReason = runtimeProviders
		? unavailableIssueRuntime?.reason ?? t('app.workbench.task.runtimeNoExecutable', 'No executable issue_to_patch/code_edit runtime is configured.')
		: t('app.workbench.task.runtimeDiscovery', 'Runtime provider discovery has not completed.');

	const detectedBranch = branchFromOverview(project, overview);
	const branch = detectedBranch === 'not detected' ? t('app.workbench.workspace.branchNotDetected', 'not detected') : detectedBranch;

	const issueEvidenceId = String(objectRecord(issueResult?.evidencePackage)?.id ?? '');
	const selectedRunEvidence = selectedRunId ? projectEvidence.find((evidence) => evidence.workflowRunId === selectedRunId) : null;
	const resolvedEvidenceId = issueEvidenceId || selectedRunEvidence?.id || projectEvidence[0]?.id || '';
	const activeEvidence = useMemo(() => projectEvidence.find((evidence) => evidence.id === resolvedEvidenceId) ?? null, [projectEvidence, resolvedEvidenceId]);
	const reviewChangedFiles = evidenceDiffChangedFiles(activeEvidence);
	const latestEvidence = projectEvidence[0] ?? null;
	const latestRunStatus = String(issueResult?.status ?? projectWorkflowRuns[0]?.status ?? 'idle');

	const blockers = useMemo(
		() => (project ? deriveBlockers({ projectId: project.id, workflowRuns: overview.workflowRuns, testResults: overview.testResultRecords, risks: overview.riskRegister, hasExecutableRuntime, runtimeBlockReason }) : []),
		[overview.workflowRuns, overview.testResultRecords, overview.riskRegister, project, hasExecutableRuntime, runtimeBlockReason],
	);

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
		const matchingProfiles = overview.agentProfiles.filter((profile) => roleMatches(blueprint.role, profile.role, `${profile.id} ${profile.name}`));
		const matchingCatalogAgents = projectAgents.filter((agent) => roleMatches(blueprint.role, agent.role, `${agent.id} ${agent.name}`));
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

	const latestPipeline = sessionPipelines[0] ?? projectPipelines[0] ?? null;
	const deliveryRows = deliveryStageBlueprints.map((stage, index) => {
		const pipelineStage = latestPipeline?.stages.map(asRecord).find((item) => {
			const normalized = stageName(item).toLowerCase();
			return normalized.includes(stage.id) || normalized.includes(stage.label.toLowerCase().split(' ')[0]);
		});
		const status = pipelineStage ? stageStatus(pipelineStage) : index === 0 && latestPipeline ? latestPipeline.status : 'pending';
		return { id: stage.id, label: t(stage.labelKey, stage.label), owner: stage.owner, status };
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
					const sessionResult: SessionCreateResponse = await createSession(token, { projectId: project.id, teamId: primaryTeam?.id, name: firstLine(trimmedPrompt) });
					session = sessionResult.session;
				}
				const chatResult: ChatCreateResponse = await createChat(token, { projectId: project.id, sessionId: session.id, prompt: trimmedPrompt, title: firstLine(trimmedPrompt) });
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

	const onSelectRun = (runId: string) => {
		setSelectedRunId(runId);
		setActiveTab('timeline');
	};

	const runTimeline = buildWorkflowTimeline(issueResult, issueBusy, hasExecutableRuntime);
	const tabs = [
		{ id: 'task' as const, label: t('app.workbench.tab.task', 'Task') },
		{ id: 'timeline' as const, label: t('app.workbench.tab.timeline', 'Timeline'), count: projectWorkflowRuns.length },
		{ id: 'diff' as const, label: t('app.workbench.tab.diff', 'Diff') },
		{ id: 'evidence' as const, label: t('app.workbench.tab.evidence', 'Evidence'), count: projectEvidence.length },
		{ id: 'logs' as const, label: t('app.workbench.tab.logs', 'Logs'), count: projectEvents.length + projectWorkflowEvents.length },
	];

	const header = (
		<PageHeader
			kicker={t('app.workbench.kicker', 'AI project workbench')}
			title={project ? project.name : t('app.workbench.title', 'Workspace Workbench')}
			summary={t('app.workbench.summary', 'Compose a task, run governed changes and review timeline, diff, evidence and logs without leaving the workspace.')}
		/>
	);

	if (!project) {
		return (
			<>
				{header}
				<div className="workbench-empty">
					<EmptyState
						title={t('app.workbench.empty.title', 'Open a workspace to begin')}
						body={t('app.workbench.empty.body', 'Select a project folder or import an existing workspace to start coordinated, evidence-backed work.')}
					/>
					<button className="button primary" type="button" onClick={onCreateProject}>
						<FolderKanban aria-hidden="true" size={16} />
						{t('app.workbench.empty.cta', 'Open folder')}
					</button>
				</div>
			</>
		);
	}

	return (
		<>
			{header}
			<div className="workbench-layout workbench-ide-layout">
				<WorkbenchExplorer
					projects={projects}
					project={project}
					branch={branch}
					sessions={projectSessions}
					chats={projectChats}
					recentRuns={projectWorkflowRuns}
					workflows={projectWorkflows}
					selectedSessionId={selectedSessionId}
					selectedRunId={selectedRunId}
					newSessionSentinel={NEW_SESSION_ID}
					onSelectProject={onSelectProject}
					onSelectSession={setSelectedSessionId}
					onSelectRun={onSelectRun}
					onCreateProject={onCreateProject}
				/>

				<section className="workbench-primary" aria-label={t('app.workbench.primaryRegion', 'Task and progress')}>
					<div className="surface flat workbench-composer-header">
						<div className="workbench-composer-meta">
							<strong>{activeSession ? activeSession.name : t('app.workbench.chat.title', 'New work session')}</strong>
							<div className="inline">
								<Badge><GitBranch aria-hidden="true" size={13} /> {branch}</Badge>
								<Badge tone={toneForStatus(latestRunStatus)}>{latestRunStatus}</Badge>
							</div>
						</div>
						<button className="button" type="button" onClick={() => setTeamOpen(true)}>
							<Users aria-hidden="true" size={15} /> {t('app.workbench.team.open', 'AI team')}
						</button>
					</div>

					<Surface title={t('app.workbench.runTimeline.title', 'Run timeline')} flat>
						<WorkflowTimeline variant="rail" stages={runTimeline} label={t('app.workbench.runTimeline.title', 'Run timeline')} />
					</Surface>

					<WorkbenchTabs tabs={tabs} activeTab={activeTab} onChangeTab={setActiveTab}>
						{activeTab === 'task' ? (
							<div className="stack">
								{issueResult ? (
									<div className="form-success" role="status">
										<CheckCircle2 aria-hidden="true" size={16} />
										<span>{t('app.workbench.review.ready', 'Run complete — review changes without leaving the workbench.')}</span>
										<Badge tone={toneForStatus(latestRunStatus)}>{latestRunStatus}</Badge>
										{activeEvidence ? <Badge tone={toneForStatus(String(activeEvidence.qaVerdict))}>QA {String(activeEvidence.qaVerdict ?? 'not_started')}</Badge> : null}
										{reviewChangedFiles !== null ? <span className="mono">{t('app.workbench.review.changedFiles', 'changed files')} {reviewChangedFiles}</span> : null}
										<button className="button" type="button" onClick={() => setActiveTab('diff')}>
											<Code2 aria-hidden="true" size={15} /> {t('app.workbench.review.diff', 'Review diff')}
										</button>
										<button className="button" type="button" onClick={() => setActiveTab('evidence')}>
											<FileCheck2 aria-hidden="true" size={15} /> {t('app.workbench.review.evidence', 'Review evidence')}
										</button>
									</div>
								) : null}
								<div className="wizard-mode-toggle" role="group" aria-label={t('app.workbench.task.modeLabel', 'Task intake mode')}>
									<button className="button" type="button" aria-pressed={taskMode === 'conversation'} onClick={() => setTaskMode('conversation')}>
										<MessageSquare aria-hidden="true" size={15} /> {t('app.workbench.task.modeConversation', 'Conversation')}
									</button>
									<button className="button" type="button" aria-pressed={taskMode === 'governed'} onClick={() => setTaskMode('governed')}>
										<SquareTerminal aria-hidden="true" size={15} /> {t('app.workbench.task.modeGoverned', 'Governed patch')}
									</button>
								</div>
								{taskMode === 'conversation' ? (
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
												<EmptyState title={t('app.workbench.chat.emptyTitle', 'No chat in this session')} body={t('app.workbench.chat.emptyBody', 'Use the composer to start coordinated project work for this workspace.')} />
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
								) : (
									<TaskComposer project={project} runtimeProviders={runtimeProviders} mutate={mutate} result={issueResult} busy={issueBusy} onResult={setIssueResult} onBusy={setIssueBusy} />
								)}
							</div>
						) : null}

						{activeTab === 'timeline' ? (
							<TimelinePanel
								issueResult={issueResult}
								issueBusy={issueBusy}
								hasExecutableRuntime={hasExecutableRuntime}
								deliveryRows={deliveryRows}
								signals={{ workflows: projectWorkflows.length, evidence: projectEvidence.length, approvals: pendingApprovals.length, workspaces: projectWorkspaces.length }}
								workflows={projectWorkflows}
								workflowRuns={projectWorkflowRuns}
								workflowEvents={projectWorkflowEvents}
								pipelines={sessionPipelines}
								onOpenArtifact={() => setActiveTab('evidence')}
							/>
						) : null}

						{activeTab === 'diff' ? <WorkbenchDiffPanel token={token} evidenceId={resolvedEvidenceId} artifacts={projectArtifacts} evidencePackage={activeEvidence} /> : null}

						{activeTab === 'evidence' ? <WorkbenchEvidencePanel token={token} evidenceId={resolvedEvidenceId} evidencePackage={activeEvidence} testResults={projectTestResults} artifacts={projectArtifacts} /> : null}

						{activeTab === 'logs' ? <LogsPanel events={projectEvents} workflowEvents={projectWorkflowEvents} /> : null}
					</WorkbenchTabs>
				</section>

				<WorkbenchInspector
					runtimeProviders={runtimeProviders}
					pendingApprovals={pendingApprovals}
					latestEvidence={latestEvidence}
					testResults={projectTestResults}
					blockers={blockers}
					onOpenJobs={onOpenJobs}
					onOpenEvidence={onOpenEvidence}
					onOpenSettings={onOpenSettings}
				/>
			</div>

			<Drawer label={t('app.workbench.team.title', 'AI delivery team')} open={teamOpen} onClose={() => setTeamOpen(false)}>
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
			</Drawer>
		</>
	);
}
