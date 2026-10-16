/**
 * Center surface of the threads shell: a real conversation backed by `project_threads`.
 *
 * The live thread is split into two regions so the user can tell the conversation from the work:
 * the transcript pane (user / AIDO Lead messages, pending decisions, artifacts such as research
 * reports) with the Codex-style composer docked under it, and the execution panel
 * ({@link ThreadExecutionPanel}) with the worker state, the pipeline steps, actionable blockers and
 * the humanized event console. Execution-side messages (agent summaries, system events, errors)
 * render in the panel, not the transcript. The composer is one rounded box: the prompt on top, then
 * the controls row (action-approval level + send) and the workspace context row (project, local
 * runtime, runtime readiness chip, git branch/scan). When the "New thread" sentinel is active the same box creates a real
 * thread and sends its first message; the title is derived automatically from the first line. Real
 * data only — settings, git and threads all come from their APIs.
 * Once the thread reaches execution with a planned backlog the live body switches to the development
 * layout (`data-mode="board"`): the story board ({@link ThreadBoard}) takes the main area, the
 * execution panel becomes a strip above it and the transcript + composer move to a side column; a
 * Chat | Board toggle overrides the automatic choice per thread. The choice and a folded chat column
 * are remembered per thread ({@link readThreadLayout}); while the chat is folded its dock button
 * counts the transcript messages that arrived unseen. A board card opens {@link ThreadStoryDrawer}.
 * @author Rodrigo Mason
 */
