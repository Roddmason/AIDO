/**
 * Workbench IDE shell: a single composer drives the product loop (conversation → questions →
 * brief → assumptions → decisions → architecture → backlog → iteration → execution → review),
 * laid out with the explorer and inspector. Owns the page-level UI state (prompt, active loop
 * section, selected session/run, task mode) and wires intake (session + chat + pipeline) to the
 * API; the derived data comes from useWorkbenchData. Sections wired to live overview data render
 * real content; the discovery/backlog sections render an honest shell until their endpoint exists.
 */
import {
	Bot,
	CheckCircle2,
	ClipboardCheck,
	Code2,
	FileCheck2,
	FolderKanban,
	GitBranch,
	Rocket,
	Users,
	Workflow,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type {
	ChatCreateResponse,
	IssueToPatchResponse,
	PipelineCreateResponse,
	SessionCreateResponse,
} from '../../api/client';
import {
	createChat,
	createPipeline,
	createSession,
	runIssueToPatch,
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
import { Badge, Drawer, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { Button, SegmentedControl, TextArea, TextField, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { shortId, toneForStatus } from '../../lib/format';
import type { ComposerDraft, ComposerMode } from './composerDraft';
import { clearComposerDraft, persistComposerDraft, readComposerDraft } from './composerDraft';
import {
	autoQaPresetId,
	type GovernedAdvanced,
	GovernedAdvancedPanel,
	issueQaPresets,
	type RuntimeRow,
} from './GovernedAdvancedPanel';
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
import {
	objectRecord,
	runtimeIsExecutableIssueRuntime,
	runtimeSupportsIssueToPatch,
} from './workbenchSelectors';

type Mutate = <T>(
	operation: (token: string) => Promise<T>,
	options?: { awaitRefresh?: boolean },
) => Promise<T>;

/** The unified intake axis: the simple conversation flow plus the four governed change types. */
const COMPOSER_MODES: { id: ComposerMode; labelKey: string; label: string }[] = [
	{ id: 'conversation', labelKey: 'app.workbench.task.modeConversation', label: 'Conversation' },
	{ id: 'fix', labelKey: 'app.workbench.task.modeFix', label: 'Fix bug' },
	{ id: 'feature', labelKey: 'app.workbench.task.modeFeature', label: 'Add feature' },
	{ id: 'refactor', labelKey: 'app.workbench.task.modeRefactor', label: 'Refactor' },
	{ id: 'tests', labelKey: 'app.workbench.task.modeTests', label: 'Write tests' },
];

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
};

function firstLine(value: string) {
	return value.trim().split(/\r?\n/)[0]?.slice(0, 96) || 'Workbench intake';
}

/** Title auto-derived from the prompt; '' when the prompt is empty so the field stays empty.
 *  Governed modes keep the legacy `"<mode>: <first line>"` shape (140-char head) so the
 *  request body is byte-identical when the user has not edited the title. */
function deriveTitle(prompt: string, mode: ComposerMode, modeLabel: string): string {
	const head = prompt.trim().split(/\r?\n/)[0] ?? '';
	if (!head) return '';
	if (mode === 'conversation') return head.slice(0, 96);
	return `${modeLabel}: ${head.slice(0, 140)}`.slice(0, 180);
}

/** Default governed-advanced values for a fresh project (QA preset auto-picked from the stack). */
function defaultAdvanced(project: Project | null): GovernedAdvanced {
	return {
		targetPath: '',
		runChecks: true,
		requireReview: true,
		preferredRuntime: '',
		qaPreset: autoQaPresetId(project),
		maxCostUsd: '',
	};
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
}: WorkbenchPageProps) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [prompt, setPrompt] = useState('');
	const [title, setTitle] = useState('');
	const [titleEdited, setTitleEdited] = useState(false);
	const [composerMode, setComposerMode] = useState<ComposerMode>('conversation');
	const [advanced, setAdvanced] = useState<GovernedAdvanced>(() =>
		defaultAdvanced(selectedProject),
	);
	const [selectedSessionId, setSelectedSessionId] = useState('');
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');
	const [created, setCreated] = useState<{
		sessionId: string;
		chatId: string;
		pipelineId: string;
	} | null>(null);
	const [activeSection, setActiveSection] = useState<ProductLoopSectionId>('conversation');
	const [taskRunResult, setTaskRunResult] = useState<IssueToPatchResponse | null>(null);
	const [isSubmittingTask, setIsSubmittingTask] = useState(false);
	const [selectedRunId, setSelectedRunId] = useState('');
	const [teamOpen, setTeamOpen] = useState(false);
	const [showAllChats, setShowAllChats] = useState(false);
	const [loopStarting, setLoopStarting] = useState(false);
	const [discoveryBusy, setDiscoveryBusy] = useState(false);

	// Draft-persistence refs: latest values for the synchronous unmount flush, the project the
	// composer is currently hydrated for (so the debounced save never clobbers with pre-hydration
	// state), and the in-flight conversation abort controller.
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
		reviewChangedFiles,
		latestEvidence,
		latestRunStatus,
		blockers,
		teamRows,
		deliveryTimeline,
		runTimeline,
		hasRun,
		hasPipeline,
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

	// Live product-loop data for the loop sections; re-fetched on project change, on demand
	// (decoupled from the 5s overview poll since the loop is detail-shaped and changes rarely).
	const loop = useProductLoop(project?.id);

	// --- Runtime / QA derivation (single source on the page; lifted from the old TaskComposer). ---
	const runtimeRows = runtimeProviders?.providers ?? [];
	const executableRuntimes = useMemo(
		() => runtimeRows.filter(runtimeIsExecutableIssueRuntime),
		[runtimeRows],
	);
	const selectedRuntime: RuntimeRow | null =
		executableRuntimes.find((item) => item.id === advanced.preferredRuntime) ?? null;
	const selectedQaPreset =
		issueQaPresets.find((item) => item.id === advanced.qaPreset) ?? issueQaPresets[0];
	const hasExecutableRuntime = executableRuntimes.length > 0;
	const unavailableIssueRuntime = runtimeRows.find(
		(runtime) => runtimeSupportsIssueToPatch(runtime) && runtime.executable !== true,
	);
	const runtimeBlockerReason = runtimeProviders
		? (unavailableIssueRuntime?.reason ??
			t(
				'app.workbench.task.runtimeNoExecutable',
				'No executable issue_to_patch/code_edit runtime is configured.',
			))
		: t('app.workbench.task.runtimeDiscovery', 'Runtime provider discovery has not completed.');

	// --- Title: auto-derived from the prompt until the user edits it, then their value wins. ---
	const activeMode = COMPOSER_MODES.find((item) => item.id === composerMode) ?? COMPOSER_MODES[0];
	const activeModeLabel = t(activeMode.labelKey, activeMode.label);
	const derivedTitle = deriveTitle(prompt, composerMode, activeModeLabel);
	const effectiveTitle = titleEdited ? title : derivedTitle;
	// Unedited path stays byte-identical to the legacy derived title (no extra trim); only a
	// user-typed title is trimmed. Both fall back to firstLine so the sent title is never empty.
	const submitTitle = titleEdited
		? title.trim() || firstLine(prompt)
		: derivedTitle || firstLine(prompt);

	const trimmedPrompt = prompt.trim();
	const isGoverned = composerMode !== 'conversation';
	const composerDisabled = busy || !project || !trimmedPrompt || (isGoverned && !selectedRuntime);
	// "AIDO decide" runs the Product Owner agent over the typed idea, so it needs a project and a prompt.
	const aidoDecideDisabled = busy || discoveryBusy || !project || !trimmedPrompt;
	const effectiveQaCommands = advanced.runChecks ? selectedQaPreset.commands : [];

	const updateAdvanced = (patch: Partial<GovernedAdvanced>) =>
		setAdvanced((prev) => ({ ...prev, ...patch }));

	// Keep one executable runtime selected; preserve a still-valid restored/manual override.
	useEffect(() => {
		setAdvanced((prev) => {
			if (!executableRuntimes.length) {
				return prev.preferredRuntime ? { ...prev, preferredRuntime: '' } : prev;
			}
			if (executableRuntimes.some((runtime) => runtime.id === prev.preferredRuntime)) return prev;
			const next = (executableRuntimes.find((runtime) => runtime.detected) ?? executableRuntimes[0])
				.id;
			return { ...prev, preferredRuntime: next };
		});
	}, [executableRuntimes]);

	// Restore the per-project draft on project switch (or reset to defaults). This is the ONLY
	// place that resets composer text — never on session or mode switch — so neither loses it.
	// biome-ignore lint/correctness/useExhaustiveDependencies: re-run only when the project id changes.
	useEffect(() => {
		const projectId = project?.id ?? '';
		const draft = projectId ? readComposerDraft(projectId) : null;
		if (draft) {
			setPrompt(draft.prompt);
			setTitle(draft.title);
			setTitleEdited(draft.titleEdited);
			setComposerMode(draft.mode);
			setAdvanced(draft.advanced);
		} else {
			setPrompt('');
			setTitle('');
			setTitleEdited(false);
			setComposerMode('conversation');
			setAdvanced(defaultAdvanced(project));
		}
	}, [project?.id]);

	// Mirror the live draft into a ref so the unmount flush can persist the latest keystrokes.
	latestDraftRef.current = project
		? { projectId: project.id, draft: { prompt, title, titleEdited, mode: composerMode, advanced } }
		: null;

	// Debounced steady-state save; guarded so it never writes before the project is hydrated.
	useEffect(() => {
		const projectId = project?.id ?? '';
		if (!projectId || hydratedProjectIdRef.current !== projectId) return;
		const handle = window.setTimeout(() => {
			persistComposerDraft(projectId, { prompt, title, titleEdited, mode: composerMode, advanced });
		}, 400);
		return () => window.clearTimeout(handle);
	}, [project?.id, prompt, title, titleEdited, composerMode, advanced]);

	// Advance the hydration marker only AFTER the save effect's commit-pass run, so a save
	// scheduled during a project switch (still holding the previous project's body) can never
	// pass the guard and write stale state under the new project id.
	useEffect(() => {
		hydratedProjectIdRef.current = project?.id ?? '';
	}, [project?.id]);

	// Synchronous flush on unmount so a fast route-away never drops the last keystrokes.
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
			const result = await mutate(async (token) => {
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
			});
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

	const submitGoverned = async (activeProject: Project, text: string) => {
		if (!selectedRuntime) {
			setError(runtimeBlockerReason);
			return;
		}
		const parsedMaxCost = advanced.maxCostUsd.trim() ? Number(advanced.maxCostUsd) : undefined;
		if (parsedMaxCost !== undefined && (!Number.isFinite(parsedMaxCost) || parsedMaxCost < 0)) {
			setError(
				t('app.workbench.task.errorCost', 'Maximum cost must be zero or a positive number.'),
			);
			return;
		}
		setBusy(true);
		setIsSubmittingTask(true);
		setError('');
		setTaskRunResult(null);
		try {
			const response = await mutate((token) =>
				runIssueToPatch(token, {
					projectId: activeProject.id,
					title: submitTitle,
					issueText: text,
					targetPath: advanced.targetPath.trim() || undefined,
					preferredRuntime: selectedRuntime.id,
					qaCommands: effectiveQaCommands.length ? effectiveQaCommands : undefined,
					maxCostUsd: parsedMaxCost,
					requireApproval: advanced.requireReview,
				}),
			);
			setTaskRunResult(response);
		} catch (submitError) {
			setError(
				submitError instanceof Error
					? submitError.message
					: t('app.workbench.task.errorRun', 'issue_to_patch failed.'),
			);
		} finally {
			setBusy(false);
			setIsSubmittingTask(false);
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
		if (isGoverned) {
			void submitGoverned(project, text);
			return;
		}
		const controller = new AbortController();
		abortRef.current = controller;
		void submitConversation(project, text, controller.signal).finally(() => {
			if (abortRef.current === controller) abortRef.current = null;
		});
	};

	const cancelConversation = () => abortRef.current?.abort();

	// Starts a durable product loop for the project (the loop-creation seam) and refreshes the view.
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

	// Navigates to the section and, when a loop exists, attempts the FSM transition. The backend is
	// authoritative: a step that is not allowed from the current state returns 422 and we just inform.
	const advanceLoop = async (toState: string, section: ProductLoopSectionId) => {
		setActiveSection(section);
		if (!project) return;
		const activeLoopId = loop.data?.loops[0]?.id;
		if (!activeLoopId) {
			notify({
				title: t('app.workbench.loop.startFirst', 'Start the product loop first'),
				tone: 'info',
			});
			return;
		}
		try {
			await transitionProductLoop(token, project.id, activeLoopId, { toState });
			loop.refresh();
			notify({ title: t('app.workbench.loop.advanced', 'Loop advanced'), tone: 'ok' });
		} catch (advanceError) {
			// The backend FSM is authoritative; surface its real reason (an invalid transition, or a
			// 404/5xx) instead of disguising every failure as a single benign "not available" outcome.
			notify({
				title: t('app.workbench.loop.advanceFailed', 'Could not advance the loop'),
				body: advanceError instanceof Error ? advanceError.message : undefined,
				tone: 'warn',
			});
		}
	};

	// "AIDO decide": runs the Product Owner agent over the typed idea. The agent produces and persists
	// the brief, clarification questions, assumptions, decisions and backlog; we then refresh the loop
	// sections. Fail-closed: with no executable product-owner runtime it persists nothing and reports it.
	const runDiscovery = async () => {
		if (!project || discoveryBusy) return;
		const idea = prompt.trim();
		if (!idea) {
			setError(t('app.workbench.error.noPrompt', 'Write a prompt before starting intake.'));
			return;
		}
		const workspaceId = projectWorkspaces[0]?.id;
		if (!workspaceId) {
			notify({
				title: t('app.workbench.loop.noWorkspace', 'No workspace available for discovery'),
				body: t(
					'app.workbench.loop.noWorkspaceBody',
					'Run a governed task first to allocate a workspace for this project.',
				),
				tone: 'warn',
			});
			return;
		}
		setDiscoveryBusy(true);
		setError('');
		try {
			const result = await runProductOwnerAgent(token, {
				projectId: project.id,
				workspaceId,
				idea,
			});
			loop.refresh();
			const reason = typeof result.reason === 'string' ? result.reason : undefined;
			if (String(result.status ?? '') === 'completed') {
				notify({
					title: t('app.workbench.loop.discoveryDone', 'Product discovery completed'),
					tone: 'ok',
				});
			} else {
				notify({
					title: t('app.workbench.loop.discoveryIncomplete', 'Product discovery did not complete'),
					body: reason,
					tone: 'warn',
				});
			}
			setActiveSection('questions');
		} catch (discoveryError) {
			notify({
				title: t('app.workbench.loop.discoveryIncomplete', 'Product discovery did not complete'),
				body: discoveryError instanceof Error ? discoveryError.message : undefined,
				tone: 'danger',
			});
		} finally {
			setDiscoveryBusy(false);
		}
	};

	// Governed run-result detail (moved from the old TaskComposer; rendered under the composer).
	const resultRuntime = objectRecord(taskRunResult?.runtime);
	const resultQa = Array.isArray(taskRunResult?.qaResults)
		? objectRecord(taskRunResult?.qaResults[0])
		: undefined;
	const resultDiff = objectRecord(taskRunResult?.diffSummary);
	const resultEvidence = objectRecord(taskRunResult?.evidencePackage);
	const changedFiles = Array.isArray(resultDiff?.changedFiles)
		? resultDiff.changedFiles.length
		: Number(resultDiff?.changedFiles ?? 0);
	const resultStatus = String(taskRunResult?.status ?? '');
	const executionMode =
		resultStatus === 'runtime_unavailable' ||
		resultStatus === 'unavailable' ||
		resultRuntime?.executable === false
			? 'runtime_unavailable'
			: 'productive_runtime';

	const onSelectRun = (runId: string) => {
		setSelectedRunId(runId);
		setActiveSection('iteration');
	};

	// Tab badges count only what is wired to live overview data today; the discovery and backlog
	// sections stay at 0 (an honest empty shell) until their HTTP endpoints exist.
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

	// The architecture section reuses the already-available overview architecture decisions,
	// scoped to this project; the other loop sections come from the product-loop endpoint.
	const projectArchitectureDecisions = overview.architectureDecisions.filter(
		(decision) => decision.projectId === project.id,
	);

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

					{/* Single composer: always visible, drives every loop action from one place. */}
					<Surface flat>
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
											{t('app.workbench.review.changedFiles', 'changed files')} {reviewChangedFiles}
										</span>
									) : null}
									<button
										className="button"
										type="button"
										onClick={() => setActiveSection('execution')}
									>
										<Code2 aria-hidden="true" size={15} />{' '}
										{t('app.workbench.review.diff', 'Review diff')}
									</button>
									<button
										className="button"
										type="button"
										onClick={() => setActiveSection('review')}
									>
										<FileCheck2 aria-hidden="true" size={15} />{' '}
										{t('app.workbench.review.evidence', 'Review evidence')}
									</button>
								</div>
							) : null}

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
							<SegmentedControl
								label={t('app.workbench.task.modeLabel', 'Task intake mode')}
								value={composerMode}
								onChange={setComposerMode}
								disabled={!project || busy}
								options={COMPOSER_MODES.map((item) => ({
									value: item.id,
									label: t(item.labelKey, item.label),
								}))}
							/>

							{isGoverned ? (
								<GovernedAdvancedPanel
									project={project}
									busy={busy}
									advanced={advanced}
									onAdvancedChange={updateAdvanced}
									executableRuntimes={executableRuntimes}
									selectedRuntime={selectedRuntime}
									selectedQaPreset={selectedQaPreset}
									hasExecutableRuntime={hasExecutableRuntime}
									runtimeBlockerReason={runtimeBlockerReason}
									onConfigureRuntime={onOpenRuntimeSetup}
								/>
							) : null}

							<div className="inline">
								<Button
									variant="primary"
									icon={<Rocket aria-hidden="true" size={16} />}
									disabled={composerDisabled}
									onClick={submitComposer}
								>
									{busy
										? isGoverned
											? t('app.workbench.task.submitting', 'Requesting change')
											: t('app.workbench.chat.creating', 'Creating work session')
										: t('app.workbench.loop.respond', 'Respond')}
								</Button>
								<Button
									icon={<Bot aria-hidden="true" size={16} />}
									loading={discoveryBusy}
									disabled={aidoDecideDisabled}
									onClick={runDiscovery}
								>
									{t('app.workbench.loop.aidoDecide', 'AIDO decides')}
								</Button>
								<Button
									icon={<FileCheck2 aria-hidden="true" size={16} />}
									disabled={busy}
									onClick={() => setActiveSection('brief')}
								>
									{t('app.workbench.loop.reviewBrief', 'Review brief')}
								</Button>
								<Button
									icon={<ClipboardCheck aria-hidden="true" size={16} />}
									disabled={busy}
									onClick={() => advanceLoop('iteration_planning', 'backlog')}
								>
									{t('app.workbench.loop.approveBacklog', 'Approve backlog')}
								</Button>
								<Button
									icon={<Workflow aria-hidden="true" size={16} />}
									disabled={busy}
									onClick={() => advanceLoop('executing', 'iteration')}
								>
									{t('app.workbench.loop.startIteration', 'Start iteration')}
								</Button>
								{!isGoverned && busy ? (
									<Button onClick={cancelConversation}>
										{t('app.workbench.composer.cancel', 'Cancel intake')}
									</Button>
								) : null}
								{isGoverned ? (
									selectedRuntime ? (
										<Badge tone="ok">{selectedRuntime.displayName}</Badge>
									) : (
										<Badge tone="danger">runtime_unavailable</Badge>
									)
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

							{taskRunResult ? (
								<div className="stack" aria-live="polite">
									<div className="inline">
										<Badge tone={toneForStatus(resultStatus)}>{resultStatus || 'no_status'}</Badge>
										<Badge>{String(resultRuntime?.id ?? 'no_runtime')}</Badge>
										<Badge tone={toneForStatus(executionMode)}>{executionMode}</Badge>
										<Badge
											tone={
												resultQa
													? toneForStatus(String(resultQa?.status ?? resultQa?.verdict ?? ''))
													: 'warn'
											}
										>
											{String(resultQa?.status ?? resultQa?.verdict ?? 'qa_not_run')}
										</Badge>
									</div>
									<div className="mono">
										{String(
											taskRunResult.reason ??
												resultRuntime?.reason ??
												t('app.workbench.task.noReason', 'No runtime reason recorded.'),
										)}
									</div>
									<div className="mono">
										{t('app.workbench.task.evidenceLine', 'Evidence')}{' '}
										{String(resultEvidence?.id ?? 'not_created')} /{' '}
										{t('app.workbench.task.changedFiles', 'changed files')}{' '}
										<span className="tnum">{Number.isFinite(changedFiles) ? changedFiles : 0}</span>
									</div>
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
									'Submit a governed task to track its run here.',
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
