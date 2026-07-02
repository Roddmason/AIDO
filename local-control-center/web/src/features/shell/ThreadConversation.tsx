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
 * @author Rodrigo Mason
 */
import {
	AlertTriangle,
	BookOpen,
	CalendarClock,
	Hash,
	History,
	Laptop,
	MessageSquare,
	Send,
	ShieldCheck,
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
	createThread,
	findSimilarThreads,
	getWorkerStatus,
	markSimilarThread,
	postThreadMessage,
	runWorkerOnce,
	type WorkerStatusResponse,
} from '../../api/client';
import type {
	Overview,
	Project,
	RuntimeProviders,
	ThreadAgentEvent,
	ThreadArtifact,
	ThreadDetail,
	ThreadMessage,
	ThreadSimilarityCandidate,
} from '../../api/types';
import type { Mutate } from '../../app/routes';
import { StatusDot } from '../../components/primitives';
import { Button, EmptyState, Skeleton, StatusChip, TextArea } from '../../components/ui';
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
import { useSettings } from '../settings/useSettings';
import { NEW_SESSION_ID } from '../workbench/useWorkbenchData';
import { GitBranchBar } from './GitBranchBar';
import { ThreadExecutionPanel } from './ThreadExecutionPanel';
import {
	arrayValue,
	isRecord,
	MESSAGE_META,
	TRANSCRIPT_KINDS,
	textValue,
} from './threadPresentation';
import { useThreadConversation } from './useThreadConversation';
import { useThreadEventStream } from './useThreadEventStream';

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
type ResearchCardPayload = {
	status?: string;
	reason?: string;
	recommendation?: ResearchRecommendationPayload;
	sources?: ResearchSourcePayload[];
	discrepancies?: Array<Record<string, unknown>>;
};