import {
	AlertTriangle,
	Ban,
	BookOpen,
	CalendarClock,
	ChevronsLeft,
	ChevronsRight,
	Hash,
	History,
	Laptop,
	MessageSquare,
	Send,
	ShieldCheck,
	StickyNote,
	Target,
} from 'lucide-react';
import { AnimatePresence, m } from 'motion/react';
import {
	type FormEvent,
	type KeyboardEvent,
	type ReactNode,
	useCallback,
	useEffect,
	useMemo,
	useRef,
	useState,
} from 'react';
import {
	applyProductLoopFeedback,
	cancelThreadExecution,
	createThread,
	findSimilarThreads,
	getThread,
	getWorkerStatus,
	markSimilarThread,
	postThreadMessage,
	postThreadNote,
	runWorkerOnce,
	updateThreadRunConfiguration,
	type WorkerStatusResponse,
} from '../../api/client';
import type {
	Overview,
	Project,
	RuntimeProviders,
	ThreadAgentEvent,
	ThreadArtifact,
	ThreadDetail,
	ThreadEta,
	ThreadMessage,
	ThreadSimilarityCandidate,
} from '../../api/types';
import type { Mutate } from '../../app/routes';
import {
	Button,
	Dialog,
	EmptyState,
	SegmentedControl,
	Skeleton,
	StatusChip,
	StatusDot,
	TextArea,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { threadStatusTone } from '../../lib/format';
import {
	cardTransition,
	EASE_OUT,
	listStagger,
	panelTransition,
	threadIntakeExit,
	threadLiveEnter,
} from '../../motion/variants';
import { RuntimeTeamChip } from '../runtime-team/RuntimeTeamChip';
import {
	type RuntimeTeamSelection,
	readRuntimeTeam,
	runtimeTeamRequest,
} from '../runtime-team/runtimeTeamModel';
import { useSettings } from '../settings/useSettings';
import { NEW_SESSION_ID } from '../workbench/useWorkbenchData';
import { GitBranchBar } from './GitBranchBar';
import { ThreadBoard } from './ThreadBoard';
import type { DecisionAnswer } from './ThreadDecisionAnswers';
import { ThreadExecutionPanel } from './ThreadExecutionPanel';
import { ThreadStoryDrawer } from './ThreadStoryDrawer';
import { type BoardMode, latestBoardRefreshSequence, storyActivity } from './threadBoardModel';
import {
	readThreadLayout,
	type ThreadLayoutPreference,
	writeThreadLayout,
} from './threadLayoutPreference';
import { ownerIdForProject } from './threadOwner';
import {
	arrayValue,
	isRecord,
	MESSAGE_META,
	safeRecord,
	TRANSCRIPT_KINDS,
	textValue,
} from './threadPresentation';
import { useShellInspector } from './useShellInspector';
import { useThreadBoard } from './useThreadBoard';
import { useThreadConversation } from './useThreadConversation';
import { useThreadEventStream } from './useThreadEventStream';
import { useThreadRemediations } from './useThreadRemediations';
import { useThreadStage } from './useThreadStage';

type ThreadConversationProps = {
	overview: Overview;
	selectedProject: Project | null;
	selectedThreadId: string;
	mutate: Mutate;
	token: string;
	runtimeProviders: RuntimeProviders | null;
	onSelectThread: (threadId: string) => void;
	onCreateProject: () => void;
	onOpenRuntimeSetup: () => void;
	onOpenApprovals: () => void;
	/** Opens a Settings section — the recovery path for blocker remediation actions. */
	onOpenSettings: (section?: string) => void;
};

type ResearchSourcePayload = {
	id?: string;
	artifactId?: string;
	url?: string;
	publisher?: string;
	trustLevel?: string;
	fetchedAt?: string;
	hash?: string;
};
type ResearchRecommendationPayload = {
	title?: string;
	decision?: string;
	sourceCitations?: ResearchSourcePayload[];
};
type ResearchRemediationPayload = {
	action?: string;
	summary?: string;
};
type ResearchCardPayload = {
	status?: string;
	reason?: string;
	recommendation?: ResearchRecommendationPayload;
	remediation?: ResearchRemediationPayload;
	sources?: ResearchSourcePayload[];
	discrepancies?: Array<Record<string, unknown>>;
};

type ConversationPhase = 'no-project' | 'new' | 'loading' | 'error' | 'live';

/** Derives the AnimatePresence phase id from the same conditions the render branches check below. */
function deriveConversationPhase(
	selectedProject: Project | null,
	isNew: boolean,
	loading: boolean,
	error: boolean,
	detail: ThreadDetail | null,
): ConversationPhase {
	if (!selectedProject) return 'no-project';
	if (isNew) return 'new';
	if (loading && !detail) return 'loading';
	if (error || !detail) return 'error';
	return 'live';
}

/** Picks the 3D depth-dissolve variant only for the intake exit and the live entrance that follows
 *  it; every other phase change (loading, error, switching between already-created threads) gets
 *  the existing plain `panelTransition` fade. */
function variantsForPhase(phase: ConversationPhase, handoffFromIntake: boolean) {
	if (phase === 'new') return threadIntakeExit;
	if (phase === 'live' && handoffFromIntake) return threadLiveEnter;
	return panelTransition;
}

/** The threads shell center: create form or live conversation, depending on the selection. */
export function ThreadConversation({
	overview,
	selectedProject,
	selectedThreadId,
	mutate,
	token,
	runtimeProviders,
	onSelectThread,
	onCreateProject,
	onOpenRuntimeSetup,
	onOpenApprovals,
	onOpenSettings,
}: ThreadConversationProps) {
	const { t } = useI18n();
	const isNew = !selectedThreadId || selectedThreadId === NEW_SESSION_ID;
	const activeThreadId = isNew ? null : selectedThreadId;
	const { detail, loading, error, busy, reload, send, resolveDecisions } = useThreadConversation(
		activeThreadId,
		mutate,
	);
	const [streamRefreshKey, setStreamRefreshKey] = useState(0);
	const [workerStatus, setWorkerStatus] = useState<WorkerStatusResponse | null>(null);
	const [workerBusy, setWorkerBusy] = useState(false);
	const eventStream = useThreadEventStream(activeThreadId, streamRefreshKey);
	const latestEventSequence = eventStream.events.at(-1)?.sequence ?? 0;
	const latestWorkerFailureSequence =
		eventStream.events.findLast((event) => event.type === 'worker_failed')?.sequence ?? 0;
	const remediations = useThreadRemediations(activeThreadId, mutate, latestEventSequence);
	const stageEvents = useMemo(
		() => mergeConsoleEvents(detail?.events ?? [], eventStream.events),
		[detail?.events, eventStream.events],
	);
	const stage = useThreadStage(
		stageEvents,
		eventStream.threadStatus ?? detail?.thread.status ?? '',
	);
	const board = useThreadBoard(activeThreadId, latestBoardRefreshSequence(stageEvents));
	const [layoutPrefs, setLayoutPrefs] = useState<Record<string, ThreadLayoutPreference>>({});
	const layoutPref = useMemo<ThreadLayoutPreference>(
		() => (activeThreadId ? (layoutPrefs[activeThreadId] ?? readThreadLayout(activeThreadId)) : {}),
		[activeThreadId, layoutPrefs],
	);
	const manualBoardMode = layoutPref.mode;
	const boardAvailable = (board.data?.progress.total ?? 0) > 0;
	const boardMode: BoardMode = boardAvailable
		? (manualBoardMode ?? (stage.inDevelopment ? 'board' : 'chat'))
		: 'chat';
	const updateLayout = useCallback(
		(patch: ThreadLayoutPreference) => {
			if (!activeThreadId) return;
			writeThreadLayout(activeThreadId, patch);
			setLayoutPrefs((current) => ({
				...current,
				[activeThreadId]: {
					...(current[activeThreadId] ?? readThreadLayout(activeThreadId)),
					...patch,
				},
			}));
		},
		[activeThreadId],
	);
	const selectBoardMode = useCallback((mode: BoardMode) => updateLayout({ mode }), [updateLayout]);
	const chatCollapsed = boardMode === 'board' && Boolean(layoutPref.chatCollapsed);
	const toggleChatCollapsed = useCallback(
		() => updateLayout({ chatCollapsed: !chatCollapsed }),
		[updateLayout, chatCollapsed],
	);
	const transcriptCount = useMemo(
		() =>
			detail && detail.thread.id === activeThreadId
				? detail.messages.filter((message) => TRANSCRIPT_KINDS.has(message.kind)).length
				: null,
		[detail, activeThreadId],
	);
	const [seenCounts, setSeenCounts] = useState<Record<string, number>>({});
	useEffect(() => {
		if (!activeThreadId || transcriptCount === null) return;
		setSeenCounts((current) => {
			if (current[activeThreadId] !== undefined && chatCollapsed) return current;
			if (current[activeThreadId] === transcriptCount) return current;
			return { ...current, [activeThreadId]: transcriptCount };
		});
	}, [activeThreadId, transcriptCount, chatCollapsed]);
	const unreadCount =
		chatCollapsed && activeThreadId && transcriptCount !== null
			? Math.max(0, transcriptCount - (seenCounts[activeThreadId] ?? transcriptCount))
			: 0;
	const [openStory, setOpenStory] = useState<{ threadId: string; storyId: string } | null>(null);
	const openStoryCard =
		openStory && openStory.threadId === activeThreadId
			? (board.data?.columns
					.flatMap((column) => column.cards)
					.find((card) => card.storyId === openStory.storyId) ?? null)
			: null;
	const openStoryActivity = useMemo(
		() => (openStory ? storyActivity(stageEvents, openStory.storyId) : []),
		[openStory, stageEvents],
	);
	const openStoryDrawer = useCallback(
		(storyId: string) => {
			if (activeThreadId) setOpenStory({ threadId: activeThreadId, storyId });
		},
		[activeThreadId],
	);
	const shellInspector = useShellInspector();
	const inspectorFoldedForRef = useRef<string | null>(null);
	useEffect(() => {
		if (!activeThreadId || boardMode !== 'board' || manualBoardMode) return;
		if (inspectorFoldedForRef.current === activeThreadId) return;
		inspectorFoldedForRef.current = activeThreadId;
		shellInspector?.collapseInspector();
	}, [activeThreadId, boardMode, manualBoardMode, shellInspector]);
	const composerDockRef = useRef<HTMLDivElement | null>(null);

	const refreshOverview = () => {
		void mutate(async () => undefined, { awaitRefresh: false });
	};

	// biome-ignore lint/correctness/useExhaustiveDependencies: sequence/running changes can require reload even when the thread status is unchanged.
	useEffect(() => {
		if (!activeThreadId || eventStream.threadStatus === null) return;
		reload();
	}, [activeThreadId, latestEventSequence, eventStream.threadStatus, eventStream.running, reload]);

	const boardLoopId = board.data?.loopId ?? null;
	const selectedProjectId = selectedProject?.id ?? null;
	const requestStoryChanges = useCallback(
		async (taskId: string, feedback: string) => {
			if (!selectedProjectId || !boardLoopId) throw new Error('No product loop to review.');
			await mutate(
				(writeToken) =>
					applyProductLoopFeedback(writeToken, selectedProjectId, boardLoopId, {
						action: 'request_changes',
						feedback,
						targetType: 'task',
						targetId: taskId,
					}),
				{ awaitRefresh: false },
			);
			reload();
			setStreamRefreshKey((value) => value + 1);
		},
		[selectedProjectId, boardLoopId, mutate, reload],
	);

	const sendMessage = useCallback(
		async (content: string) => {
			await send(content);
			setStreamRefreshKey((value) => value + 1);
		},
		[send],
	);

	const saveRuntimeTeam = useCallback(
		async (selection: RuntimeTeamSelection | null) => {
			if (!activeThreadId) return;
			await updateThreadRunConfiguration(token, activeThreadId, runtimeTeamRequest(selection));
			reload();
		},
		[activeThreadId, token, reload],
	);

	const loadWorkerStatus = useCallback((signal?: AbortSignal) => {
		getWorkerStatus(signal)
			.then((status) => {
				if (!signal?.aborted) setWorkerStatus(status);
			})
			.catch(() => {
				if (!signal?.aborted) setWorkerStatus(null);
			});
	}, []);

	// biome-ignore lint/correctness/useExhaustiveDependencies: thread/status/failure changes are explicit worker-status refresh signals.
	useEffect(() => {
		const controller = new AbortController();
		loadWorkerStatus(controller.signal);
		return () => controller.abort();
	}, [loadWorkerStatus, activeThreadId, eventStream.threadStatus, latestWorkerFailureSequence]);

	const runQueuedJobsNow = useCallback(async () => {
		setWorkerBusy(true);
		try {
			await mutate((writeToken) => runWorkerOnce(writeToken), { awaitRefresh: false });
			loadWorkerStatus();
			reload();
			setStreamRefreshKey((value) => value + 1);
		} finally {
			setWorkerBusy(false);
		}
	}, [loadWorkerStatus, mutate, reload]);

	const submitDecisionAnswers = useCallback(
		async (answers: DecisionAnswer[]) => {
			await resolveDecisions(answers);
			setStreamRefreshKey((value) => value + 1);
		},
		[resolveDecisions],
	);

	const phase = deriveConversationPhase(selectedProject, isNew, loading, error, detail);

	const awaitingHandoffRef = useRef(false);
	if (phase === 'new') {
		awaitingHandoffRef.current = true;
	}
	const handoffFromIntake = useMemo(() => phase === 'live' && awaitingHandoffRef.current, [phase]);
	useEffect(() => {
		if (phase === 'live' || phase === 'no-project') {
			awaitingHandoffRef.current = false;
		}
	}, [phase]);

	const [cancelBusy, setCancelBusy] = useState(false);
	const [objectiveDraft, setObjectiveDraft] = useState<string | null>(null);
	const [objectiveBusy, setObjectiveBusy] = useState<NewObjectiveMode | null>(null);
	const [objectiveFailed, setObjectiveFailed] = useState(false);
	const [composerResetKey, setComposerResetKey] = useState(0);
	const pendingNewThreadRef = useRef<string | null>(null);

	const addNote = useCallback(
		async (noteContent: string) => {
			if (!activeThreadId) throw new Error('No active thread to attach the note to.');
			await mutate(
				(writeToken) => postThreadNote(writeToken, activeThreadId, { content: noteContent }),
				{ awaitRefresh: false },
			);
			reload();
			setStreamRefreshKey((value) => value + 1);
		},
		[activeThreadId, mutate, reload],
	);

	const cancelExecution = useCallback(async () => {
		if (!activeThreadId) return;
		setCancelBusy(true);
		try {
			await mutate((writeToken) => cancelThreadExecution(writeToken, activeThreadId), {
				awaitRefresh: false,
			});
			reload();
			loadWorkerStatus();
			setStreamRefreshKey((value) => value + 1);
		} finally {
			setCancelBusy(false);
		}
	}, [activeThreadId, mutate, reload, loadWorkerStatus]);

	const openNewObjective = useCallback((objective: string) => {
		setObjectiveFailed(false);
		setObjectiveDraft(objective);
	}, []);

	const closeNewObjective = useCallback(() => {
		if (objectiveBusy) return;
		pendingNewThreadRef.current = null;
		setObjectiveDraft(null);
	}, [objectiveBusy]);

	const resolveNewObjective = useCallback(
		async (mode: NewObjectiveMode) => {
			if (objectiveDraft === null || !selectedProject) return;
			const objective = objectiveDraft;
			setObjectiveFailed(false);
			setObjectiveBusy(mode);
			try {
				if (mode === 'add_to_loop') {
					await addNote(objective);
					setComposerResetKey((value) => value + 1);
				} else if (mode === 'new_thread') {
					let createdId = pendingNewThreadRef.current;
					if (!createdId) {
						const firstLine = objective.split(/\r?\n/)[0]?.trim() ?? '';
						const title = (firstLine || objective).slice(0, 80);
						const created = await mutate(
							(writeToken) =>
								createThread(writeToken, {
									projectId: selectedProject.id,
									ownerType: 'workspace',
									ownerId: ownerIdForProject(overview, selectedProject),
									title,
								}),
							{ awaitRefresh: false },
						);
						createdId = created.thread.id;
						pendingNewThreadRef.current = createdId;
					}
					await mutate(
						(writeToken) => postThreadMessage(writeToken, createdId, { content: objective }),
						{ awaitRefresh: false },
					);
					pendingNewThreadRef.current = null;
					onSelectThread(createdId);
				} else if (activeThreadId) {
					await mutate(
						(writeToken) =>
							cancelThreadExecution(writeToken, activeThreadId, {
								reason: 'Replaced by a new objective.',
							}),
						{ awaitRefresh: false },
					);
					await mutate(
						(writeToken) => postThreadMessage(writeToken, activeThreadId, { content: objective }),
						{ awaitRefresh: false },
					);
					setComposerResetKey((value) => value + 1);
					reload();
					setStreamRefreshKey((value) => value + 1);
				}
				setObjectiveDraft(null);
			} catch {
				setObjectiveFailed(true);
			} finally {
				setObjectiveBusy(null);
			}
		},
		[
			objectiveDraft,
			selectedProject,
			addNote,
			mutate,
			overview,
			onSelectThread,
			activeThreadId,
			reload,
		],
	);

	let content: ReactNode;

	if (!selectedProject) {
		content = (
			<div className="shell-chat-layout">
				<div className="shell-chat-transcript-empty">
					<EmptyState
						title={t('app.threads.emptyTitle', 'No thread selected')}
						body={t('app.threads.emptyBody', 'Pick a thread on the left or start a new one.')}
						action={
							<Button variant="primary" onClick={onCreateProject}>
								{t('app.shell.threads.openFolder', 'Open folder')}
							</Button>
						}
					/>
				</div>
			</div>
		);
	} else if (isNew) {
		content = (
			<NewThreadComposer
				overview={overview}
				project={selectedProject}
				token={token}
				runtimeProviders={runtimeProviders}
				onGitRefresh={refreshOverview}
				onOpenRuntimeSetup={onOpenRuntimeSetup}
				onCreated={onSelectThread}
			/>
		);
	} else if (loading && !detail) {
		content = (
			<div className="shell-chat-layout">
				<div className="shell-chat-transcript">
					<Skeleton className="thread-skeleton-row" />
					<Skeleton className="thread-skeleton-row" />
					<Skeleton className="thread-skeleton-row" />
				</div>
			</div>
		);
	} else if (error || !detail) {
		content = (
			<div className="shell-chat-layout">
				<div className="shell-chat-transcript-empty">
					<EmptyState
						title={t('app.threads.loadErrorTitle', 'Could not load this thread')}
						body=""
						action={
							<Button variant="secondary" onClick={reload}>
								{t('app.threads.retry', 'Retry')}
							</Button>
						}
					/>
				</div>
			</div>
		);
	} else {
		const pendingDecisions = detail.decisions.filter((decision) => decision.status === 'pending');
		const researchArtifacts = detail.artifacts.filter(
			(artifact) => artifact.kind === 'research_report',
		);
		const transcriptMessages = detail.messages.filter((message) =>
			TRANSCRIPT_KINDS.has(message.kind),
		);
		const executionMessages = detail.messages.filter(
			(message) => !TRANSCRIPT_KINDS.has(message.kind),
		);
		const consoleEvents = stageEvents;
		const threadStatus = eventStream.threadStatus ?? detail.thread.status;
		const threadEta = eventStream.eta ?? detail.eta ?? null;
		const latestQueueSequence =
			consoleEvents.findLast(
				(event) =>
					event.type === 'run_queued' ||
					((event.type === 'research_adopted' || event.type === 'runtime_risk_approved') &&
						safeRecord(event.payload).status === 'queued'),
			)?.sequence ?? 0;
		const waitingForWorker =
			threadStatus === 'queued' &&
			!consoleEvents.some(
				(event) => event.type === 'worker_claimed' && event.sequence > latestQueueSequence,
			);
		const isExecuting = threadStatus === 'queued' || threadStatus === 'running';

		content = (
			<div className="shell-chat-layout thread-live-layout">
				<header className="thread-conversation-head">
					<div className="thread-conversation-title">
						<MessageSquare aria-hidden="true" size={16} />
						<h2>{detail.thread.title}</h2>
					</div>
					<div className="thread-conversation-actions">
						{boardAvailable ? (
							<SegmentedControl<BoardMode>
								className="thread-mode-toggle"
								label={t('app.threads.board.modeLabel', 'Thread view')}
								value={boardMode}
								onChange={selectBoardMode}
								options={[
									{ value: 'chat', label: t('app.threads.board.modeChat', 'Chat') },
									{ value: 'board', label: t('app.threads.board.modeBoard', 'Board') },
								]}
							/>
						) : null}
						{threadEta ? <ThreadEtaIndicator eta={threadEta} /> : null}
						<StatusChip tone={threadStatusTone(threadStatus)}>
							{threadStatus.replace(/_/g, ' ')}
						</StatusChip>
					</div>
				</header>

				<div
					className="thread-live-body"
					data-mode={boardMode}
					data-chat={chatCollapsed ? 'collapsed' : undefined}
				>
					<div
						className="thread-transcript-pane"
						data-collapsed={chatCollapsed ? 'true' : undefined}
					>
						{boardMode === 'board' ? (
							<button
								type="button"
								className="thread-chat-dock-toggle"
								aria-expanded={!chatCollapsed}
								aria-controls="thread-live-chat"
								onClick={toggleChatCollapsed}
							>
								<MessageSquare aria-hidden="true" size={15} />
								<span className="thread-chat-dock-label">
									{chatCollapsed
										? t('app.threads.board.showChat', 'Show chat')
										: t('app.threads.board.hideChat', 'Hide chat')}
								</span>
								{unreadCount > 0 ? (
									<span className="thread-chat-unread tnum">
										{unreadCount}
										<span className="sr-only">{t('app.threads.board.unread', 'new messages')}</span>
									</span>
								) : null}
								{chatCollapsed ? (
									<ChevronsLeft aria-hidden="true" size={15} />
								) : (
									<ChevronsRight aria-hidden="true" size={15} />
								)}
							</button>
						) : null}
						<section
							id="thread-live-chat"
							className="thread-live-scroll"
							aria-label={t('app.threads.chatRegion', 'Thread chat')}
						>
							<div
								className="thread-chat-transcript"
								role="log"
								aria-live="polite"
								aria-relevant="additions"
							>
								{transcriptMessages.length === 0 ? (
									<p className="thread-chat-empty">
										{t('app.threads.transcriptEmpty', 'No messages yet. Send the first one below.')}
									</p>
								) : (
									transcriptMessages.map((message) => (
										<ThreadMessageRow key={message.id} message={message} />
									))
								)}
							</div>

							{researchArtifacts.map((artifact) => (
								<ThreadResearchCard
									key={artifact.id}
									artifact={artifact}
									onOpenResearch={() => onOpenSettings('research')}
								/>
							))}
						</section>

						<div className="thread-composer-dock" ref={composerDockRef}>
							<ThreadComposerBox
								key={`composer-${composerResetKey}`}
								project={selectedProject}
								token={token}
								runtimeProviders={runtimeProviders}
								onGitRefresh={refreshOverview}
								onOpenRuntimeSetup={onOpenRuntimeSetup}
								value=""
								busy={busy}
								rows={3}
								submitLabel={t('app.threads.send', 'Send')}
								submitBusyLabel={t('app.threads.sending', 'Sending…')}
								onSendMessage={sendMessage}
								executing={isExecuting}
								cancelBusy={cancelBusy}
								onAddNote={addNote}
								onCancelExecution={cancelExecution}
								onNewObjective={openNewObjective}
								teamControl={
									<RuntimeTeamChip
										projectId={selectedProject.id}
										token={token}
										selection={readRuntimeTeam(detail.thread.metadata)}
										disabled={isExecuting}
										onChange={saveRuntimeTeam}
									/>
								}
							/>
						</div>
					</div>

					<ThreadExecutionPanel
						threadStatus={threadStatus}
						events={consoleEvents}
						messages={executionMessages}
						pendingDecisions={pendingDecisions}
						onSubmitDecisions={submitDecisionAnswers}
						workerStatus={workerStatus}
						workerBusy={workerBusy}
						syncing={eventStream.loading}
						streamError={eventStream.error}
						waitingForWorker={waitingForWorker}
						handoffFromIntake={handoffFromIntake}
						remediations={remediations}
						onRunQueuedNow={runQueuedJobsNow}
						onOpenApprovals={onOpenApprovals}
						onOpenSettings={onOpenSettings}
						presentation={boardMode === 'board' ? 'strip' : 'column'}
					/>
					{boardMode === 'board' && board.data ? (
						<ThreadBoard board={board.data} error={board.error} onOpenStory={openStoryDrawer} />
					) : null}
				</div>

				<ThreadStoryDrawer
					card={openStoryCard}
					loopState={board.data?.loopState ?? null}
					activity={openStoryActivity}
					onClose={() => setOpenStory(null)}
					onRequestChanges={requestStoryChanges}
					onOpenApprovals={onOpenApprovals}
				/>

				<ThreadNewObjectiveDialog
					open={objectiveDraft !== null}
					objective={objectiveDraft ?? ''}
					busy={objectiveBusy}
					failed={objectiveFailed}
					onClose={closeNewObjective}
					onResolve={resolveNewObjective}
				/>
			</div>
		);
	}

	const stageVariants = variantsForPhase(phase, handoffFromIntake);

	return (
		<div className="thread-conversation-stage">
			<AnimatePresence mode="popLayout" initial={false}>
				<m.div
					className="thread-conversation-phase"
					key={phase}
					variants={stageVariants}
					initial="initial"
					animate="animate"
					exit="exit"
				>
					{content}
				</m.div>
			</AnimatePresence>
		</div>
	);
}

/** Research report card generated by ResearchAgent and attached to the thread as a real artifact. */
function ThreadResearchCard({
	artifact,
	onOpenResearch,
}: {
	artifact: ThreadArtifact;
	onOpenResearch: () => void;
}) {
	const { t } = useI18n();
	const payload = researchPayload(artifact.metadata);
	const status = payload.status || 'research_required';
	const sources = payload.sources ?? [];
	const discrepancies = payload.discrepancies ?? [];
	const recommendation = payload.recommendation ?? {};
	const remediation = payload.remediation ?? {};
	const showSourceList = sources.length > 0 || status === 'research_ready';
	const showRecoveryState =
		status !== 'research_ready' && Boolean(payload.reason || remediation.summary);
	return (
		<m.section
			className="thread-research-card"
			aria-label={t('app.threads.research.title', 'Research')}
			variants={panelTransition}
			initial="initial"
			animate="animate"
		>
			<div className="thread-research-head">
				<div className="thread-research-title">
					<BookOpen aria-hidden="true" size={15} />
					<strong>{t('app.threads.research.historyTitle', 'Research history')}</strong>
				</div>
				<StatusChip tone={researchTone(status)}>{status.replace(/_/g, ' ')}</StatusChip>
			</div>
			<p className="thread-research-empty">
				{t(
					'app.threads.research.historyHelp',
					'This report is a historical result. Current status and repair actions are shown in Execution.',
				)}
			</p>

			<div className="thread-research-recommendation">
				<span>{t('app.threads.research.recommendation', 'Recommendation')}</span>
				<strong>{recommendation.title || payload.reason || artifact.title}</strong>
				<p>{recommendation.decision || payload.reason || ''}</p>
			</div>

			{showRecoveryState ? (
				<div className="thread-research-state" data-status={status}>
					<div className="thread-research-state-head">
						<AlertTriangle aria-hidden="true" size={14} />
						<span>{t('app.threads.research.recordedResult', 'Recorded result')}</span>
					</div>
					{payload.reason ? <strong>{payload.reason}</strong> : null}
					{remediation.summary ? <p>{remediation.summary}</p> : null}
				</div>
			) : null}

			{showSourceList ? (
				<details className="thread-research-disclosure" open>
					<summary>{t('app.threads.research.sources', 'Sources')}</summary>
					<div className="thread-research-source-list">
						{sources.length ? (
							sources.map((source, index) => (
								<div
									className="thread-research-source"
									key={source.id || source.artifactId || source.url || `source-${index}`}
								>
									<a href={source.url} target="_blank" rel="noreferrer">
										{source.publisher || source.url}
									</a>
									<StatusChip tone={source.trustLevel === 'untrusted' ? 'warn' : 'ok'}>
										{(source.trustLevel || 'untrusted').replace(/_/g, ' ')}
									</StatusChip>
									<div className="thread-research-source-meta">
										<span>
											<CalendarClock aria-hidden="true" size={13} />
											{source.fetchedAt || t('app.threads.research.noDate', 'No date')}
										</span>
										<span>
											<Hash aria-hidden="true" size={13} />
											{shortHash(source.hash)}
										</span>
									</div>
								</div>
							))
						) : (
							<p className="thread-research-empty">
								{t('app.threads.research.noSources', 'No sources persisted yet.')}
							</p>
						)}
					</div>
				</details>
			) : null}

			{discrepancies.length ? (
				<details className="thread-research-disclosure">
					<summary>{t('app.threads.research.discrepancies', 'Discrepancies')}</summary>
					<ul className="thread-research-discrepancies">
						{discrepancies.map((item) => (
							<li
								key={`${String(item.topic ?? 'discrepancy')}::${String(item.conflictingValues ?? '')}`}
							>
								<strong>{String(item.topic ?? 'source conflict')}</strong>
								<span>{String(item.conflictingValues ?? '')}</span>
							</li>
						))}
					</ul>
				</details>
			) : null}
			<Button variant="secondary" onClick={onOpenResearch}>
				{t('app.threads.remediation.action.openResearch', 'Open research')}
			</Button>
		</m.section>
	);
}

function researchPayload(metadata: unknown): ResearchCardPayload {
	if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) return {};
	const record = metadata as Record<string, unknown>;
	return {
		status: textValue(record.status),
		reason: textValue(record.reason),
		recommendation: recommendationValue(record.recommendation),
		remediation: remediationValue(record.remediation),
		sources: arrayValue(record.sources).map(sourceValue),
		discrepancies: arrayValue(record.discrepancies).filter(isRecord),
	};
}

