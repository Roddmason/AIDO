/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import {
	Bot,
	CheckCircle2,
	Code2,
	FileCheck2,
	FolderKanban,
	GitBranch,
	MessageSquare,
	Rocket,
	SquareTerminal,
	Users,
} from 'lucide-react';
import { useState } from 'react';
import type {
	ChatCreateResponse,
	IssueToPatchResponse,
	PipelineCreateResponse,
	SessionCreateResponse,
} from '../../api/client';
import { createChat, createPipeline, createSession } from '../../api/client';
import type {
	Overview,
	Project,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../../api/types';
import { Badge, Drawer, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { shortId, toneForStatus } from '../../lib/format';
import { LogsPanel } from './panels/LogsPanel';
import { TimelinePanel } from './panels/TimelinePanel';
import { WorkbenchDiffPanel } from './panels/WorkbenchDiffPanel';
import { WorkbenchEvidencePanel } from './panels/WorkbenchEvidencePanel';
import { TaskComposer } from './TaskComposer';
import { formatTime, NEW_SESSION_ID, useWorkbenchData } from './useWorkbenchData';
import { WorkbenchExplorer } from './WorkbenchExplorer';
import { WorkbenchInspector } from './WorkbenchInspector';
import type { WorkbenchTabId } from './WorkbenchTabs';
import { WorkbenchTabs } from './WorkbenchTabs';
import { WorkflowTimeline } from './WorkflowTimeline';

type Mutate = <T>(
	operation: (token: string) => Promise<T>,
	options?: { awaitRefresh?: boolean },
) => Promise<T>;
type TaskMode = 'conversation' | 'governed';

type WorkbenchPageProps = {
	overview: Overview;
	selectedProject: Project | null;
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	mutate: Mutate;
	token: string;
	onSelectProject: (projectId: string) => void;
	onCreateProject: () => void;
	onOpenJobs: () => void;
	onOpenEvidence: () => void;
	onOpenSettings: () => void;
	onOpenRuntimeSetup: () => void;
	onRefresh: () => Promise<unknown> | void;
};

function firstLine(value: string) {
	return value.trim().split(/\r?\n/)[0]?.slice(0, 96) || 'Workbench intake';
}

export function WorkbenchPage({
	overview,
	selectedProject,
	runtimeProviders,
	runtimeProviderConfiguration,
	mutate,
	token,
	onSelectProject,
	onCreateProject,
	onOpenJobs,
	onOpenEvidence,
	onOpenSettings,
	onOpenRuntimeSetup,
	onRefresh,
}: WorkbenchPageProps) {
	const { t } = useI18n();
	const [prompt, setPrompt] = useState('');
	const [selectedSessionId, setSelectedSessionId] = useState('');
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');
	const [created, setCreated] = useState<{
		sessionId: string;
		chatId: string;
		pipelineId: string;
	} | null>(null);
	const [activeTab, setActiveTab] = useState<WorkbenchTabId>('task');
	const [taskMode, setTaskMode] = useState<TaskMode>('conversation');
	const [taskRunResult, setTaskRunResult] = useState<IssueToPatchResponse | null>(null);
	const [isSubmittingTask, setIsSubmittingTask] = useState(false);
	const [selectedRunId, setSelectedRunId] = useState('');
	const [teamOpen, setTeamOpen] = useState(false);

	const {
		projects,
		project,
		primaryTeam,
		projectSessions,
		activeSession,
		projectChats,
		sessionChats,
		sessionPipelines,
		projectWorkflows,
		projectWorkflowRuns,
		projectWorkflowEvents,
		projectEvents,
		projectWorkspaces,
		projectEvidence,
		projectArtifacts,
		projectTestResults,
		pendingApprovals,
		hasExecutableRuntime,
		branch,
		resolvedEvidenceId,
		activeEvidence,
		reviewChangedFiles,
		latestEvidence,
		latestRunStatus,
		blockers,
		teamRows,
		deliveryRows,
		runTimeline,
	} = useWorkbenchData({
		overview,
		selectedProject,
		runtimeProviders,
		selectedSessionId,
		taskRunResult,
		isSubmittingTask,
		selectedRunId,
		onResetSession: setSelectedSessionId,
	});

	const chatDisabled = busy || !project || !prompt.trim();

	const submitPrompt = async () => {
		if (!project) {
			setError(
				t('app.workbench.error.noProject', 'Select a workspace folder before starting intake.'),
			);
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
			setCreated({
				sessionId: result.session.id,
				chatId: result.chat.id,
				pipelineId: result.pipeline.id,
			});
			setSelectedSessionId(result.session.id);
			setPrompt('');
		} catch (submitError) {
			setError(
				submitError instanceof Error
					? submitError.message
					: t('app.workbench.error.submitFailed', 'Workbench intake failed.'),
			);
		} finally {
			setBusy(false);
		}
	};

	const onSelectRun = (runId: string) => {
		setSelectedRunId(runId);
		setActiveTab('timeline');
	};

	const tabs = [
		{ id: 'task' as const, label: t('app.workbench.tab.task', 'Task') },
		{
			id: 'timeline' as const,
			label: t('app.workbench.tab.timeline', 'Timeline'),
			count: projectWorkflowRuns.length,
		},
		{ id: 'diff' as const, label: t('app.workbench.tab.diff', 'Diff') },
		{
			id: 'evidence' as const,
			label: t('app.workbench.tab.evidence', 'Evidence'),
			count: projectEvidence.length,
		},
		{
			id: 'logs' as const,
			label: t('app.workbench.tab.logs', 'Logs'),
			count: projectEvents.length + projectWorkflowEvents.length,
		},
	];

	const header = (
		<PageHeader
			kicker={t('app.workbench.kicker', 'AI project workbench')}
			title={project ? project.name : t('app.workbench.title', 'Workspace Workbench')}
			summary={t(
				'app.workbench.summary',
				'Compose a task, run governed changes and review timeline, diff, evidence and logs without leaving the workspace.',
			)}
		/>
	);

	if (!project) {
		return (
			<>
				{header}
				<div className="workbench-empty">
					<EmptyState
						title={t('app.workbench.empty.title', 'Open a workspace to begin')}
						body={t(
							'app.workbench.empty.body',
							'Select a project folder or import an existing workspace to start coordinated, evidence-backed work.',
						)}
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
			<div className="workbench-layout">
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

				<section
					className="workbench-primary"
					aria-label={t('app.workbench.primaryRegion', 'Task and progress')}
				>
					<div className="surface flat workbench-composer-header">
						<div className="workbench-composer-meta">
							<strong>
								{activeSession
									? activeSession.name
									: t('app.workbench.chat.title', 'New work session')}
							</strong>
							<div className="inline">
								<Badge>
									<GitBranch aria-hidden="true" size={13} /> {branch}
								</Badge>
								<Badge tone={toneForStatus(latestRunStatus)}>{latestRunStatus}</Badge>
							</div>
						</div>
						<button className="button" type="button" onClick={() => setTeamOpen(true)}>
							<Users aria-hidden="true" size={15} /> {t('app.workbench.team.open', 'AI team')}
						</button>
					</div>

					<Surface title={t('app.workbench.runTimeline.title', 'Run timeline')} flat>
						<WorkflowTimeline
							variant="rail"
							stages={runTimeline}
							label={t('app.workbench.runTimeline.title', 'Run timeline')}
						/>
					</Surface>

					<WorkbenchTabs tabs={tabs} activeTab={activeTab} onChangeTab={setActiveTab}>
						{activeTab === 'task' ? (
							<div className="stack">
								{taskRunResult ? (
									<div className="form-success" role="status">
										<CheckCircle2 aria-hidden="true" size={16} />
										<span>
											{t(
												'app.workbench.review.ready',
												'Run complete — review changes without leaving the workbench.',
											)}
										</span>
										<Badge tone={toneForStatus(latestRunStatus)}>{latestRunStatus}</Badge>
										{activeEvidence ? (
											<Badge tone={toneForStatus(String(activeEvidence.qaVerdict))}>
												QA {String(activeEvidence.qaVerdict ?? 'not_started')}
											</Badge>
										) : null}
										{reviewChangedFiles !== null ? (
											<span className="mono">
												{t('app.workbench.review.changedFiles', 'changed files')}{' '}
												{reviewChangedFiles}
											</span>
										) : null}
										<button className="button" type="button" onClick={() => setActiveTab('diff')}>
											<Code2 aria-hidden="true" size={15} />{' '}
											{t('app.workbench.review.diff', 'Review diff')}
										</button>
										<button
											className="button"
											type="button"
											onClick={() => setActiveTab('evidence')}
										>
											<FileCheck2 aria-hidden="true" size={15} />{' '}
											{t('app.workbench.review.evidence', 'Review evidence')}
										</button>
									</div>
								) : null}
								<div
									className="wizard-mode-toggle"
									role="group"
									aria-label={t('app.workbench.task.modeLabel', 'Task intake mode')}
								>
									<button
										className="button"
										type="button"
										aria-pressed={taskMode === 'conversation'}
										onClick={() => setTaskMode('conversation')}
									>
										<MessageSquare aria-hidden="true" size={15} />{' '}
										{t('app.workbench.task.modeConversation', 'Conversation')}
									</button>
									<button
										className="button"
										type="button"
										aria-pressed={taskMode === 'governed'}
										onClick={() => setTaskMode('governed')}
									>
										<SquareTerminal aria-hidden="true" size={15} />{' '}
										{t('app.workbench.task.modeGoverned', 'Governed patch')}
									</button>
								</div>
								{taskMode === 'conversation' ? (
									<div className="workbench-chat">
										<div
											className="chat-transcript"
											aria-label={t('app.workbench.chat.history', 'Session chat history')}
										>
											{sessionChats.length ? (
												sessionChats.slice(0, 8).map((chat) => (
													<article className="chat-bubble" key={chat.id}>
														<div className="chat-bubble-header">
															<Badge tone={toneForStatus(String(chat.status ?? 'active'))}>
																{String(chat.status ?? 'active')}
															</Badge>
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
													body={t(
														'app.workbench.chat.emptyBody',
														'Use the composer to start coordinated project work for this workspace.',
													)}
												/>
											)}
										</div>
										<div className="chat-composer">
											<label htmlFor="workbench-chat-prompt">
												{t('app.workbench.chat.promptLabel', 'Task prompt')}
											</label>
											<textarea
												id="workbench-chat-prompt"
												className="textarea chat-textarea"
												value={prompt}
												rows={5}
												disabled={!project || busy}
												placeholder={t(
													'app.workbench.chat.placeholder',
													'Ask the AI team to plan, implement, test or prepare a long-running delivery inside this workspace.',
												)}
												onChange={(event) => setPrompt(event.target.value)}
											/>
											<div className="chat-composer-actions">
												<button
													className="button primary"
													type="button"
													disabled={chatDisabled}
													onClick={() => void submitPrompt()}
												>
													<Rocket aria-hidden="true" size={16} />
													{busy
														? t('app.workbench.chat.creating', 'Creating work session')
														: t('app.workbench.chat.submit', 'Start intake')}
												</button>
												<button
													className="button"
													type="button"
													disabled={!project || busy}
													onClick={() =>
														setPrompt(
															t(
																'app.workbench.chat.quickPrompt',
																'Inspect this workspace, identify the real architecture and propose the smallest safe execution plan with QA evidence.',
															),
														)
													}
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
											{error ? (
												<div className="form-error" role="alert">
													{error}
												</div>
											) : null}
										</div>
									</div>
								) : (
									<TaskComposer
										project={project}
										runtimeProviders={runtimeProviders}
										mutate={mutate}
										result={taskRunResult}
										busy={isSubmittingTask}
										onResult={setTaskRunResult}
										onBusy={setIsSubmittingTask}
										onConfigureRuntime={onOpenRuntimeSetup}
									/>
								)}
							</div>
						) : null}

						{activeTab === 'timeline' ? (
							<TimelinePanel
								taskRunResult={taskRunResult}
								isSubmittingTask={isSubmittingTask}
								hasExecutableRuntime={hasExecutableRuntime}
								deliveryRows={deliveryRows}
								signals={{
									workflows: projectWorkflows.length,
									evidence: projectEvidence.length,
									approvals: pendingApprovals.length,
									workspaces: projectWorkspaces.length,
								}}
								workflows={projectWorkflows}
								workflowRuns={projectWorkflowRuns}
								workflowEvents={projectWorkflowEvents}
								pipelines={sessionPipelines}
								onOpenArtifact={() => setActiveTab('evidence')}
							/>
						) : null}

						{activeTab === 'diff' ? (
							<WorkbenchDiffPanel
								token={token}
								evidenceId={resolvedEvidenceId}
								artifacts={projectArtifacts}
								evidencePackage={activeEvidence}
							/>
						) : null}

						{activeTab === 'evidence' ? (
							<WorkbenchEvidencePanel
								token={token}
								evidenceId={resolvedEvidenceId}
								evidencePackage={activeEvidence}
								testResults={projectTestResults}
								artifacts={projectArtifacts}
							/>
						) : null}

						{activeTab === 'logs' ? (
							<LogsPanel events={projectEvents} workflowEvents={projectWorkflowEvents} />
						) : null}
					</WorkbenchTabs>
				</section>

				<WorkbenchInspector
					runtimeProviders={runtimeProviders}
					runtimeProviderConfiguration={runtimeProviderConfiguration}
					token={token}
					pendingApprovals={pendingApprovals}
					latestEvidence={latestEvidence}
					testResults={projectTestResults}
					blockers={blockers}
					onOpenJobs={onOpenJobs}
					onOpenEvidence={onOpenEvidence}
					onOpenSettings={onOpenSettings}
					onOpenRuntimeSetup={onOpenRuntimeSetup}
					onRefresh={onRefresh}
				/>
			</div>

			<Drawer
				label={t('app.workbench.team.title', 'AI delivery team')}
				open={teamOpen}
				onClose={() => setTeamOpen(false)}
			>
				<div className="team-grid">
					{teamRows.map((member) => {
						const Icon = member.icon;
						return (
							<article className="team-card" key={member.role}>
								<div className="team-card-header">
									<span className="team-icon">
										<Icon aria-hidden="true" size={15} />
									</span>
									<div>
										<strong>{t(member.labelKey, member.label)}</strong>
										<span>{t(member.laneKey, member.lane)}</span>
									</div>
									<Badge tone={member.configured ? toneForStatus(member.status) : 'warn'}>
										{member.status}
									</Badge>
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
