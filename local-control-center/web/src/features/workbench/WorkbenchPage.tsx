/**
 * Workbench IDE shell: a single composer drives the product loop (conversation → questions →
 * brief → assumptions → decisions → architecture → backlog → iteration → execution → review),
 * laid out with the explorer and inspector. Owns the page-level UI state (prompt, active loop
 * section, selected session/run) and wires intake (session + chat + pipeline) to the
 * API; the derived data comes from useWorkbenchData. Sections wired to live overview data render
 * real content; the discovery/backlog sections render an honest shell until their endpoint exists.
 *
 * When hideExplorer=true (shell mode) the center renders a clean chat-first layout: transcript
 * leads, composer is pinned at the bottom, auxiliary panels are hidden.
 * @author Rodrigo Mason
 */
import {
	CheckCircle2,
	FolderKanban,
	GitBranch,
	Rocket,
	Users,
} from 'lucide-react';
import type { KeyboardEvent } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type {
	ChatCreateResponse,
	PipelineCreateResponse,
	SessionCreateResponse,
} from '../../api/client';
import {
	createChat,
	createPipeline,
	createSession,
	getProjectGitStatus,
	startProductLoop,
} from '../../api/client';
import type {
	Overview,
	Project,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../../api/types';
import { Badge, Drawer, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { Button, TextArea, TextField, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { shortId, toneForStatus } from '../../lib/format';
import { TeamActivityPanel } from '../team-activity/TeamActivityPanel';
import type { ComposerDraft } from './composerDraft';
import { clearComposerDraft, persistComposerDraft, readComposerDraft } from './composerDraft';
import { ProductLoopStepper } from './ProductLoopStepper';
import { LogsPanel } from './panels/LogsPanel';
import { ProductLoopSection } from './panels/ProductLoopSection';
import { TimelinePanel } from './panels/TimelinePanel';
import { WorkbenchDiffPanel } from './panels/WorkbenchDiffPanel';
import { WorkbenchEvidencePanel } from './panels/WorkbenchEvidencePanel';
import { buildProductLoopSections, type ProductLoopSectionId } from './productLoopModel';
import { useProductLoop } from './useProductLoop';
import { formatTime, NEW_SESSION_ID, useWorkbenchData } from './useWorkbenchData';
import { WorkbenchExplorer } from './WorkbenchExplorer';
import { WorkbenchInspector } from './WorkbenchInspector';
import { WorkbenchTabs } from './WorkbenchTabs';
import { WorkflowTimeline } from './WorkflowTimeline';

type Mutate = <T>(
	operation: (token: string) => Promise<T>,
	options?: { awaitRefresh?: boolean },
) => Promise<T>;

/** Chats shown before the "View full chat history" toggle reveals the rest. */
const CHAT_PREVIEW_COUNT = 8;

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
	/** Hide the in-page workspace/session explorer when an outer shell already provides one
	 *  (the thread/loop ShellSidebar). Defaults to false so the standalone Workbench is unchanged. */
	hideExplorer?: boolean;
	/** Optional controlled session selection: when provided, the shell owns which session is active
	 *  (so the ShellSidebar can drive the center). When omitted, the page keeps its own internal state. */
	selectedSessionId?: string;
	onSelectSession?: (sessionId: string) => void;
};

function firstLine(value: string) {
	return value.trim().split(/\r?\n/)[0]?.slice(0, 96) || 'Workbench intake';
}

/** Title auto-derived from the prompt; '' when the prompt is empty so the field stays empty.
 *  The Product Owner intake infers work type from the prompt, so the title never encodes a
 *  user-selected task classification. */
function deriveTitle(prompt: string): string {
	const head = prompt.trim().split(/\r?\n/)[0] ?? '';
	if (!head) return '';
	return head.slice(0, 96);
}

/**
 * Top-level Workbench screen. Renders an empty state until a workspace is selected, then lays out
 * explorer, the single-composer product-loop column and inspector, and drives intake against the
 * live API.
 */
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
	hideExplorer = false,
	selectedSessionId: controlledSessionId,
	onSelectSession,
}: WorkbenchPageProps) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [prompt, setPrompt] = useState('');
	const [title, setTitle] = useState('');
	const [titleEdited, setTitleEdited] = useState(false);
	const [internalSessionId, setInternalSessionId] = useState('');
	const selectedSessionId = controlledSessionId ?? internalSessionId;
	const setSelectedSessionId = useCallback(
		(sessionId: string) => {
			if (onSelectSession) onSelectSession(sessionId);
			else setInternalSessionId(sessionId);
		},
		[onSelectSession],
	);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');
	const [created, setCreated] = useState<{
		sessionId: string;
		chatId: string;
		pipelineId: string;
	} | null>(null);
	const [activeSection, setActiveSection] = useState<ProductLoopSectionId>('conversation');
	const [selectedRunId, setSelectedRunId] = useState('');
	const [teamOpen, setTeamOpen] = useState(false);
	const [showAllChats, setShowAllChats] = useState(false);
	const [loopStarting, setLoopStarting] = useState(false);
	const [detectedGitBranch, setDetectedGitBranch] = useState('');

	const latestDraftRef = useRef<{ projectId: string; draft: ComposerDraft } | null>(null);
	const hydratedProjectIdRef = useRef('');
	const abortRef = useRef<AbortController | null>(null);

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
		branch,
		resolvedEvidenceId,
		activeEvidence,
		latestEvidence,
		latestRunStatus,
		blockers,
		deliveryTimeline,
		runTimeline,
		hasRun,
		hasPipeline,
	} = useWorkbenchData({
		overview,
		selectedProject,
		runtimeProviders,
		selectedSessionId,
		taskRunResult: null,
		isSubmittingTask: false,
		selectedRunId,
		onResetSession: setSelectedSessionId,
	});

	const loop = useProductLoop(project?.id);

	const derivedTitle = deriveTitle(prompt);
	const effectiveTitle = titleEdited ? title : derivedTitle;
	const submitTitle = titleEdited
		? title.trim() || firstLine(prompt)
		: derivedTitle || firstLine(prompt);

	const trimmedPrompt = prompt.trim();
	const composerDisabled = busy || !project || !trimmedPrompt;
	const displayBranch = detectedGitBranch || branch;

	useEffect(() => {
		const projectId = project?.id ?? '';
		if (!projectId) {
			setDetectedGitBranch('');
			return;
		}
		const controller = new AbortController();
		getProjectGitStatus(projectId, controller.signal)
			.then((status) => {
				if (controller.signal.aborted) return;
				setDetectedGitBranch(status.currentBranch || '');
			})
			.catch(() => {
				if (!controller.signal.aborted) setDetectedGitBranch('');
			});
		return () => controller.abort();
	}, [project?.id]);

	// biome-ignore lint/correctness/useExhaustiveDependencies: re-run only when the project id changes.
	useEffect(() => {
		const projectId = project?.id ?? '';
		const draft = projectId ? readComposerDraft(projectId) : null;
		if (draft) {
			setPrompt(draft.prompt);
			setTitle(draft.title);
			setTitleEdited(draft.titleEdited);
		} else {
			setPrompt('');
			setTitle('');
			setTitleEdited(false);
		}
	}, [project?.id]);

	latestDraftRef.current = project
		? { projectId: project.id, draft: { prompt, title, titleEdited } }
		: null;

	useEffect(() => {
		const projectId = project?.id ?? '';
		if (!projectId || hydratedProjectIdRef.current !== projectId) return;
		const handle = window.setTimeout(() => {
			persistComposerDraft(projectId, { prompt, title, titleEdited });
		}, 400);
		return () => window.clearTimeout(handle);
	}, [project?.id, prompt, title, titleEdited]);

	useEffect(() => {
		hydratedProjectIdRef.current = project?.id ?? '';
	}, [project?.id]);

	useEffect(() => {
		return () => {
			const latest = latestDraftRef.current;
			if (latest && hydratedProjectIdRef.current === latest.projectId) {
				persistComposerDraft(latest.projectId, latest.draft);
			}
		};
	}, []);

	const submitConversation = async (activeProject: Project, text: string, signal: AbortSignal) => {
		setBusy(true);
		setError('');
		setCreated(null);
		try {
			const result = await mutate(
				async (token) => {
					let session = activeSession;
					if (!session) {
						const sessionResult: SessionCreateResponse = await createSession(
							token,
							{ projectId: activeProject.id, teamId: primaryTeam?.id, name: submitTitle },
							signal,
						);
						session = sessionResult.session;
					}
					const chatResult: ChatCreateResponse = await createChat(
						token,
						{ projectId: activeProject.id, sessionId: session.id, prompt: text, title: submitTitle },
						signal,
					);
					const pipelineResult: PipelineCreateResponse = await createPipeline(
						token,
						{
							projectId: activeProject.id,
							sessionId: session.id,
							chatId: chatResult.chat.id,
							title: submitTitle,
							productOwnerIntake: true,
							stages: [
								{ id: 'intake', status: 'created', owner: 'product_owner', source: 'workbench_chat' },
								{ id: 'planning', status: 'pending', owner: 'technical_lead' },
								{ id: 'architecture', status: 'pending', owner: 'architect_agent' },
								{ id: 'implementation', status: 'pending', owner: 'developer' },
								{ id: 'validation', status: 'pending', owner: 'qa_reviewer' },
								{ id: 'delivery', status: 'pending', owner: 'release_manager' },
							],
						},
						signal,
					);
					return { session, chat: chatResult.chat, pipeline: pipelineResult.pipeline };
				},
				{ awaitRefresh: false },
			);
			setCreated({
				sessionId: result.session.id,
				chatId: result.chat.id,
				pipelineId: result.pipeline.id,
			});
			setSelectedSessionId(result.session.id);
			setPrompt('');
			setTitle('');
			setTitleEdited(false);
			clearComposerDraft(activeProject.id);
		} catch (submitError) {
			if (signal.aborted) return; // User cancelled — keep the prompt, surface no error.
			setError(
				submitError instanceof Error
					? submitError.message
					: t('app.workbench.error.submitFailed', 'Workbench intake failed.'),
			);
		} finally {
			setBusy(false);
		}
	};

	const submitComposer = () => {
		if (!project) {
			setError(
				t('app.workbench.error.noProject', 'Select a workspace folder before starting intake.'),
			);
			return;
		}
		const text = prompt.trim();
		if (!text) {
			setError(t('app.workbench.error.noPrompt', 'Write a prompt before starting intake.'));
			return;
		}
		const controller = new AbortController();
		abortRef.current = controller;
		void submitConversation(project, text, controller.signal).finally(() => {
			if (abortRef.current === controller) abortRef.current = null;
		});
	};

	const cancelConversation = () => abortRef.current?.abort();

	const startLoop = async () => {
		if (!project || loopStarting) return;
		setLoopStarting(true);
		try {
			await startProductLoop(token, project.id, { title: submitTitle || project.name });
			loop.refresh();
			notify({ title: t('app.workbench.loop.started', 'Product loop started'), tone: 'ok' });
		} catch (startError) {
			notify({
				title: t('app.workbench.loop.startFailed', 'Could not start the product loop'),
				body: startError instanceof Error ? startError.message : undefined,
				tone: 'danger',
			});
		} finally {
			setLoopStarting(false);
		}
	};

	const onSelectRun = (runId: string) => {
		setSelectedRunId(runId);
		setActiveSection('iteration');
	};

	const loopSections = buildProductLoopSections({
		conversation: sessionChats.length,
		iteration: sessionPipelines.length,
		execution: projectWorkflowRuns.length,
		review: projectEvidence.length,
	});
	const loopTabs = loopSections.map((section) => ({
		id: section.id,
		label: t(section.labelKey, section.label),
		count: section.count,
	}));

	const header = (
		<PageHeader
			kicker={t('app.workbench.kicker', 'AI project workbench')}
			title={project ? project.name : t('app.workbench.title', 'Workspace Workbench')}
			summary={t(
				'app.workbench.summary',
				'Compose a task, let the Product Owner intake infer the work, and review timeline, evidence and logs without leaving the workspace.',
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

	const projectArchitectureDecisions = overview.architectureDecisions.filter(
		(decision) => decision.projectId === project.id,
	);

	const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
		if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
			event.preventDefault();
			if (!composerDisabled) submitComposer();
		}
	};

	if (hideExplorer) {
		return (
			<section
				className="shell-chat-layout"
				aria-label={t('app.workbench.primaryRegion', 'Task and progress')}
			>
				{/* --- Transcript: leads, fills available height, scrolls --- */}
				<div
					id="shell-chat-transcript"
					className="shell-chat-transcript"
					aria-label={t('app.workbench.shell.transcriptRegion', 'Session transcript')}
					role="log"
				>
					{sessionChats.length ? (
						(showAllChats ? sessionChats : sessionChats.slice(0, CHAT_PREVIEW_COUNT)).map(
							(chat) => (
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
							),
						)
					) : (
						<div className="shell-chat-transcript-empty">
							<EmptyState
								title={t('app.workbench.shell.emptyTranscriptTitle', 'Start a conversation')}
								body={t(
									'app.workbench.shell.emptyTranscriptBody',
									'Describe what you want to build. AIDO plans, implements and tests it in this workspace.',
								)}
							/>
						</div>
					)}
					{sessionChats.length > CHAT_PREVIEW_COUNT ? (
						<Button
							aria-expanded={showAllChats}
							aria-controls="shell-chat-transcript"
							onClick={() => setShowAllChats((open) => !open)}
						>
							{showAllChats
								? t('app.workbench.chat.showFewer', 'Show fewer')
								: t('app.workbench.chat.viewFull', 'View full chat history')}
						</Button>
					) : null}

				</div>

				{/* --- Pinned bottom composer --- */}
				<div className="shell-chat-composer">
					{error ? (
						<div className="form-error" role="alert">
							{error}
						</div>
					) : null}
					{created ? (
						<div className="form-success" role="status">
							<CheckCircle2 aria-hidden="true" size={16} />
							<span>{t('app.workbench.chat.created', 'Chat intake created')}</span>
							<span className="mono">{shortId(created.sessionId)}</span>
							<span>{t('app.workbench.chat.pipelineLinked', 'pipeline')}</span>
							<span className="mono">{shortId(created.pipelineId)}</span>
						</div>
					) : null}

					<TextArea
						label={t('app.workbench.composer.promptLabel', 'What should AIDO do?')}
						value={prompt}
						rows={3}
						disabled={!project || busy}
						placeholder={t(
							'app.workbench.composer.promptPlaceholder',
							'Describe the task in plain language. The AI team plans, implements and tests it inside this workspace.',
						)}
						onChange={(event) => setPrompt(event.target.value)}
						onKeyDown={onComposerKeyDown}
					/>

					{/* Inline submit row */}
					<div className="shell-chat-options">
						<div className="shell-chat-send">
							{busy ? (
								<Button onClick={cancelConversation}>
									{t('app.workbench.composer.cancel', 'Cancel intake')}
								</Button>
							) : (
								<Button
									variant="primary"
									icon={<Rocket aria-hidden="true" size={16} />}
									disabled={composerDisabled}
									onClick={submitComposer}
								>
									{busy
										? t('app.workbench.chat.creating', 'Creating work session')
										: t('app.workbench.loop.respond', 'Respond')}
								</Button>
							)}
						</div>
					</div>

					{/* Context row: project · branch (read-only) */}
					<div className="shell-chat-context" aria-hidden="true">
						<span>{project.name}</span>
						<span className="shell-chat-context-sep" aria-hidden="true">
							·
						</span>
						<span>
							<GitBranch
								aria-hidden="true"
								size={11}
								style={{ display: 'inline', verticalAlign: 'middle' }}
							/>{' '}
							{displayBranch}
						</span>
					</div>
				</div>
			</section>
		);
	}

	return (
		<>
			{header}
			<div className="workbench-layout">
				<WorkbenchExplorer
					projects={projects}
					project={project}
					branch={displayBranch}
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
									<GitBranch aria-hidden="true" size={13} /> {displayBranch}
								</Badge>
								<Badge tone={toneForStatus(latestRunStatus)}>{latestRunStatus}</Badge>
							</div>
						</div>
						<button className="button" type="button" onClick={() => setTeamOpen(true)}>
							<Users aria-hidden="true" size={15} /> {t('app.workbench.team.open', 'AI team')}
						</button>
					</div>

					{/* Single composer: always visible, drives every loop action from one place. */}
					<Surface flat>
						<div className="stack">
							<TextArea
								label={t('app.workbench.composer.promptLabel', 'What should AIDO do?')}
								value={prompt}
								rows={6}
								disabled={!project || busy}
								placeholder={t(
									'app.workbench.composer.promptPlaceholder',
									'Describe the task in plain language. The AI team plans, implements and tests it inside this workspace.',
								)}
								onChange={(event) => setPrompt(event.target.value)}
							/>
							<TextField
								label={t('app.workbench.composer.titleLabel', 'Title')}
								help={t(
									'app.workbench.composer.titleHelp',
									'Auto-derived from your prompt — edit to override.',
								)}
								value={effectiveTitle}
								disabled={!project || busy}
								onChange={(event) => {
									setTitle(event.target.value);
									setTitleEdited(true);
								}}
							/>
							<div className="inline">
								<Button
									variant="primary"
									icon={<Rocket aria-hidden="true" size={16} />}
									disabled={composerDisabled}
									onClick={submitComposer}
								>
									{busy
										? t('app.workbench.chat.creating', 'Creating work session')
										: t('app.workbench.loop.respond', 'Respond')}
								</Button>
								{busy ? (
									<Button onClick={cancelConversation}>
										{t('app.workbench.composer.cancel', 'Cancel intake')}
									</Button>
								) : null}
							</div>

							{error ? (
								<div className="form-error" role="alert">
									{error}
								</div>
							) : null}
							{created ? (
								<div className="form-success" role="status">
									<CheckCircle2 aria-hidden="true" size={16} />
									<span>{t('app.workbench.chat.created', 'Chat intake created')}</span>
									<span className="mono">{shortId(created.sessionId)}</span>
									<span>{t('app.workbench.chat.pipelineLinked', 'pipeline')}</span>
									<span className="mono">{shortId(created.pipelineId)}</span>
								</div>
							) : null}

						</div>
					</Surface>

					<Surface title={t('app.workbench.runTimeline.title', 'Run timeline')} flat>
						{hasRun ? (
							<WorkflowTimeline
								variant="rail"
								stages={runTimeline}
								label={t('app.workbench.runTimeline.title', 'Run timeline')}
							/>
						) : (
							<EmptyState
								title={t('app.workbench.runTimeline.emptyTitle', 'No run yet')}
								body={t(
									'app.workbench.runTimeline.emptyBody',
									'Submit an intake to track its autonomous run here.',
								)}
							/>
						)}
					</Surface>

					<Surface title={t('app.workbench.loop.stepperTitle', 'Loop status')} flat>
						<ProductLoopStepper
							loop={loop.data?.loops[0] ?? null}
							onStart={startLoop}
							starting={loopStarting}
						/>
					</Surface>

					<WorkbenchTabs tabs={loopTabs} activeTab={activeSection} onChangeTab={setActiveSection}>
						{activeSection === 'conversation' ? (
							<div className="workbench-chat">
								<div
									id="workbench-chat-transcript"
									className="chat-transcript"
									aria-label={t('app.workbench.chat.history', 'Session chat history')}
								>
									{sessionChats.length ? (
										(showAllChats ? sessionChats : sessionChats.slice(0, CHAT_PREVIEW_COUNT)).map(
											(chat) => (
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
											),
										)
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
								{sessionChats.length > CHAT_PREVIEW_COUNT ? (
									<Button
										aria-expanded={showAllChats}
										aria-controls="workbench-chat-transcript"
										onClick={() => setShowAllChats((open) => !open)}
									>
										{showAllChats
											? t('app.workbench.chat.showFewer', 'Show fewer')
											: t('app.workbench.chat.viewFull', 'View full chat history')}
									</Button>
								) : null}
							</div>
						) : null}

						{activeSection === 'questions' ? (
							<ProductLoopSection
								section="questions"
								data={loop.data}
								architectureDecisions={projectArchitectureDecisions}
								loading={loop.loading}
								error={loop.error}
							/>
						) : null}
						{activeSection === 'brief' ? (
							<ProductLoopSection
								section="brief"
								data={loop.data}
								architectureDecisions={projectArchitectureDecisions}
								loading={loop.loading}
								error={loop.error}
							/>
						) : null}
						{activeSection === 'assumptions' ? (
							<ProductLoopSection
								section="assumptions"
								data={loop.data}
								architectureDecisions={projectArchitectureDecisions}
								loading={loop.loading}
								error={loop.error}
							/>
						) : null}
						{activeSection === 'decisions' ? (
							<ProductLoopSection
								section="decisions"
								data={loop.data}
								architectureDecisions={projectArchitectureDecisions}
								loading={loop.loading}
								error={loop.error}
							/>
						) : null}
						{activeSection === 'architecture' ? (
							<ProductLoopSection
								section="architecture"
								data={loop.data}
								architectureDecisions={projectArchitectureDecisions}
								loading={loop.loading}
								error={loop.error}
							/>
						) : null}
						{activeSection === 'backlog' ? (
							<ProductLoopSection
								section="backlog"
								data={loop.data}
								architectureDecisions={projectArchitectureDecisions}
								loading={loop.loading}
								error={loop.error}
							/>
						) : null}

						{activeSection === 'iteration' ? (
							<div className="stack">
								<ProductLoopSection
									section="iterations"
									data={loop.data}
									architectureDecisions={projectArchitectureDecisions}
									loading={loop.loading}
									error={loop.error}
								/>
								<TimelinePanel
									runTimeline={runTimeline}
									hasRun={hasRun}
									deliveryTimeline={deliveryTimeline}
									hasPipeline={hasPipeline}
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
									onOpenArtifact={() => setActiveSection('review')}
								/>
							</div>
						) : null}

						{activeSection === 'execution' ? (
							<div className="stack">
								<WorkbenchDiffPanel
									token={token}
									evidenceId={resolvedEvidenceId}
									artifacts={projectArtifacts}
									evidencePackage={activeEvidence}
								/>
								<LogsPanel events={projectEvents} workflowEvents={projectWorkflowEvents} />
							</div>
						) : null}

						{activeSection === 'review' ? (
							<WorkbenchEvidencePanel
								token={token}
								evidenceId={resolvedEvidenceId}
								evidencePackage={activeEvidence}
								testResults={projectTestResults}
								artifacts={projectArtifacts}
							/>
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
				label={t('app.teamActivity.drawerTitle', 'Team activity')}
				open={teamOpen}
				onClose={() => setTeamOpen(false)}
			>
				<TeamActivityPanel projectId={selectedProject?.id} open={teamOpen} />
			</Drawer>
		</>
	);
}