function recommendationValue(value: unknown): ResearchRecommendationPayload {
	if (!isRecord(value)) return {};
	return {
		title: textValue(value.title),
		decision: textValue(value.decision),
		sourceCitations: arrayValue(value.sourceCitations).map(sourceValue),
	};
}

function remediationValue(value: unknown): ResearchRemediationPayload {
	if (!isRecord(value)) return {};
	return {
		action: textValue(value.action),
		summary: textValue(value.summary),
	};
}

function sourceValue(value: unknown): ResearchSourcePayload {
	if (!isRecord(value)) return {};
	return {
		id: textValue(value.id ?? value.sourceId),
		artifactId: textValue(value.artifactId),
		url: textValue(value.url),
		publisher: textValue(value.publisher),
		trustLevel: textValue(value.trustLevel),
		fetchedAt: textValue(value.fetchedAt),
		hash: textValue(value.hash),
	};
}

function shortHash(value: string | undefined): string {
	return value ? value.slice(0, 12) : 'pending';
}

function researchTone(status: string): 'ok' | 'warn' | 'danger' | 'info' | 'pending' {
	if (status === 'research_ready') return 'ok';
	if (status === 'research_blocked') return 'danger';
	if (status === 'research_running') return 'pending';
	if (status === 'research_required') return 'warn';
	return 'info';
}