/** Resolves the workspace owner id for a brand-new thread: the project's runtime workspace if any. */
function ownerIdForProject(overview: Overview, project: Project): string {
	const workspace = overview.runtimeWorkspaces.find((entry) => entry.projectId === project.id);
	return workspace?.id ?? project.id;
}

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
}: ThreadConversationProps) {
	const { t } = useI18n();
	const isNew = !selectedThreadId || selectedThreadId === NEW_SESSION_ID;
	const activeThreadId = isNew ? null : selectedThreadId;
	const { detail, loading, error, busy, reload, send, resolve } = useThreadConversation(
		activeThreadId,
		mutate,
	);
	const [streamRefreshKey, setStreamRefreshKey] = useState(0);
	const [workerStatus, setWorkerStatus] = useState<WorkerStatusResponse | null>(null);
	const [workerBusy, setWorkerBusy] = useState(false);
	const eventStream = useThreadEventStream(activeThreadId, streamRefreshKey);
	const decisionRef = useRef<HTMLElement | null>(null);
	const composerDockRef = useRef<HTMLDivElement | null>(null);

	// Git mutations inside the composer re-pull the overview (fire-and-forget) so the shell stays in sync.
	const refreshOverview = () => {
		void mutate(async () => undefined, { awaitRefresh: false });
	};

	useEffect(() => {
		if (!activeThreadId || eventStream.events.length === 0) return;
		reload();
	}, [activeThreadId, eventStream.events.length, eventStream.threadStatus, reload]);

	const sendMessage = useCallback(
		async (content: string) => {
			await send(content);
			setStreamRefreshKey((value) => value + 1);
		},
		[send],
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

	useEffect(() => {
		const controller = new AbortController();
		loadWorkerStatus(controller.signal);
		return () => controller.abort();
	}, [loadWorkerStatus, activeThreadId, eventStream.threadStatus]);

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

	const resolveDecision = useCallback(
		async (decisionId: string, resolution: string) => {
			await resolve(decisionId, resolution);
			setStreamRefreshKey((value) => value + 1);
		},
		[resolve],
	);

	const focusDecision = useCallback(() => {
		decisionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
		decisionRef.current?.focus();
	}, []);

	const focusGitBar = useCallback(() => {
		composerDockRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
	}, []);

	// One phase id per render, used only to pick the AnimatePresence key/variant below; the
	// if/else-if chain further down re-checks the same conditions directly so TypeScript's
	// narrowing of `selectedProject`/`detail` keeps working exactly as before this refactor.
	const phase = deriveConversationPhase(selectedProject, isNew, loading, error, detail);

	// Remembers that the user is mid-handoff from the new-thread intake, surviving the transient
	// 'loading' tick that always intervenes between 'new' and 'live' (the thread-detail fetch is at
	// least one async tick, so 'loading' reliably shows up in between — confirmed visually, not just
	// in theory). Switching between two already-created threads still passes through 'loading' but
	// never through 'new', so it never sets this flag and gets the plain fade, as intended.
	const awaitingHandoffRef = useRef(false);
	if (phase === 'new') {
		awaitingHandoffRef.current = true;
	}
	// Frozen for the whole 'live' mount via useMemo(deps=[phase]) — a plain derived const would flip
	// before the console's delayed fade-in ever fires (the reset effect below runs almost immediately
	// after the 'live' render commits), retargeting Motion's `animate` prop and defeating the delay.
	const handoffFromIntake = useMemo(() => phase === 'live' && awaitingHandoffRef.current, [phase]);
	useEffect(() => {
		if (phase === 'live' || phase === 'no-project') {
			awaitingHandoffRef.current = false;
		}
	}, [phase]);

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
				mutate={mutate}
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
		const pendingDecision =
			detail.decisions.find((decision) => decision.status === 'pending') ?? null;
		const researchArtifacts = detail.artifacts.filter(
			(artifact) => artifact.kind === 'research_report',
		);
		const transcriptMessages = detail.messages.filter((message) =>
			TRANSCRIPT_KINDS.has(message.kind),
		);
		const executionMessages = detail.messages.filter(
			(message) => !TRANSCRIPT_KINDS.has(message.kind),
		);
		const consoleEvents = mergeConsoleEvents(detail.events, eventStream.events);
		const threadStatus = eventStream.threadStatus ?? detail.thread.status;
		const waitingForWorker =
			threadStatus === 'queued' && !consoleEvents.some((event) => event.type === 'worker_claimed');

		content = (
			<div className="shell-chat-layout thread-live-layout">
				<header className="thread-conversation-head">
					<div className="thread-conversation-title">
						<MessageSquare aria-hidden="true" size={16} />
						<h2>{detail.thread.title}</h2>
					</div>
					<StatusChip tone={threadStatusTone(threadStatus)}>
						{threadStatus.replace(/_/g, ' ')}
					</StatusChip>
				</header>

				<div className="thread-live-body">
					<div className="thread-transcript-pane">
						<section
							className="thread-live-scroll"
							aria-label={t('app.threads.chatRegion', 'Thread chat')}
						>
							<div className="thread-chat-transcript" aria-live="polite">
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

							{pendingDecision ? (
								<m.section
									ref={decisionRef}
									tabIndex={-1}
									className="thread-decision-console"
									aria-label={t('app.threads.decisionTitle', 'Decision needed')}
									variants={panelTransition}
									initial="initial"
									animate="animate"
								>
									<div className="thread-decision-head">
										<AlertTriangle aria-hidden="true" size={15} />
										<strong>{t('app.threads.decisionTitle', 'Decision needed')}</strong>
									</div>
									<p>{pendingDecision.prompt}</p>
									<div className="thread-decision-options">
										{pendingDecision.options.map((option) => (
											<Button
												key={option}
												variant="secondary"
												disabled={busy}
												onClick={() => resolveDecision(pendingDecision.id, option)}
											>
												{option}
											</Button>
										))}
									</div>
								</m.section>
							) : null}

							{researchArtifacts.map((artifact) => (
								<ThreadResearchCard key={artifact.id} artifact={artifact} />
							))}
						</section>

						<div className="thread-composer-dock" ref={composerDockRef}>
							<ThreadComposerBox
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
							/>
						</div>
					</div>

					<ThreadExecutionPanel
						project={selectedProject}
						token={token}
						threadStatus={threadStatus}
						events={consoleEvents}
						messages={executionMessages}
						pendingDecision={pendingDecision}
						workerStatus={workerStatus}
						workerBusy={workerBusy}
						syncing={eventStream.loading}
						streamError={eventStream.error}
						waitingForWorker={waitingForWorker}
						handoffFromIntake={handoffFromIntake}
						onRunQueuedNow={runQueuedJobsNow}
						onOpenRuntimeSetup={onOpenRuntimeSetup}
						onOpenApprovals={onOpenApprovals}
						onFocusDecision={focusDecision}
						onFocusGitBar={focusGitBar}
						onGitRefresh={refreshOverview}
					/>
				</div>
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
function ThreadResearchCard({ artifact }: { artifact: ThreadArtifact }) {
	const { t } = useI18n();
	const payload = researchPayload(artifact.metadata);
	const status = payload.status || 'research_required';
	const sources = payload.sources ?? [];
	const discrepancies = payload.discrepancies ?? [];
	const recommendation = payload.recommendation ?? {};
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
					<strong>{t('app.threads.research.title', 'Research')}</strong>
				</div>
				<StatusChip tone={researchTone(status)}>{status.replace(/_/g, ' ')}</StatusChip>
			</div>

			<div className="thread-research-recommendation">
				<span>{t('app.threads.research.recommendation', 'Recommendation')}</span>
				<strong>{recommendation.title || payload.reason || artifact.title}</strong>
				<p>{recommendation.decision || payload.reason || ''}</p>
			</div>

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

			{discrepancies.length ? (
				<details className="thread-research-disclosure">
					<summary>{t('app.threads.research.discrepancies', 'Discrepancies')}</summary>
					<ul className="thread-research-discrepancies">
						{discrepancies.map((item, index) => (
							<li key={`${String(item.topic ?? 'discrepancy')}-${index}`}>
								<strong>{String(item.topic ?? 'source conflict')}</strong>
								<span>{String(item.conflictingValues ?? '')}</span>
							</li>
						))}
					</ul>
				</details>
			) : null}
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
}: ThreadComposerBoxProps) {
	const { t } = useI18n();
	const [value, setValue] = useState(initialValue);
	const [localBusy, setLocalBusy] = useState(false);
	const { project: projectSettings, setValue: setSetting } = useSettings(project.id, true);
	const [permBusy, setPermBusy] = useState(false);

	const autonomy = projectSettings.find((setting) => setting.key === 'autonomy.level');
	const autonomyValue = typeof autonomy?.value === 'string' ? autonomy.value : 'guided';
	const working = busy || localBusy;
	// Same executable definition the status bar counts (shellStatus), so both surfaces agree.
	const executableRuntimes =
		runtimeProviders?.providers.filter((provider) => provider.executable).length ?? 0;
	// With zero executable runtimes nothing runs regardless of autonomy, so the "full access" warn on
	// the permissions shield is moot; suppressing it leaves the runtime CTA as the composer's only warn.
	// Gated on a settled snapshot (runtimeProviders !== null) so a pending discovery never mutes it.
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
			if (onSendMessage) {
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

	// Enter sends; Shift+Enter inserts a newline (the conventional chat-composer affordance).
	const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
		if (event.key === 'Enter' && !event.shiftKey) {
			event.preventDefault();
			void submitNow();
		}
	};

	return (
		<form className="thread-composer-box" onSubmit={submit}>
			<TextArea
				label={t('app.threads.composerLabel', 'Message AIDO')}
				value={value}
				rows={rows}
				placeholder={t('app.threads.composerPlaceholder', 'Describe the work or ask a question…')}
				onChange={(event) => {
					setValue(event.target.value);
					onValueChange?.(event.target.value);
				}}
				onKeyDown={onKeyDown}
				error={errorText}
			/>
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
			<div className="shell-chat-context">
				<span className="shell-chat-context-project">{project.name}</span>
				<span
					className="composer-env"
					title={t('app.composer.localHint', 'AIDO runs on this machine')}
				>
					<Laptop aria-hidden="true" size={13} />
					{t('app.composer.local', 'Local')}
				</span>
				{/* Runtime readiness at the point of composing: with zero executable runtimes the thread
				    will block on execution, so the chip becomes the setup CTA instead of a passive count.
				    Hidden while discovery is pending — no state is asserted before the API answered.
				    Direct child of the context row (no wrapper) so its top border stays on the shared
				    toolbar line; the underlined label is the persistent "this is a link" affordance. */}
				{runtimeProviders ? (
					<button
						type="button"
						className="composer-env composer-runtimes"
						data-tone={executableRuntimes === 0 ? 'warn' : undefined}
						onClick={onOpenRuntimeSetup}
					>
						<StatusDot tone={executableRuntimes > 0 ? 'ok' : 'warn'} />
						{/* Polite live region: once the button is mounted its label persists, so a later
						    warn→ok flip (or a changed count) is announced without re-announcing on focus. */}
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
				<GitBranchBar selectedProject={project} token={token} onRefresh={onGitRefresh} />
			</div>
		</form>
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
	mutate,
	token,
	runtimeProviders,
	onGitRefresh,
	onOpenRuntimeSetup,
	onCreated,
}: {
	overview: Overview;
	project: Project;
	mutate: Mutate;
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

	// Debounced similar-work lookup. Best-effort by design: failures stay silent and never block
	// the composer; the AbortController drops stale in-flight responses when the draft changes.
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
		// Title is derived automatically from the first line of the message (no manual field); the
		// coordinator can refine it from the conversation topic later.
		const firstLine = firstMessage.split(/\r?\n/)[0]?.trim() ?? '';
		const threadTitle = (firstLine || firstMessage).slice(0, 80);
		// Frozen here: the debounce may clear the card while the async creation is in flight.
		const dismissedCandidate = candidate;
		setFailed(false);
		try {
			const created = await mutate(
				(mutateToken) =>
					createThread(mutateToken, {
						projectId: project.id,
						ownerType: 'workspace',
						ownerId: ownerIdForProject(overview, project),
						title: threadTitle,
					}),
				{ awaitRefresh: false },
			);
			onCreated(created.thread.id);
			await mutate(
				(mutateToken) =>
					postThreadMessage(mutateToken, created.thread.id, {
						content: firstMessage,
						...(dismissedCandidate
							? {
									metadata: {
										mode: 'create_new_anyway',
										similarThreadId: dismissedCandidate.threadId,
									},
								}
							: {}),
					}),
				{ awaitRefresh: false },
			);
			if (dismissedCandidate) {
				// Persist the deduplication decision ("saw similar work, created new anyway") as a real
				// similarity event; the thread itself is already created, so a failure here stays silent.
				try {
					await mutate(
						(mutateToken) =>
							markSimilarThread(mutateToken, created.thread.id, dismissedCandidate.threadId, {
								action: 'create_new_anyway',
								score: dismissedCandidate.score,
								reason: dismissedCandidate.reason,
							}),
						{ awaitRefresh: false },
					);
				} catch {
					// Best-effort audit trail; the created thread must not look broken because of it.
				}
			}
		} catch (creationError) {
			setFailed(true);
			throw creationError;
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
			await mutate(
				(mutateToken) => postThreadMessage(mutateToken, target, { content, metadata: { mode } }),
				{ awaitRefresh: false },
			);
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
			// createThreadFromMessage already surfaced the failure through the composer errorText.
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
