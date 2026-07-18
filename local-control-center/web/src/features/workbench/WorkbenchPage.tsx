/**
 * Workbench IDE shell: a single composer drives the product loop (conversation → questions →
 * brief → assumptions → decisions → architecture → backlog → iteration → execution → review),
 * laid out with the explorer and inspector. Owns the page-level UI state (prompt, active loop
 * section, selected thread/run) and wires intake to project threads; the derived data comes from
 * useWorkbenchData. Sections wired to live overview data render
 * real content; the discovery/backlog sections render an honest shell until their endpoint exists.
 *
 * When hideExplorer=true (shell mode) the center renders a clean chat-first layout: transcript
 * leads, composer is pinned at the bottom, auxiliary panels are hidden.
 * @author Rodrigo Mason
 */
import { CheckCircle2, FolderKanban, Rocket, Users } from 'lucide-react';
import type { KeyboardEvent } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
	aidoDecideProductLoop,
	applyProductLoopFeedback,
	approveProductBrief,
	approveProductLoopBacklog,
	createThread,
	getProjectGitStatus,
	postThreadMessage,
	runProductOwnerAgent,
	startProductLoop,
	transitionProductLoop,
} from '../../api/client';
import type {
	Overview,
	Project,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../../api/types';
import {
	StatusChip as Badge,
	Button,
	Drawer,
	EmptyState,
	PageHeader,
	SegmentedControl,
	Surface,
	TextArea,
	TextField,
	useToast,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { formatTime, shortId, toneForStatus } from '../../lib/format';
import { GitBranchBar } from '../shell/GitBranchBar';
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
import { NEW_SESSION_ID, useWorkbenchData } from './useWorkbenchData';
import { WorkbenchExplorer } from './WorkbenchExplorer';
import { WorkbenchInspector } from './WorkbenchInspector';
import { WorkbenchTabs } from './WorkbenchTabs';
import { WorkflowTimeline } from './WorkflowTimeline';

type Mutate = <T>(
	operation: (token: string) => Promise<T>,
	options?: { awaitRefresh?: boolean },
) => Promise<T>;

type WorkbenchUserMode = 'consulta' | 'aido_decide';

/** Thread records shown before the "view full history" toggle reveals the rest. */
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
	onRefresh: () => Promise<unknown> | undefined;
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

function ownerIdForProject(overview: Overview, project: Project): string {
	const workspace = overview.runtimeWorkspaces.find((entry) => entry.projectId === project.id);
	return workspace?.id ?? project.id;
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
	const [userMode, setUserMode] = useState<WorkbenchUserMode>('aido_decide');
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
		threadId: string;
		messageId: string;
	} | null>(null);
	const [activeSection, setActiveSection] = useState<ProductLoopSectionId>('conversation');
	const [selectedRunId, setSelectedRunId] = useState('');
	const [teamOpen, setTeamOpen] = useState(false);
	const [showAllChats, setShowAllChats] = useState(false);
	const [loopStarting, setLoopStarting] = useState(false);
	const [loopActionBusy, setLoopActionBusy] = useState('');
	const [detectedGitBranch, setDetectedGitBranch] = useState('');

	const latestDraftRef = useRef<{ projectId: string; draft: ComposerDraft } | null>(null);
	const hydratedProjectIdRef = useRef('');
	const abortRef = useRef<AbortController | null>(null);

	const {
		projects,
		project,
		projectSessions,
		activeSession,
		sessionChats,
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
	const activeProductLoop = loop.data?.loops[0] ?? null;

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

	useEffect(() => {
		const projectId = project?.id ?? '';
		const draft = projectId ? readComposerDraft(projectId) : null;
		if (draft) {
			setPrompt(draft.prompt);
			setTitle(draft.title);
			setTitleEdited(draft.titleEdited);
			setUserMode(draft.userMode ?? 'aido_decide');
		} else {
			setPrompt('');
			setTitle('');
			setTitleEdited(false);
			setUserMode('aido_decide');
		}
	}, [project?.id]);

	latestDraftRef.current = project
		? { projectId: project.id, draft: { prompt, title, titleEdited, userMode } }
		: null;

	useEffect(() => {
		const projectId = project?.id ?? '';
		if (!projectId || hydratedProjectIdRef.current !== projectId) return;
		const handle = window.setTimeout(() => {
			persistComposerDraft(projectId, { prompt, title, titleEdited, userMode });
		}, 400);
		return () => window.clearTimeout(handle);
	}, [project?.id, prompt, title, titleEdited, userMode]);

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
					let threadId = activeSession?.id ?? '';
					if (!threadId) {
						const createdThread = await createThread(
							token,
							{
								projectId: activeProject.id,
								ownerType: 'workspace',
								ownerId: ownerIdForProject(overview, activeProject),
								title: submitTitle,
							},
							signal,
						);
						threadId = createdThread.thread.id;
					}
					const messageResult = await postThreadMessage(
						token,
						threadId,
						{ content: text, author: userMode === 'consulta' ? 'operator' : 'user' },
						signal,
					);
					return {
						thread: messageResult.thread,
						message: messageResult.messages[0],
					};
				},
				{ awaitRefresh: false },
			);
			setCreated({
				threadId: result.thread.id,
				messageId: result.message?.id ?? '',
			});
			setSelectedSessionId(result.thread.id);
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

	const runLoopAction = async (
		action: string,
		operation: () => Promise<unknown>,
		successTitle: string,
		failureTitle: string,
	) => {
		if (!project || loopActionBusy) return;
		setLoopActionBusy(action);
		try {
			await operation();
			loop.refresh();
			notify({ title: successTitle, tone: 'ok' });
		} catch (actionError) {
			notify({
				title: failureTitle,
				body: actionError instanceof Error ? actionError.message : undefined,
				tone: 'danger',
			});
		} finally {
			setLoopActionBusy('');
		}
	};

	const handleAidoDecide = () => {
		if (!project || !activeProductLoop) return;
		void runLoopAction(
			'aido_decide',
			() =>
				aidoDecideProductLoop(token, project.id, activeProductLoop.id, {
					reason: 'AIDO selected the recommended product decisions.',
				}),
			t('app.workbench.loop.aidoDecideDone', 'AIDO decisions recorded'),
			t('app.workbench.loop.aidoDecideFailed', 'Could not record AIDO decisions'),
		);
	};

	const handleApproveBrief = () => {
		const brief = loop.data?.brief;
		if (!project || !brief) return;
		void runLoopAction(
			'approve_brief',
			() =>
				approveProductBrief(token, project.id, brief.id, {
					reason: 'Product brief approved from the Workbench.',
				}),
			t('app.workbench.loop.briefApproved', 'Product brief approved'),
			t('app.workbench.loop.briefApproveFailed', 'Could not approve the product brief'),
		);
	};

	const handleApproveBacklog = () => {
		if (!project || !activeProductLoop) return;
		void runLoopAction(
			'approve_backlog',
			() =>
				approveProductLoopBacklog(token, project.id, activeProductLoop.id, {
					reason: 'Product backlog approved from the Workbench.',
				}),
			t('app.workbench.loop.backlogApproved', 'Product backlog approved'),
			t('app.workbench.loop.backlogApproveFailed', 'Could not approve the product backlog'),
		);
	};

	const handleStartIteration = () => {
		if (!project || !activeProductLoop) return;
		void runLoopAction(
			'start_iteration',
			() =>
				transitionProductLoop(token, project.id, activeProductLoop.id, {
					toState: 'iteration_planning',
					reason: 'Iteration started from the Workbench backlog.',
					trigger: 'operator_start_iteration',
				}),
			t('app.workbench.loop.iterationStarted', 'Iteration started'),
			t('app.workbench.loop.iterationStartFailed', 'Could not start the iteration'),
		);
	};

	const handleAcceptDelivery = () => {
		if (!project || !activeProductLoop) return;
		void runLoopAction(
			'accept_delivery',
			() =>
				applyProductLoopFeedback(token, project.id, activeProductLoop.id, {
					action: 'accept',
					feedback: 'Delivery approved from the Workbench after reviewing evidence.',
					targetType: 'loop',
					targetId: activeProductLoop.id,
					expectedVersion: activeProductLoop.version,
				}),
			t('app.workbench.loop.deliveryApproved', 'Delivery approved'),
			t('app.workbench.loop.deliveryApproveFailed', 'Could not approve the delivery'),
		);
	};

	const handleRequestDeliveryChanges = (taskId: string, feedback: string) => {
		if (!project || !activeProductLoop) return;
		void runLoopAction(
			'request_delivery_changes',
			() =>
				applyProductLoopFeedback(token, project.id, activeProductLoop.id, {
					action: 'request_changes',
					feedback,
					targetType: 'task',
					targetId: taskId,
					expectedVersion: activeProductLoop.version,
				}),
			t('app.workbench.loop.deliveryChangesRequested', 'Changes requested'),
			t('app.workbench.loop.deliveryChangesRequestFailed', 'Could not request delivery changes'),
		);
	};

	const handleContinueDelivery = (feedback: string) => {
		if (!project || !activeProductLoop) return;
		void runLoopAction(
			'continue_delivery',
			() =>
				applyProductLoopFeedback(token, project.id, activeProductLoop.id, {
					action: 'continue',
					feedback,
					targetType: 'loop',
					targetId: activeProductLoop.id,
					expectedVersion: activeProductLoop.version,
				}),
			t('app.workbench.loop.deliveryContinued', 'Delivery continuation recorded'),
			t('app.workbench.loop.deliveryContinueFailed', 'Could not continue the delivery loop'),
		);
	};

	const expansionWorkspaceId =
		projectWorkspaces.find((workspace) => workspace.status !== 'archived')?.id ?? '';

	const handleExpandEpic = (epicId: string) => {
		if (!project || !expansionWorkspaceId) return;
		void runLoopAction(
			'expand_epic',
			() =>
				runProductOwnerAgent(token, {
					projectId: project.id,
					workspaceId: expansionWorkspaceId,
					taskId: `epic-expansion-${epicId}`,
					epicId,
				}),
			t('app.workbench.loop.epicExpanded', 'Epic expanded into new stories'),
			t('app.workbench.loop.epicExpandFailed', 'Could not expand the epic'),
		);
	};

	const productLoopActions = {
		busy: Boolean(loopActionBusy),
		onAidoDecide: activeProductLoop ? handleAidoDecide : undefined,
		onApproveBrief: loop.data?.brief ? handleApproveBrief : undefined,
		onApproveBacklog: activeProductLoop ? handleApproveBacklog : undefined,
		onStartIteration:
			activeProductLoop?.state === 'backlog_ready' ? handleStartIteration : undefined,
		onExpandEpic: expansionWorkspaceId ? handleExpandEpic : undefined,
	};

	const onSelectRun = (runId: string) => {
		setSelectedRunId(runId);
		setActiveSection('iteration');
	};

	const loopSections = buildProductLoopSections({
		conversation: sessionChats.length,
		questions: loop.data?.questions.length ?? 0,
		brief: loop.data?.brief ? 1 : 0,
		assumptions: loop.data?.assumptions.length ?? 0,
		decisions: loop.data?.decisions.length ?? 0,
		backlog:
			(loop.data?.epics.length ?? 0) +
			(loop.data?.stories.length ?? 0) +
			(loop.data?.tasks.length ?? 0),
		iteration: projectWorkflowRuns.length,
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

	const modeControl = (
		<SegmentedControl<WorkbenchUserMode>
			label={t('app.workbench.composer.modeLabel', 'Workbench mode')}
			value={userMode}
			disabled={!project || busy}
			onChange={setUserMode}
			options={[
				{ value: 'consulta', label: t('app.workbench.mode.consulta', 'Consultation') },
				{ value: 'aido_decide', label: t('app.workbench.mode.loop', 'Agents / Loop') },
			]}
		/>
	);
	const submitLabel =
		userMode === 'consulta'
			? t('app.workbench.mode.consulta', 'Consultation')
			: t('app.workbench.mode.loop', 'Agents / Loop');

	if (hideExplorer) {
		return (
			<section
				className="shell-chat-layout"
				aria-label={t('app.workbench.primaryRegion', 'Thread and progress')}
			>
				{/* --- Transcript: leads, fills available height, scrolls --- */}
				<div
					id="shell-chat-transcript"
					className="shell-chat-transcript"
					aria-label={t('app.workbench.shell.transcriptRegion', 'Thread transcript')}
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
									<p>{chat.summary}</p>
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
								: t('app.workbench.chat.viewFull', 'View full thread history')}
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
							<span>{t('app.workbench.chat.created', 'Thread intake created')}</span>
							<span className="mono">{shortId(created.threadId)}</span>
							<span>{t('app.workbench.chat.pipelineLinked', 'message')}</span>
							<span className="mono">{shortId(created.messageId)}</span>
						</div>
					) : null}

					<TextArea
						label={t('app.workbench.composer.promptLabel', 'What should AIDO do?')}
						value={prompt}
						rows={3}
						disabled={!project || busy}
						placeholder={t(
							'app.workbench.composer.promptPlaceholder',
							'Describe the work in plain language.',
						)}
						onChange={(event) => setPrompt(event.target.value)}
						onKeyDown={onComposerKeyDown}
					/>
					{modeControl}

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
									{busy ? t('app.workbench.chat.creating', 'Creating thread intake') : submitLabel}
								</Button>
							)}
						</div>
					</div>

					{/* Workspace toolbar: project label + clustered git controls (branch, status, actions) */}
					<div className="shell-chat-context">
						<span className="shell-chat-context-project">{project.name}</span>
						<GitBranchBar selectedProject={project} token={token} onRefresh={onRefresh} />
					</div>
				</div>
			</section>
		);
	}

	return (
		// `.workbench-canvas` is the query container for `.workbench-layout`: the grid must reflow on the
		// width of this resizable pane, which is far narrower than the viewport a media query would read.
		<div className="workbench-canvas">
			{header}
			<div className="workbench-layout">
				<WorkbenchExplorer
					projects={projects}
					project={project}
					branch={displayBranch}
					sessions={projectSessions}
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
									? activeSession.title
									: t('app.workbench.chat.title', 'New project thread')}
							</strong>
							<div className="inline">
								<Badge tone={toneForStatus(latestRunStatus)}>{latestRunStatus}</Badge>
							</div>
						</div>
						<button
							aria-label={t('app.workbench.team.openLoop', 'Open Agents / Loop')}
							className="button"
							type="button"
							onClick={() => setTeamOpen(true)}
						>
							<Users aria-hidden="true" size={15} /> {t('app.workbench.mode.loop', 'Agents / Loop')}
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
									'Describe the work in plain language.',
								)}
								onChange={(event) => setPrompt(event.target.value)}
							/>
							{modeControl}
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
									{busy ? t('app.workbench.chat.creating', 'Creating thread intake') : submitLabel}
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
									<span>{t('app.workbench.chat.created', 'Thread intake created')}</span>
									<span className="mono">{shortId(created.threadId)}</span>
									<span>{t('app.workbench.chat.pipelineLinked', 'message')}</span>
									<span className="mono">{shortId(created.messageId)}</span>
								</div>
							) : null}

							{/* Workspace toolbar: mirrors the shell-chat composer toolbar for consistency */}
							<div className="shell-chat-context">
								<span className="shell-chat-context-project">{project.name}</span>
								<GitBranchBar selectedProject={project} token={token} onRefresh={onRefresh} />
							</div>
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
									aria-label={t('app.workbench.chat.history', 'Thread history')}
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
													<p>{chat.summary}</p>
												</article>
											),
										)
									) : (
										<EmptyState
											title={t('app.workbench.chat.emptyTitle', 'No messages in this thread')}
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
											: t('app.workbench.chat.viewFull', 'View full thread history')}
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
								actions={productLoopActions}
							/>
						) : null}
						{activeSection === 'brief' ? (
							<ProductLoopSection
								section="brief"
								data={loop.data}
								architectureDecisions={projectArchitectureDecisions}
								loading={loop.loading}
								error={loop.error}
								actions={productLoopActions}
							/>
						) : null}
						{activeSection === 'assumptions' ? (
							<ProductLoopSection
								section="assumptions"
								data={loop.data}
								architectureDecisions={projectArchitectureDecisions}
								loading={loop.loading}
								error={loop.error}
								actions={productLoopActions}
							/>
						) : null}
						{activeSection === 'decisions' ? (
							<ProductLoopSection
								section="decisions"
								data={loop.data}
								architectureDecisions={projectArchitectureDecisions}
								loading={loop.loading}
								error={loop.error}
								actions={productLoopActions}
							/>
						) : null}
						{activeSection === 'architecture' ? (
							<ProductLoopSection
								section="architecture"
								data={loop.data}
								architectureDecisions={projectArchitectureDecisions}
								loading={loop.loading}
								error={loop.error}
								actions={productLoopActions}
							/>
						) : null}
						{activeSection === 'backlog' ? (
							<ProductLoopSection
								section="backlog"
								data={loop.data}
								architectureDecisions={projectArchitectureDecisions}
								loading={loop.loading}
								error={loop.error}
								actions={productLoopActions}
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
									actions={productLoopActions}
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
								activeProductLoop={activeProductLoop}
								tasks={loop.data?.tasks ?? []}
								decisionBusy={
									loopActionBusy === 'accept_delivery' ||
									loopActionBusy === 'request_delivery_changes' ||
									loopActionBusy === 'continue_delivery'
								}
								onAcceptDelivery={handleAcceptDelivery}
								onRequestChanges={handleRequestDeliveryChanges}
								onContinueDelivery={handleContinueDelivery}
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
				<TeamActivityPanel projectId={selectedProject?.id} open={teamOpen} token={token} />
			</Drawer>
		</div>
	);
}