/** Remaining-time chip next to the header status chip; a null ``eta`` (no loop yet) renders nothing. */
function ThreadEtaIndicator({ eta }: { eta: ThreadEta }) {
	const { t } = useI18n();
	if (eta.status === 'waiting_operator') {
		return (
			<StatusChip tone="pending">
				{t('app.threads.eta.waitingOperator', 'Waiting for your decision')}
			</StatusChip>
		);
	}
	if (eta.status === 'insufficient_history') {
		return (
			<StatusChip tone="info">
				{t('app.threads.eta.learning', 'Remaining time: learning')}
			</StatusChip>
		);
	}
	if (eta.remainingSeconds == null) return null;
	const label =
		eta.remainingSeconds < 60
			? t('app.threads.eta.lessThanMinute', '< 1 min left')
			: t('app.threads.eta.minutesRemaining', '≈ {minutes} min left').replace(
					'{minutes}',
					String(Math.round(eta.remainingSeconds / 60)),
				);
	const p90Minutes = Math.round((eta.remainingP90Seconds ?? eta.remainingSeconds) / 60);
	let title = t('app.threads.eta.title', 'up to {p90} min · based on {samples} previous runs')
		.replace('{p90}', String(p90Minutes))
		.replace('{samples}', String(eta.sampleCount));
	if (eta.includesOperatorApproval) {
		title = `${title} ${t('app.threads.eta.titleApprovalSuffix', '+ your approval')}`;
	}
	return (
		<StatusChip tone="pending" title={title}>
			{label}
		</StatusChip>
	);
}

