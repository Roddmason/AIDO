/**
 * Center surface of the threads shell: a real conversation backed by `project_threads`.
 *
 * Renders the selected thread's timeline (messages typed by kind: user, aido_lead, agent_summary,
 * decision_request, artifact, error, system_event), surfaces pending decisions as actionable options,
 * and hosts the Codex-style composer that posts a user message and runs the coordinator (responds or
 * blocks). The composer is one rounded box: the prompt on top, then the controls row (action-approval
 * level + send) and the workspace context row (project, local runtime, git branch/scan). When the
 * "New thread" sentinel is active the same box creates a real thread and sends its first message; the
 * title is derived automatically from the first line. Real data only — settings, git and threads all
 * come from their APIs.
 * @author Rodrigo Mason
 */
import {
	AlertTriangle,
	BookOpen,
	CalendarClock,
	Hash,
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
import { createThread, postThreadMessage } from '../../api/client';
import type {
	Overview,
	Project,
	ThreadAgentEvent,
	ThreadArtifact,
	ThreadDetail,
	ThreadMessage,
} from '../../api/types';
import type { Mutate } from '../../app/routes';
import { Button, EmptyState, Skeleton, StatusChip, TextArea } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
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
import { useThreadConversation } from './useThreadConversation';
import { useThreadEventStream } from './useThreadEventStream';

type ThreadConversationProps = {
	overview: Overview;
	selectedProject: Project | null;
	selectedThreadId: string;
	mutate: Mutate;
	token: string;
	onSelectThread: (threadId: string) => void;
	onCreateProject: () => void;
};

type MessageMeta = { authorKey: string; tone: 'ok' | 'warn' | 'danger' | 'info' | 'pending' };
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

const MESSAGE_META: Record<ThreadMessage['kind'], MessageMeta> = {
	user: { authorKey: 'app.threads.authorUser', tone: 'info' },
	aido_lead: { authorKey: 'app.threads.authorLead', tone: 'ok' },
	agent_summary: { authorKey: 'app.threads.authorAgent', tone: 'info' },
	decision_request: { authorKey: 'app.threads.decisionTitle', tone: 'warn' },
	artifact: { authorKey: 'app.threads.authorArtifact', tone: 'info' },
	error: { authorKey: 'app.threads.authorError', tone: 'danger' },
	system_event: { authorKey: 'app.threads.authorSystem', tone: 'pending' },
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
	onSelectThread,
	onCreateProject,
}: ThreadConversationProps) {
	const { t } = useI18n();
	const isNew = !selectedThreadId || selectedThreadId === NEW_SESSION_ID;
	const activeThreadId = isNew ? null : selectedThreadId;
	const { detail, loading, error, busy, reload, send, resolve } = useThreadConversation(
		activeThreadId,
		mutate,
	);
	const [streamRefreshKey, setStreamRefreshKey] = useState(0);
	const eventStream = useThreadEventStream(activeThreadId, streamRefreshKey);

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

	const resolveDecision = useCallback(
		async (decisionId: string, resolution: string) => {
			await resolve(decisionId, resolution);
			setStreamRefreshKey((value) => value + 1);
		},
		[resolve],
	);

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
				onGitRefresh={refreshOverview}
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
		const pendingDecision = detail.decisions.find((decision) => decision.status === 'pending');
		const researchArtifacts = detail.artifacts.filter(
			(artifact) => artifact.kind === 'research_report',
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
					<StatusChip tone={toneForStatus(threadStatus)}>
						{threadStatus.replace(/_/g, ' ')}
					</StatusChip>
				</header>

				<div className="thread-live-scroll" aria-label={t('app.threads.chatRegion', 'Thread chat')}>
					<div className="thread-chat-transcript" aria-live="polite">
						{detail.messages.length === 0 ? (
							<p className="thread-chat-empty">
								{t('app.threads.transcriptEmpty', 'No messages yet. Send the first one below.')}
							</p>
						) : (
							detail.messages.map((message) => (
								<ThreadMessageRow key={message.id} message={message} />
							))
						)}
					</div>

					{pendingDecision ? (
						<m.section
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

					<m.section
						className="thread-execution-console"
						role="log"
						aria-live="polite"
						aria-relevant="additions"
						aria-label={t('app.threads.consoleRegion', 'Execution console')}
						initial={{ opacity: 0, y: 6 }}
						animate={{
							opacity: 1,
							y: 0,
							transition: { duration: 0.2, ease: EASE_OUT, delay: handoffFromIntake ? 0.55 : 0 },
						}}
					>
						<div className="thread-console-title">
							<span>{t('app.threads.consoleTitle', 'Execution')}</span>
							{eventStream.loading ? (
								<StatusChip tone="pending">{t('app.threads.consoleLoading', 'syncing')}</StatusChip>
							) : null}
						</div>
						{eventStream.error ? (
							<div className="thread-console-status" data-tone="danger">
								{eventStream.error}
							</div>
						) : null}
						{waitingForWorker ? (
							<div className="thread-console-status" data-tone="pending">
								{t('app.threads.waitingWorker', 'Queued: waiting for a worker to claim this run.')}
							</div>
						) : null}
						{consoleEvents.length ? (
							consoleEvents.map((event) => <ThreadConsoleRow key={event.id} event={event} />)
						) : (
							<div className="thread-console-status" data-tone="muted">
								{t('app.threads.consoleEmpty', 'No execution events yet.')}
							</div>
						)}
						{researchArtifacts.map((artifact) => (
							<ThreadResearchCard key={artifact.id} artifact={artifact} />
						))}
					</m.section>
				</div>

				<div className="thread-composer-dock">
					<ThreadComposerBox
						project={selectedProject}
						token={token}
						onGitRefresh={refreshOverview}
						value=""
						busy={busy}
						rows={3}
						submitLabel={t('app.threads.send', 'Send')}
						submitBusyLabel={t('app.threads.sending', 'Sending…')}
						onSendMessage={sendMessage}
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

function isRecord(value: unknown): value is Record<string, unknown> {
	return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function arrayValue(value: unknown): unknown[] {
	return Array.isArray(value) ? value : [];
}

function textValue(value: unknown): string | undefined {
	return typeof value === 'string' && value.trim() ? value : undefined;
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

function ThreadConsoleRow({ event }: { event: ThreadAgentEvent }) {
	const { t } = useI18n();
	const payload = safeRecord(event.payload);
	const actor =
		event.agentRole || textValue(payload.role) || textValue(payload.agentName) || 'aido';
	const title = t(`app.threads.event.${event.type}`, eventTitle(event.type));
	const detail = eventDetail(event, payload);
	const chips = [
		textValue(payload.status) || textValue(payload.toState),
		textValue(payload.runtimeId),
		textValue(payload.agentId),
		textValue(payload.workerId),
		textValue(payload.jobId),
	].filter((value): value is string => Boolean(value));
	return (
		<div className="thread-console-row" data-type={event.type}>
			<span className="thread-console-seq mono">#{event.sequence}</span>
			<span className="thread-console-actor">{actor}</span>
			<div className="thread-console-body">
				<div className="thread-console-line">
					<strong>{title}</strong>
					{chips.map((chip) => (
						<span className="thread-console-chip mono" key={chip}>
							{chip}
						</span>
					))}
				</div>
				{detail ? <p>{detail}</p> : null}
			</div>
		</div>
	);
}

function eventTitle(type: string): string {
	const titles: Record<string, string> = {
		message_received: 'Message received',
		classification_completed: 'Classification ready',
		team_planned: 'Team planned',
		run_queued: 'Run queued',
		worker_claimed: 'Worker assigned',
		runtime_selected: 'Runtime selected',
		agent_running: 'Agent working',
		workspace_check: 'Workspace',
		runtime_check: 'Runtime',
		git_check: 'Git',
		discovery: 'Discovery',
		planning: 'Planning',
		backlog_ready: 'Backlog ready',
		branch_ready: 'Branch ready',
		executing: 'Execution',
		qa_running: 'QA',
		security_running: 'Security',
		review_ready: 'Review',
		awaiting_approval: 'Approval required',
		approval_required: 'Approval required',
		blocked: 'Blocked',
		completed: 'Completed',
		decision_required: 'Decision required',
		decision_resolved: 'Decision resolved',
	};
	return titles[type] ?? type.replace(/_/g, ' ');
}

function eventDetail(event: ThreadAgentEvent, payload: Record<string, unknown>): string {
	const reason = textValue(payload.reason);
	if (reason) return reason;
	const roles = safeRecord(payload.summary).roles;
	if (Array.isArray(roles) && roles.length) {
		return `Roles: ${roles.map(String).join(', ')}`;
	}
	const fromState = textValue(payload.fromState);
	const toState = textValue(payload.toState);
	if (fromState && toState) return `${fromState} -> ${toState}`;
	const assignmentId = textValue(payload.assignmentId);
	if (assignmentId) return `Asignacion: ${assignmentId}`;
	return event.createdAt;
}

function safeRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: {};
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
	onGitRefresh: () => void;
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
};

/**
 * The Codex-style composer box shared by the new-thread intake and the active conversation: a single
 * rounded surface with the prompt, the action-approval level (wired to the real `autonomy.level`
 * setting), Send, and the workspace context row (project, local runtime, git branch/scan).
 */
function ThreadComposerBox({
	project,
	token,
	onGitRefresh,
	value: initialValue,
	busy,
	rows,
	submitLabel,
	submitBusyLabel,
	errorText,
	onSendMessage,
	onCreateThread,
}: ThreadComposerBoxProps) {
	const { t } = useI18n();
	const [value, setValue] = useState(initialValue);
	const [localBusy, setLocalBusy] = useState(false);
	const { project: projectSettings, setValue: setSetting } = useSettings(project.id, true);
	const [permBusy, setPermBusy] = useState(false);

	const autonomy = projectSettings.find((setting) => setting.key === 'autonomy.level');
	const autonomyValue = typeof autonomy?.value === 'string' ? autonomy.value : 'guided';
	const working = busy || localBusy;

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
				onChange={(event) => setValue(event.target.value)}
				onKeyDown={onKeyDown}
				error={errorText}
			/>
			<div className="shell-chat-options">
				<span className="composer-perms" data-level={autonomyValue}>
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
				<GitBranchBar selectedProject={project} token={token} onRefresh={onGitRefresh} />
			</div>
		</form>
	);
}

/** New-thread intake: the Codex-style composer box under a heading. The title is auto-derived from
 *  the first line of the message (no manual field), the way modern AI IDEs name conversations. */
function NewThreadComposer({
	overview,
	project,
	mutate,
	token,
	onGitRefresh,
	onCreated,
}: {
	overview: Overview;
	project: Project;
	mutate: Mutate;
	token: string;
	onGitRefresh: () => void;
	onCreated: (threadId: string) => void;
}) {
	const { t } = useI18n();
	const [failed, setFailed] = useState(false);

	const createThreadFromMessage = async (firstMessage: string) => {
		// Title is derived automatically from the first line of the message (no manual field); the
		// coordinator can refine it from the conversation topic later.
		const firstLine = firstMessage.split(/\r?\n/)[0]?.trim() ?? '';
		const threadTitle = (firstLine || firstMessage).slice(0, 80);
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
					postThreadMessage(mutateToken, created.thread.id, { content: firstMessage }),
				{ awaitRefresh: false },
			);
		} catch (creationError) {
			setFailed(true);
			throw creationError;
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
						onGitRefresh={onGitRefresh}
						value=""
						busy={false}
						rows={4}
						submitLabel={t('app.threads.create', 'Create thread')}
						submitBusyLabel={t('app.threads.creating', 'Creating…')}
						errorText={
							failed
								? t('app.threads.errorSend', 'Could not send the message. Try again.')
								: undefined
						}
						onCreateThread={createThreadFromMessage}
					/>
				</m.div>
			</m.div>
		</div>
	);
}