/** One chat transcript row; intentionally flat so the thread reads like a console chat, not cards. */
function ThreadMessageRow({ message }: { message: ThreadMessage }) {
	const { t } = useI18n();
	const meta = MESSAGE_META[message.kind];
	return (
		<m.article
			className="thread-chat-row"
			data-kind={message.kind}
			variants={cardTransition}
			initial="initial"
			animate="animate"
		>
			<span className="thread-console-seq mono">#{message.sequence}</span>
			<span className="thread-console-actor">
				<StatusChip tone={meta.tone}>{t(meta.authorKey, message.author)}</StatusChip>
			</span>
			<p className="thread-row-content">{message.content}</p>
		</m.article>
	);
}

function mergeConsoleEvents(
	detailEvents: ThreadAgentEvent[],
	streamEvents: ThreadAgentEvent[],
): ThreadAgentEvent[] {
	const bySequence = new Map<number, ThreadAgentEvent>();
	for (const event of [...detailEvents, ...streamEvents]) {
		bySequence.set(event.sequence, event);
	}
	return [...bySequence.values()].sort((left, right) => left.sequence - right.sequence);
}

/** The three action-approval levels mapped onto the real `autonomy.level` setting. */
const PERMISSION_OPTIONS: ReadonlyArray<{ value: string; labelKey: string; fallback: string }> = [
	{ value: 'guided', labelKey: 'app.composer.permissions.guided', fallback: 'Ask for approval' },
	{
		value: 'recommended',
		labelKey: 'app.composer.permissions.recommended',
		fallback: 'Approve for me',
	},
	{ value: 'autonomous', labelKey: 'app.composer.permissions.autonomous', fallback: 'Full access' },
];

type ThreadComposerBoxProps = {
	project: Project;
	token: string;
	runtimeProviders: RuntimeProviders | null;
	onGitRefresh: () => void;
	/** Opens the runtime setup section in Settings — the chip's recovery path. */
	onOpenRuntimeSetup: () => void;
	value: string;
	busy: boolean;
	rows: number;
	submitLabel: string;
	submitBusyLabel: string;
	errorText?: string;
	/** Active-thread mode: post the typed message and clear on success. */
	onSendMessage?: (content: string) => Promise<void>;
	/** New-thread mode: create the thread from the typed message (title auto-derived). */
	onCreateThread?: (content: string) => Promise<void>;
	/** Mirrors every keystroke to the parent (used by the intake's similar-work lookup). */
	onValueChange?: (value: string) => void;
	/** Note mode (thread queued/running): the primary action saves a note instead of starting a run. */
	executing?: boolean;
	/** True while the stop-execution request is in flight (drives the secondary button spinner). */
	cancelBusy?: boolean;
	/** Note-mode primary action: append the typed text as an operator note (no run). */
	onAddNote?: (content: string) => Promise<void>;
	/** Note-mode secondary action: stop the thread's in-flight execution. */
	onCancelExecution?: () => void;
	/** Note-mode escape hatch: hand the typed text to the "new objective" decision modal. */
	onNewObjective?: (content: string) => void;
	/** Context-row control for the thread's AI team (chip + drawer), rendered before the git bar. */
	teamControl?: ReactNode;
};

/**
 * The Codex-style composer box shared by the new-thread intake and the active conversation: a single
 * rounded surface with the prompt, the action-approval level (wired to the real `autonomy.level`
 * setting), Send, and the workspace context row (project, local runtime, runtime readiness chip,
 * git branch/scan). The readiness chip deep-links into runtime setup and turns into the setup CTA
 * when nothing is executable, so the composer never shows a dead "0 runtimes" fact.
 */
function ThreadComposerBox({
	project,
	token,
	runtimeProviders,
	onGitRefresh,
	onOpenRuntimeSetup,
	value: initialValue,
	busy,
	rows,
	submitLabel,
	submitBusyLabel,
	errorText,
	onSendMessage,
	onCreateThread,
	onValueChange,
	executing = false,
	cancelBusy = false,
	onAddNote,
	onCancelExecution,
	onNewObjective,
	teamControl,
}: ThreadComposerBoxProps) {
	const { t } = useI18n();
	const [value, setValue] = useState(initialValue);
	const [localBusy, setLocalBusy] = useState(false);
	const { project: projectSettings, setValue: setSetting } = useSettings(project.id, true, token);
	const [permBusy, setPermBusy] = useState(false);

	const autonomy = projectSettings.find((setting) => setting.key === 'autonomy.level');
	const autonomyValue = typeof autonomy?.value === 'string' ? autonomy.value : 'guided';
	const working = busy || localBusy;
	const executableRuntimes =
		runtimeProviders?.providers.filter((provider) => provider.executable).length ?? 0;
	const runtimesBlocked = runtimeProviders !== null && executableRuntimes === 0;

	const onPermissionChange = async (next: string) => {
		setPermBusy(true);
		try {
			await setSetting('autonomy.level', 'project', project.id, next);
		} finally {
			setPermBusy(false);
		}
	};

	const submitNow = async () => {
		const content = value.trim();
		if (!content || working) return;
		setLocalBusy(true);
		try {
			if (executing && onAddNote) {
				setValue('');
				try {
					await onAddNote(content);
				} catch {
					setValue(content);
				}
			} else if (onSendMessage) {
				setValue('');
				try {
					await onSendMessage(content);
				} catch {
					setValue(content);
				}
			} else if (onCreateThread) {
				await onCreateThread(content);
			}
		} finally {
			setLocalBusy(false);
		}
	};

	const submit = (event: FormEvent) => {
		event.preventDefault();
		void submitNow();
	};

	const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
		if (event.key === 'Enter' && !event.shiftKey) {
			event.preventDefault();
			void submitNow();
		}
	};

	return (
		<form className="thread-composer-box" onSubmit={submit}>
			<TextArea
				label={
					executing
						? t('app.threads.noteLabel', 'Add a note')
						: t('app.threads.composerLabel', 'Message AIDO')
				}
				value={value}
				rows={rows}
				placeholder={
					executing
						? t('app.threads.notePlaceholder', 'Add a note or instruction for the running loop…')
						: t('app.threads.composerPlaceholder', 'Describe the work or ask a question…')
				}
				onChange={(event) => {
					setValue(event.target.value);
					onValueChange?.(event.target.value);
				}}
				onKeyDown={onKeyDown}
				error={errorText}
			/>
			{executing ? (
				<>
					<div className="thread-note-mode-hint" role="note">
						<StickyNote aria-hidden="true" size={14} />
						<span>
							<strong>{t('app.threads.noteMode.title', 'Note mode')}</strong>{' '}
							{t(
								'app.threads.noteMode.hint',
								'Execution running — your message is saved as a note, not a new run.',
							)}
						</span>
					</div>
					<div className="shell-chat-options">
						<button
							type="button"
							className="composer-new-objective"
							disabled={!value.trim()}
							onClick={() => onNewObjective?.(value.trim())}
						>
							<Target aria-hidden="true" size={14} />
							{t('app.threads.newObjective.trigger', 'New objective? Decide what to do')}
						</button>
						<div className="thread-note-actions">
							<Button
								type="button"
								variant="secondary"
								loading={cancelBusy}
								disabled={cancelBusy}
								icon={<Ban aria-hidden="true" size={15} />}
								onClick={() => onCancelExecution?.()}
							>
								{cancelBusy
									? t('app.threads.stoppingExecution', 'Stopping…')
									: t('app.threads.stopExecution', 'Stop execution')}
							</Button>
							<Button
								type="submit"
								variant="primary"
								className="shell-chat-send"
								loading={working}
								disabled={!value.trim()}
								icon={<StickyNote aria-hidden="true" size={15} />}
							>
								{working
									? t('app.threads.addingNote', 'Adding…')
									: t('app.threads.addNote', 'Add note')}
							</Button>
						</div>
					</div>
				</>
			) : (
				<div className="shell-chat-options">
					<span
						className="composer-perms"
						data-level={autonomyValue}
						data-execution-blocked={runtimesBlocked ? '' : undefined}
					>
						<ShieldCheck aria-hidden="true" size={14} />
						<select
							className="status-branch-select composer-perms-select"
							aria-label={t('app.composer.permissions', 'Action approval')}
							value={autonomyValue}
							disabled={permBusy}
							onChange={(event) => void onPermissionChange(event.target.value)}
						>
							{PERMISSION_OPTIONS.map((option) => (
								<option key={option.value} value={option.value}>
									{t(option.labelKey, option.fallback)}
								</option>
							))}
						</select>
					</span>
					<Button
						type="submit"
						variant="primary"
						className="shell-chat-send"
						loading={working}
						disabled={!value.trim()}
						icon={<Send aria-hidden="true" size={15} />}
					>
						{working ? submitBusyLabel : submitLabel}
					</Button>
				</div>
			)}
			<div className="shell-chat-context">
				<span className="shell-chat-context-project">{project.name}</span>
				<span
					className="composer-env"
					title={t('app.composer.localHint', 'AIDO runs on this machine')}
				>
					<Laptop aria-hidden="true" size={13} />
					{t('app.composer.local', 'Local')}
				</span>
				{runtimeProviders ? (
					<button
						type="button"
						className="composer-env composer-runtimes"
						data-tone={executableRuntimes === 0 ? 'warn' : undefined}
						onClick={onOpenRuntimeSetup}
					>
						<StatusDot tone={executableRuntimes > 0 ? 'ok' : 'warn'} />
						<span className="composer-runtimes-label" aria-live="polite">
							{executableRuntimes > 0 ? (
								<>
									<span className="tnum">{executableRuntimes}</span>{' '}
									{executableRuntimes === 1
										? t('app.composer.executableRuntime', 'executable runtime')
										: t('app.statusBar.executableRuntimes', 'executable runtimes')}
								</>
							) : (
								t('app.statusBar.configureRuntimes', 'Set up runtimes')
							)}
						</span>
					</button>
				) : null}
				{teamControl}
				<GitBranchBar selectedProject={project} token={token} onRefresh={onGitRefresh} />
			</div>
		</form>
	);
}

type NewObjectiveMode = 'add_to_loop' | 'new_thread' | 'replace';

/** The three deliberate resolutions for a new objective typed during execution. The stacked-choice
 *  shape mirrors the similarity card so both decision surfaces read the same. */
const NEW_OBJECTIVE_OPTIONS: ReadonlyArray<{
	mode: NewObjectiveMode;
	labelKey: string;
	labelFallback: string;
	hintKey: string;
	hintFallback: string;
	danger?: boolean;
}> = [
	{
		mode: 'add_to_loop',
		labelKey: 'app.threads.newObjective.addToLoop',
		labelFallback: 'Add to the current loop',
		hintKey: 'app.threads.newObjective.addToLoopHint',
		hintFallback: 'Saved as a note for the running execution. Nothing new starts.',
	},
	{
		mode: 'new_thread',
		labelKey: 'app.threads.newObjective.newThread',
		labelFallback: 'Create a new thread',
		hintKey: 'app.threads.newObjective.newThreadHint',
		hintFallback: 'Starts a separate thread with this objective. The current one keeps running.',
	},
	{
		mode: 'replace',
		labelKey: 'app.threads.newObjective.replace',
		labelFallback: 'Cancel current and replace',
		hintKey: 'app.threads.newObjective.replaceHint',
		hintFallback: 'Stops the running execution and starts this objective here.',
		danger: true,
	},
];

/** Decision modal shown when the operator types a fresh objective into a running thread. It never
 *  starts a run on its own — the operator must consciously pick a resolution, so a concurrent loop is
 *  always intentional. Purely presentational; every action lives in the parent's `onResolve`. */
function ThreadNewObjectiveDialog({
	open,
	objective,
	busy,
	failed,
	onClose,
	onResolve,
}: {
	open: boolean;
	objective: string;
	busy: NewObjectiveMode | null;
	failed: boolean;
	onClose: () => void;
	onResolve: (mode: NewObjectiveMode) => void;
}) {
	const { t } = useI18n();
	const anyBusy = busy !== null;
	return (
		<Dialog
			open={open}
			onClose={onClose}
			label={t('app.threads.newObjective.title', 'An execution is already running')}
			className="thread-objective-dialog"
		>
			<p className="thread-objective-body">
				{t(
					'app.threads.newObjective.body',
					'Choose what to do with this objective without accidentally starting a second run:',
				)}
			</p>
			<figure className="thread-objective-preview">
				<figcaption>{t('app.threads.newObjective.yourObjective', 'Your objective')}</figcaption>
				<blockquote>{objective}</blockquote>
			</figure>
			<div className="thread-objective-options">
				{NEW_OBJECTIVE_OPTIONS.map((option) => (
					<button
						key={option.mode}
						type="button"
						className="thread-objective-option"
						data-danger={option.danger ? '' : undefined}
						data-busy={busy === option.mode ? '' : undefined}
						disabled={anyBusy}
						onClick={() => onResolve(option.mode)}
					>
						<span className="thread-objective-option-label">
							{t(option.labelKey, option.labelFallback)}
						</span>
						<span className="thread-objective-option-hint">
							{t(option.hintKey, option.hintFallback)}
						</span>
					</button>
				))}
			</div>
			{failed ? (
				<p className="thread-objective-error" role="alert">
					{t('app.threads.newObjective.error', 'Could not complete the action. Try again.')}
				</p>
			) : null}
			<div className="thread-objective-footer">
				<Button variant="secondary" disabled={anyBusy} onClick={onClose}>
					{t('app.threads.newObjective.cancel', 'Keep writing')}
				</Button>
			</div>
		</Dialog>
	);
}

/** Mirrors the backend HIGH_SIMILARITY_THRESHOLD: only real matches interrupt the intake. */
const SIMILARITY_THRESHOLD = 0.6;
/** Below this many characters no lexical token survives normalization, so the lookup is skipped. */
const SIMILARITY_MIN_QUERY = 3;
const SIMILARITY_DEBOUNCE_MS = 500;

type SimilarityReuseMode = 'improve_existing' | 'performance_pass' | 'create_new_anyway';

/** New-thread intake: the Codex-style composer box under a heading. The title is auto-derived from
 *  the first line of the message (no manual field), the way modern AI IDEs name conversations.
 *  While the operator types, a debounced similarity lookup surfaces already-done work so they can
 *  continue/improve an existing thread instead of duplicating it. */
function NewThreadComposer({
	overview,
	project,
	token,
	runtimeProviders,
	onGitRefresh,
	onOpenRuntimeSetup,
	onCreated,
}: {
	overview: Overview;
	project: Project;
	token: string;
	runtimeProviders: RuntimeProviders | null;
	onGitRefresh: () => void;
	onOpenRuntimeSetup: () => void;
	onCreated: (threadId: string) => void;
}) {
	const { t } = useI18n();
	const [failed, setFailed] = useState(false);
	const [draft, setDraft] = useState('');
	const [candidate, setCandidate] = useState<ThreadSimilarityCandidate | null>(null);
	const [reuseBusy, setReuseBusy] = useState<SimilarityReuseMode | null>(null);
	const [reuseFailed, setReuseFailed] = useState(false);
	const [runtimeTeam, setRuntimeTeam] = useState<RuntimeTeamSelection | null>(null);
	const [creating, setCreating] = useState(false);
	const pendingCreatedThreadRef = useRef<{
		threadId: string;
		dismissedCandidate: ThreadSimilarityCandidate | null;
		teamSaved: boolean;
	} | null>(null);

	useEffect(() => {
		const query = draft.trim();
		if (query.length < SIMILARITY_MIN_QUERY) {
			setCandidate(null);
			return undefined;
		}
		const controller = new AbortController();
		const timer = window.setTimeout(() => {
			findSimilarThreads(project.id, query, 1, controller.signal)
				.then((response) => {
					const top = response.candidates[0];
					setCandidate(top && top.score >= SIMILARITY_THRESHOLD ? top : null);
				})
				.catch(() => undefined);
		}, SIMILARITY_DEBOUNCE_MS);
		return () => {
			controller.abort();
			window.clearTimeout(timer);
		};
	}, [draft, project.id]);

	const createThreadFromMessage = async (firstMessage: string) => {
		const firstLine = firstMessage.split(/\r?\n/)[0]?.trim() ?? '';
		const threadTitle = (firstLine || firstMessage).slice(0, 80);
		let pending = pendingCreatedThreadRef.current;
		const dismissedCandidate = pending?.dismissedCandidate ?? candidate;
		setFailed(false);
		setCreating(true);
		try {
			if (!pending) {
				const created = await createThread(token, {
					projectId: project.id,
					ownerType: 'workspace',
					ownerId: ownerIdForProject(overview, project),
					title: threadTitle,
				});
				pending = { threadId: created.thread.id, dismissedCandidate, teamSaved: false };
				pendingCreatedThreadRef.current = pending;
			}
			if (!pending) throw new Error('Thread creation did not return a durable thread id.');
			const pendingThreadId = pending.threadId;
			if (runtimeTeam || pending.teamSaved) {
				await updateThreadRunConfiguration(token, pendingThreadId, runtimeTeamRequest(runtimeTeam));
				pending.teamSaved = runtimeTeam !== null;
			}
			await postThreadMessage(token, pendingThreadId, {
				content: firstMessage,
				...(dismissedCandidate
					? {
							metadata: {
								mode: 'create_new_anyway',
								similarThreadId: dismissedCandidate.threadId,
							},
						}
					: {}),
			});
			pendingCreatedThreadRef.current = null;
			onGitRefresh();
			onCreated(pendingThreadId);
			if (dismissedCandidate) {
				try {
					await markSimilarThread(token, pendingThreadId, dismissedCandidate.threadId, {
						action: 'create_new_anyway',
						score: dismissedCandidate.score,
						reason: dismissedCandidate.reason,
					});
				} catch {}
			}
		} catch (creationError) {
			const pendingThread = pendingCreatedThreadRef.current;
			if (pendingThread) {
				try {
					const detail = await getThread(pendingThread.threadId);
					const firstMessagePersisted = detail.messages.some(
						(message) => message.kind === 'user' && message.content === firstMessage,
					);
					if (firstMessagePersisted) {
						pendingCreatedThreadRef.current = null;
						onGitRefresh();
						onCreated(pendingThread.threadId);
						return;
					}
				} catch {}
			}
			setFailed(true);
			throw creationError;
		} finally {
			setCreating(false);
		}
	};

	/** Sends the draft to the matched thread with the chosen mode instead of creating a new one. */
	const reuseExistingThread = async (mode: 'improve_existing' | 'performance_pass') => {
		const content = draft.trim();
		if (!candidate || !content || reuseBusy) return;
		const target = candidate.threadId;
		setReuseFailed(false);
		setReuseBusy(mode);
		try {
			await postThreadMessage(token, target, { content, metadata: { mode } });
			onGitRefresh();
			onCreated(target);
		} catch {
			setReuseFailed(true);
		} finally {
			setReuseBusy(null);
		}
	};

	/** The card's explicit "create new anyway": same flow as the composer submit. */
	const createNewAnyway = async () => {
		const content = draft.trim();
		if (!content || reuseBusy) return;
		setReuseBusy('create_new_anyway');
		try {
			await createThreadFromMessage(content);
		} catch {
		} finally {
			setReuseBusy(null);
		}
	};

	const heading = t('app.threads.newHeading', 'What will we work on in {project}?').replace(
		'{project}',
		project.name,
	);

	return (
		<div className="shell-chat-layout">
			<m.div className="thread-intake" variants={listStagger} initial="initial" animate="animate">
				<m.h2 className="thread-intake-heading" variants={cardTransition}>
					{heading}
				</m.h2>
				<m.div className="thread-intake-form" variants={cardTransition}>
					<ThreadComposerBox
						project={project}
						token={token}
						runtimeProviders={runtimeProviders}
						onGitRefresh={onGitRefresh}
						onOpenRuntimeSetup={onOpenRuntimeSetup}
						value=""
						busy={reuseBusy !== null}
						rows={4}
						submitLabel={t('app.threads.create', 'Create thread')}
						submitBusyLabel={t('app.threads.creating', 'Creating…')}
						errorText={
							failed
								? t('app.threads.errorSend', 'Could not send the message. Try again.')
								: undefined
						}
						onCreateThread={createThreadFromMessage}
						onValueChange={setDraft}
						teamControl={
							<RuntimeTeamChip
								projectId={project.id}
								token={token}
								selection={runtimeTeam}
								disabled={creating}
								onChange={setRuntimeTeam}
							/>
						}
					/>
					<div className="thread-similarity-slot" aria-live="polite">
						<AnimatePresence initial={false}>
							{candidate ? (
								<ThreadSimilaritySuggestion
									candidate={candidate}
									reuseBusy={reuseBusy}
									reuseFailed={reuseFailed}
									onContinueExisting={() => onCreated(candidate.threadId)}
									onReuseExisting={reuseExistingThread}
									onCreateNew={createNewAnyway}
								/>
							) : null}
						</AnimatePresence>
					</div>
				</m.div>
			</m.div>
		</div>
	);
}

/** The similar-work card: one matched thread and the four deduplication decisions the operator can
 *  take on it. Purely presentational; every action stays in the intake's handlers. */
function ThreadSimilaritySuggestion({
	candidate,
	reuseBusy,
	reuseFailed,
	onContinueExisting,
	onReuseExisting,
	onCreateNew,
}: {
	candidate: ThreadSimilarityCandidate;
	reuseBusy: SimilarityReuseMode | null;
	reuseFailed: boolean;
	onContinueExisting: () => void;
	onReuseExisting: (mode: 'improve_existing' | 'performance_pass') => Promise<void>;
	onCreateNew: () => Promise<void>;
}) {
	const { t } = useI18n();
	const busy = reuseBusy !== null;
	return (
		<m.section
			className="thread-similarity-card"
			aria-labelledby="thread-similarity-title"
			initial={{ opacity: 0, y: 8 }}
			animate={{ opacity: 1, y: 0, transition: { duration: 0.2, ease: EASE_OUT } }}
			exit={{ opacity: 0, y: 8, transition: { duration: 0.14, ease: EASE_OUT } }}
		>
			<div className="thread-similarity-head">
				<History aria-hidden="true" size={15} />
				<h3 id="thread-similarity-title">
					{t('app.threads.similar.title', 'This was already worked on in “{thread}”').replace(
						'{thread}',
						candidate.title,
					)}
				</h3>
				<StatusChip tone="info">
					{t('app.threads.similar.match', '{score}% match').replace(
						'{score}',
						String(Math.round(candidate.score * 100)),
					)}
				</StatusChip>
				{candidate.status === 'archived' ? (
					<StatusChip tone={threadStatusTone(candidate.status)}>
						{t('app.threads.similar.archived', 'Archived')}
					</StatusChip>
				) : null}
			</div>
			<p className="thread-similarity-candidate">
				<strong>{candidate.title}</strong>
				{candidate.summary ? <span>{candidate.summary}</span> : null}
			</p>
			<fieldset className="thread-similarity-actions">
				<legend className="sr-only">
					{t('app.threads.similar.actionsLabel', 'Choose what to do with this match')}
				</legend>
				<Button variant="secondary" disabled={busy} onClick={onContinueExisting}>
					{t('app.threads.similar.continue', 'Continue existing thread')}
				</Button>
				<Button
					variant="secondary"
					loading={reuseBusy === 'improve_existing'}
					disabled={busy}
					onClick={() => void onReuseExisting('improve_existing')}
				>
					{t('app.threads.similar.improve', 'Refactor existing work')}
				</Button>
				<Button
					variant="secondary"
					loading={reuseBusy === 'performance_pass'}
					disabled={busy}
					onClick={() => void onReuseExisting('performance_pass')}
				>
					{t('app.threads.similar.performance', 'Improve performance')}
				</Button>
				<Button
					variant="secondary"
					loading={reuseBusy === 'create_new_anyway'}
					disabled={busy}
					onClick={() => void onCreateNew()}
				>
					{t('app.threads.similar.createNew', 'Create new thread anyway')}
				</Button>
			</fieldset>
			{reuseFailed ? (
				<p className="thread-similarity-error" role="alert">
					{t('app.threads.similar.error', 'Could not reuse the existing thread. Try again.')}
				</p>
			) : null}
		</m.section>
	);
}
