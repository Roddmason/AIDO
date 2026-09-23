/**
 * Right-hand execution panel of the threads shell: everything AIDO is *doing*, kept apart from the
 * conversation transcript. Surfaces the worker state with a manual "Run now" escape hatch while the
 * thread sits queued and no worker is running, the seven-step product pipeline derived from the
 * thread's real event log, the actionable blocker cards read from `/threads/{id}/remediations`
 * ({@link ThreadBlockerList}) plus the two thread-state blockers that are not remediations (pending
 * decision, approval), and the humanized execution console. Raw event payloads stay behind a per-row
 * disclosure — never rendered by default.
 * @author Rodrigo Mason
 */
import { AlertTriangle, ExternalLink, Play } from 'lucide-react';
import { m } from 'motion/react';
import { useMemo, useState } from 'react';

import type { WorkerStatusResponse } from '../../api/client';
import type { ThreadAgentEvent, ThreadDecision, ThreadMessage } from '../../api/types';
import { Button, StatusChip } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { EASE_OUT } from '../../motion/variants';
import { describeReasonCode } from '../runtime-setup/reasonCopy';
import { ThreadBlockerList } from './ThreadBlockerCard';
import { MESSAGE_META, safeRecord, textValue } from './threadPresentation';
import type { ThreadRemediationsHandle } from './useThreadRemediations';

type Translate = ReturnType<typeof useI18n>['t'];

export type ThreadExecutionPanelProps = {
	threadStatus: string;
	events: ThreadAgentEvent[];
	/** Execution-side messages only (agent_summary, system_event, error). */
	messages: ThreadMessage[];
	pendingDecision: ThreadDecision | null;
	workerStatus: WorkerStatusResponse | null;
	workerBusy: boolean;
	syncing: boolean;
	streamError: string;
	waitingForWorker: boolean;
	handoffFromIntake: boolean;
	/** Persisted blocker remediations for this thread; the queued banner owns `worker_not_running`. */
	remediations: ThreadRemediationsHandle;
	onRunQueuedNow: () => void;
	onOpenApprovals: () => void;
	onFocusDecision: () => void;
	/** Opens a Settings section — the recovery path for settings-kind remediation actions. */
	onOpenSettings: (section?: string) => void;
	/** `strip` compacts the panel into a horizontal band above the story board (development mode). */
	presentation?: 'column' | 'strip';
};

const PIPELINE_STEPS = [
	{ id: 'runtime_check', labelKey: 'app.threads.step.runtime_check', fallback: 'Runtime check' },
	{ id: 'git_check', labelKey: 'app.threads.step.git_check', fallback: 'Git check' },
	{ id: 'branch_ready', labelKey: 'app.threads.step.branch_ready', fallback: 'Branch ready' },
	{ id: 'executing', labelKey: 'app.threads.step.executing', fallback: 'Executing' },
	{ id: 'qa_running', labelKey: 'app.threads.step.qa_running', fallback: 'QA checks' },
	{
		id: 'security_running',
		labelKey: 'app.threads.step.security_running',
		fallback: 'Security scan',
	},
	{
		id: 'awaiting_approval',
		labelKey: 'app.threads.step.awaiting_approval',
		fallback: 'Awaiting approval',
	},
] as const;

/** Step index of `executing`: from here on the thread is in development (board layout). */
export const EXECUTING_STEP_INDEX = PIPELINE_STEPS.findIndex((step) => step.id === 'executing');

/** Maps every pipeline event type onto the index of the step it makes current. Planning-phase
 *  states (discovery → backlog) sit between git_check and branch_ready, so they point at the
 *  branch step; the console rows below the stepper carry the fine-grained activity. */
const MILESTONE_INDEX: Record<string, number> = {
	workspace_check: 0,
	runtime_check: 0,
	runtime_selected: 0,
	git_check: 1,
	discovery: 2,
	discovering: 2,
	research_adopted: 2,
	runtime_risk_approved: 2,
	awaiting_user: 2,
	planning: 2,
	brief_ready: 2,
	architecture_review: 2,
	backlog_ready: 2,
	iteration_planning: 2,
	branch_ready: 2,
	executing: 3,
	agent_running: 3,
	reworking: 3,
	qa_running: 4,
	security_running: 5,
	// Analysis (quality_review) runs right after the security gate and has no step of its own,
	// so it keeps the security step current instead of freezing the stepper.
	quality_review: 5,
	review_ready: 6,
	awaiting_approval: 6,
	approval_required: 6,
	completed: PIPELINE_STEPS.length,
	delivered: PIPELINE_STEPS.length,
};

/** Blockers may name the failing agent/guard instead of emitting a pipeline milestone. */
const BLOCKED_STAGE_INDEX: Record<string, number> = {
	runtime: 0,
	resource_manager: 0,
	product_owner: 2,
	research: 2,
	worker: 3,
};

type StepState = 'pending' | 'active' | 'done' | 'blocked' | 'stopped';

/** Thread states with no job in flight: nothing is running, so no step can still be active. */
const IDLE_THREAD_STATUSES = new Set(['open', 'archived']);

type PipelineSnapshot = {
	/** Index of the step the newest milestone made current (>= PIPELINE_STEPS.length once delivered). */
	current: number;
	states: StepState[];
	/** Sequence of the newest milestone event, used to decide whether a `blocked` event is current. */
	lastMilestoneSeq: number;
	/** True while the thread is stopped on a blocker, whether or not a remediation was persisted. */
	blocked: boolean;
	blockedReason: string;
	blockedStage: string;
};

/**
 * Derives the seven-step pipeline from the append-only event log. The newest milestone event wins
 * (so a QA rework honestly moves the pipeline back to "executing"); the two readiness checks are
 * unordered peers, so each one counts as done as soon as its own event was seen.
 */
export function derivePipeline(
	events: ThreadAgentEvent[],
	threadStatus: string,
	remediationStage: string,
): PipelineSnapshot {
	let current = -1;
	let lastMilestoneSeq = 0;
	const seenChecks = new Set<number>();
	let blockedSeq = 0;
	let blockedReason = '';
	let blockedStage = '';
	let blockedLoopId = '';

	for (const event of events) {
		const milestone = MILESTONE_INDEX[event.type];
		if (milestone !== undefined) {
			current = milestone;
			lastMilestoneSeq = event.sequence;
			if (milestone <= 1) seenChecks.add(milestone);
		}
		// Capacity failures can leave a job queued; its waiting-worker banner owns that state.
		const workerExecutionFailed = event.type === 'worker_failed' && threadStatus !== 'queued';
		if (event.type === 'blocked' || workerExecutionFailed) {
			const payload = safeRecord(event.payload);
			const reason = textValue(payload.reason) ?? '';
			const loopId = textValue(payload.loopId) ?? '';
			const sameFailure =
				blockedSeq > lastMilestoneSeq &&
				loopId !== '' &&
				loopId === blockedLoopId &&
				reason === blockedReason;
			// A job-level failure repeats the agent's blocker without its stage; preserve only that
			// same failure's stage, never the stage of an earlier loop or a different cause.
			blockedStage = workerExecutionFailed
				? 'worker'
				: textValue(payload.stage) || (sameFailure ? blockedStage : '');
			blockedSeq = event.sequence;
			blockedReason = reason;
			blockedLoopId = loopId;
		}
	}

	// A newer lifecycle milestone supersedes a historical blocker even while the thread snapshot
	// still says blocked. A later failure remains authoritative over an earlier research adoption.
	const isBlocked = blockedSeq > 0 ? blockedSeq > lastMilestoneSeq : threadStatus === 'blocked';
	if (isBlocked) {
		// Auxiliary research jobs may append a blocker without the product loop's stage.
		blockedStage ||= remediationStage;
		current = BLOCKED_STAGE_INDEX[blockedStage] ?? MILESTONE_INDEX[blockedStage] ?? current;
	}
	const doneAll = current >= PIPELINE_STEPS.length || threadStatus === 'resolved';
	const idle = !isBlocked && IDLE_THREAD_STATUSES.has(threadStatus);
	const states = PIPELINE_STEPS.map((_, index): StepState => {
		if (doneAll) return 'done';
		if (index === current) {
			if (isBlocked) return 'blocked';
			return idle ? 'stopped' : 'active';
		}
		if (index <= 1 && seenChecks.has(index)) return 'done';
		if (index < current) return 'done';
		return 'pending';
	});
	return {
		current,
		states,
		lastMilestoneSeq,
		blocked: isBlocked,
		blockedReason: isBlocked ? blockedReason : '',
		blockedStage: isBlocked ? blockedStage : '',
	};
}

type BlockerCard = {
	id: string;
	title: string;
	detail: string;
	hint?: string;
	actionLabel?: string;
	onAction?: () => void;
	actionBusy?: boolean;
	/** True when the action navigates away from the thread (shown with an outbound icon). */
	actionExternal?: boolean;
};

/** The queued banner already offers "Run now", so the worker remediation is hidden in this host. */
const REMEDIATION_EXCLUDE_IN_PANEL = ['worker_not_running'] as const;

/** Reason code of the newest `resource_wait` the governor reported after the last hand-off to a worker. */
function latestResourceWait(events: ThreadAgentEvent[]): string {
	let code = '';
	for (const event of events) {
		if (event.type === 'resource_wait')
			code = textValue(safeRecord(event.payload).reasonCode) ?? '';
		else if (event.type === 'worker_claimed' || event.type === 'run_queued') code = '';
	}
	return code;
}

type QueuedBannerCopy = { label: string; title: string; body: string };

/**
 * Accessible name, title and body of the queued banner: starting, waiting for machine capacity, or for a worker.
 * The starting state keeps the historical "Waiting for worker" region name that existing specs locate.
 */
function queuedBannerCopy(
	workerBusy: boolean,
	resourceWaitCode: string,
	t: Translate,
): QueuedBannerCopy {
	if (workerBusy) {
		return {
			label: t('app.threads.waitingWorkerTitle', 'Waiting for worker'),
			title: t('app.threads.startingRunTitle', 'Starting run'),
			body: t(
				'app.threads.startingRun',
				'Starting this run — progress appears in the execution log below.',
			),
		};
	}
	if (resourceWaitCode) {
		return {
			label: t('app.threads.waitingCapacityTitle', 'Waiting for machine capacity'),
			title: t('app.threads.waitingCapacityTitle', 'Waiting for machine capacity'),
			body: `${t('app.threads.waitingCapacity', 'Waiting for machine capacity:')} ${describeReasonCode(resourceWaitCode, t)}`,
		};
	}
	return {
		label: t('app.threads.waitingWorkerTitle', 'Waiting for worker'),
		title: t('app.threads.waitingWorkerTitle', 'Waiting for worker'),
		body: t('app.threads.waitingWorker', 'Queued: waiting for a worker to pick this run up.'),
	};
}

/** Everything AIDO is doing for this thread, rendered as its own panel next to the transcript. */
export function ThreadExecutionPanel({
	threadStatus,
	events,
	messages,
	pendingDecision,
	workerStatus,
	workerBusy,
	syncing,
	streamError,
	waitingForWorker,
	handoffFromIntake,
	remediations,
	onRunQueuedNow,
	onOpenApprovals,
	onFocusDecision,
	onOpenSettings,
	presentation = 'column',
}: ThreadExecutionPanelProps) {
	const { t } = useI18n();

	const pipeline = useMemo(() => {
		const stages = new Set(
			remediations.cards
				.filter((card) => !REMEDIATION_EXCLUDE_IN_PANEL.some((type) => type === card.blockerType))
				.map((card) => card.stage)
				.filter(Boolean),
		);
		const remediationStage = stages.size === 1 ? ([...stages][0] ?? '') : '';
		return derivePipeline(events, threadStatus, remediationStage);
	}, [events, threadStatus, remediations.cards]);

	// Thread-state blockers that are NOT persisted remediations: a pending decision and an awaiting
	// approval. The stage/runtime/git/worker blockers now come from the remediations endpoint below.
	const blockers = collectBlockers({
		t,
		threadStatus,
		pendingDecision,
		onOpenApprovals,
		onFocusDecision,
	});

	const consoleEntries = useMemo(() => mergeConsoleEntries(events, messages), [events, messages]);
	const resourceWaitCode = useMemo(() => latestResourceWait(events), [events]);
	const queuedCopy = queuedBannerCopy(workerBusy, resourceWaitCode, t);
	const workerIsRunning = workerStatus?.running === true;
	const showRunNow = waitingForWorker && !workerIsRunning;
	// A blocked run whose blocker the backend never mapped to a remediation still deserves a repair
	// card; the blocked event carries the only stage and cause we have for it.
	const remediationFallback = pipeline.blocked
		? { stage: pipeline.blockedStage, reason: pipeline.blockedReason }
		: null;

	return (
		<m.aside
			className="thread-execution-pane"
			data-presentation={presentation}
			aria-label={t('app.threads.consoleRegion', 'Execution console')}
			initial={{ opacity: 0, y: 6 }}
			animate={{
				opacity: 1,
				y: 0,
				transition: { duration: 0.2, ease: EASE_OUT, delay: handoffFromIntake ? 0.55 : 0 },
			}}
		>
			<div className="thread-execution-head">
				<span className="thread-execution-heading">
					{t('app.threads.consoleTitle', 'Execution')}
				</span>
				<div className="thread-console-actions">
					{workerStatus ? (
						<StatusChip tone={workerTone(workerStatus)}>{workerLabel(workerStatus, t)}</StatusChip>
					) : null}
					{syncing ? (
						<StatusChip tone="pending">{t('app.threads.consoleLoading', 'syncing')}</StatusChip>
					) : null}
				</div>
			</div>

			{streamError ? (
				<div className="thread-console-status" data-tone="danger">
					{streamError}
				</div>
			) : null}

			{waitingForWorker ? (
				<section className="thread-queued-banner" aria-label={queuedCopy.label}>
					<strong>{queuedCopy.title}</strong>
					<p>{queuedCopy.body}</p>
					{showRunNow ? (
						<>
							<p className="thread-queued-hint">
								{t(
									'app.threads.workerNotRunningHint',
									'The worker loop is not running; start this queued run manually.',
								)}
							</p>
							<Button
								variant="primary"
								icon={<Play aria-hidden="true" size={14} />}
								loading={workerBusy}
								disabled={workerBusy}
								onClick={onRunQueuedNow}
							>
								{workerBusy
									? t('app.threads.workerRunningNow', 'Running queued jobs')
									: t('app.threads.runNow', 'Run now')}
							</Button>
						</>
					) : null}
				</section>
			) : null}

			{blockers.length ? (
				<section
					className="thread-blocker-list"
					aria-label={t('app.threads.blockersTitle', 'Blockers')}
				>
					{blockers.map((blocker) => (
						<article className="thread-blocker-card" key={blocker.id}>
							<div className="thread-blocker-title">
								<AlertTriangle aria-hidden="true" size={14} />
								<strong>{blocker.title}</strong>
							</div>
							{blocker.detail ? <p>{blocker.detail}</p> : null}
							{blocker.hint ? <p className="thread-blocker-hint">{blocker.hint}</p> : null}
							{blocker.actionLabel && blocker.onAction ? (
								<Button
									variant="secondary"
									icon={
										blocker.actionExternal ? (
											<ExternalLink aria-hidden="true" size={14} />
										) : undefined
									}
									loading={blocker.actionBusy}
									disabled={blocker.actionBusy}
									onClick={blocker.onAction}
								>
									{blocker.actionLabel}
								</Button>
							) : null}
						</article>
					))}
				</section>
			) : null}

			{/* Persisted, executable repair actions for runtime/git/gitleaks/QA/provider blockers. The
			    queued banner above owns the worker-not-running "Run now", so it is excluded here. */}
			<ThreadBlockerList
				handle={remediations}
				onOpenSettings={onOpenSettings}
				excludeBlockerTypes={REMEDIATION_EXCLUDE_IN_PANEL}
				fallback={remediationFallback}
			/>

			<ol className="thread-pipeline" aria-label={t('app.threads.pipelineTitle', 'Pipeline steps')}>
				{PIPELINE_STEPS.map((step, index) => (
					<li
						className="thread-pipeline-step"
						data-state={pipeline.states[index]}
						aria-current={pipeline.states[index] === 'active' ? 'step' : undefined}
						key={step.id}
					>
						<span className="thread-pipeline-dot" aria-hidden="true" />
						<span className="thread-pipeline-label">
							{step.id === 'branch_ready' &&
							pipeline.states[index] === 'blocked' &&
							['research', 'product_owner'].includes(pipeline.blockedStage)
								? t('app.threads.step.discovery_research', 'Discovery and research')
								: t(step.labelKey, step.fallback)}
						</span>
						<span className="thread-pipeline-state">
							{t(`app.threads.stepState.${pipeline.states[index]}`, pipeline.states[index])}
						</span>
					</li>
				))}
			</ol>

			<div className="thread-execution-log" role="log" aria-live="polite" aria-relevant="additions">
				{consoleEntries.length ? (
					consoleEntries.map((entry) =>
						entry.kind === 'event' ? (
							<ThreadConsoleRow key={entry.key} event={entry.event} />
						) : (
							<ThreadConsoleMessageRow key={entry.key} message={entry.message} />
						),
					)
				) : (
					<div className="thread-console-status" data-tone="muted">
						{t('app.threads.consoleEmpty', 'No execution events yet.')}
					</div>
				)}
			</div>
		</m.aside>
	);
}

function workerTone(status: WorkerStatusResponse) {
	if (status.running) return 'ok';
	if (status.paused || status.status === 'idle') return 'pending';
	if (status.status === 'blocked' || status.status === 'failed') return 'danger';
	return 'warn';
}

function workerLabel(status: WorkerStatusResponse, t: Translate) {
	if (status.running) return t('app.threads.workerRunning', 'Worker running');
	if (status.paused) return t('app.threads.workerPaused', 'Worker paused');
	return t('app.threads.workerStopped', 'Worker stopped');
}

type CollectBlockersInput = {
	t: Translate;
	threadStatus: string;
	pendingDecision: ThreadDecision | null;
	onOpenApprovals: () => void;
	onFocusDecision: () => void;
};

/**
 * Builds the two thread-state blocker cards that are not persisted remediations: a pending decision
 * and an awaiting approval. Runtime/git/gitleaks/worker blockers come from the remediations endpoint
 * ({@link ThreadBlockerList}), so they are intentionally not derived from the event log here.
 */
function collectBlockers({
	t,
	threadStatus,
	pendingDecision,
	onOpenApprovals,
	onFocusDecision,
}: CollectBlockersInput): BlockerCard[] {
	const cards: BlockerCard[] = [];
	if (pendingDecision) {
		cards.push({
			id: `decision-${pendingDecision.id}`,
			title: t('app.threads.blocker.decisionTitle', 'Decision pending'),
			detail: pendingDecision.prompt,
			actionLabel: t('app.threads.blocker.answerDecision', 'Answer decision'),
			onAction: onFocusDecision,
		});
	}
	if (threadStatus === 'awaiting_approval') {
		cards.push({
			id: 'approval',
			title: t('app.threads.blocker.approvalTitle', 'Approval required'),
			detail: t(
				'app.threads.blocker.approvalHint',
				'This run pauses until you approve it on the review board.',
			),
			actionLabel: t('app.threads.blocker.openApprovals', 'Open approvals'),
			// The only blocker action that leaves the thread (navigates to the review board), so it
			// alone carries the outbound icon.
			actionExternal: true,
			onAction: onOpenApprovals,
		});
	}
	return cards;
}

type ConsoleEntry =
	| { kind: 'event'; key: string; createdAt: string; sequence: number; event: ThreadAgentEvent }
	| { kind: 'message'; key: string; createdAt: string; sequence: number; message: ThreadMessage };

/** Interleaves execution events and execution-side messages chronologically (createdAt, then seq). */
function mergeConsoleEntries(
	events: ThreadAgentEvent[],
	messages: ThreadMessage[],
): ConsoleEntry[] {
	const entries: ConsoleEntry[] = [
		...events.map(
			(event): ConsoleEntry => ({
				kind: 'event',
				key: `event-${event.id}`,
				createdAt: event.createdAt,
				sequence: event.sequence,
				event,
			}),
		),
		...messages.map(
			(message): ConsoleEntry => ({
				kind: 'message',
				key: `message-${message.id}`,
				createdAt: message.createdAt,
				sequence: message.sequence,
				message,
			}),
		),
	];
	return entries.sort(
		(left, right) =>
			left.createdAt.localeCompare(right.createdAt) || left.sequence - right.sequence,
	);
}

/** Wall-clock HH:MM:SS for a console row; the full ISO instant stays available on hover. */
function formatClock(iso: string): string {
	const parsed = new Date(iso);
	if (Number.isNaN(parsed.getTime())) return '';
	return parsed.toLocaleTimeString(undefined, {
		hour12: false,
		hour: '2-digit',
		minute: '2-digit',
		second: '2-digit',
	});
}

function ThreadConsoleTime({ createdAt }: { createdAt: string }) {
	return (
		<time className="thread-console-time" dateTime={createdAt} title={createdAt}>
			{formatClock(createdAt)}
		</time>
	);
}

/** Execution-side message (agent summary, system event, error) rendered as a console row. */
function ThreadConsoleMessageRow({ message }: { message: ThreadMessage }) {
	const { t } = useI18n();
	const meta = MESSAGE_META[message.kind];
	return (
		<div className="thread-console-row" data-type={message.kind}>
			<ThreadConsoleTime createdAt={message.createdAt} />
			<span className="thread-console-seq mono">#{message.sequence}</span>
			<span className="thread-console-actor">
				<StatusChip tone={meta.tone}>{t(meta.authorKey, message.author)}</StatusChip>
			</span>
			<div className="thread-console-body">
				<p>{message.content}</p>
			</div>
		</div>
	);
}

/** Una fila de consola del hilo; exportada para que el dock inferior use el mismo lenguaje visual. */
export function ThreadConsoleRow({ event }: { event: ThreadAgentEvent }) {
	const { t } = useI18n();
	const [detailsOpen, setDetailsOpen] = useState(false);
	const payload = safeRecord(event.payload);
	const actor =
		event.agentRole || textValue(payload.role) || textValue(payload.agentName) || 'aido';
	const title = t(`app.threads.event.${event.type}`, eventTitle(event.type));
	const detail = eventDetail(payload, t);
	const chips = [
		textValue(payload.status) || textValue(payload.toState),
		textValue(payload.runtimeId),
		textValue(payload.agentId),
		textValue(payload.workerId),
		textValue(payload.jobId),
	].filter((value): value is string => Boolean(value));
	const hasPayload = Object.keys(payload).length > 0;
	return (
		<div className="thread-console-row" data-type={event.type}>
			<ThreadConsoleTime createdAt={event.createdAt} />
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
				{hasPayload ? (
					<details
						className="thread-console-payload"
						onToggle={(event) => setDetailsOpen(event.currentTarget.open)}
					>
						<summary>{t('app.threads.eventDetails', 'Technical details')}</summary>
						{detailsOpen ? <pre>{JSON.stringify(payload, null, 2)}</pre> : null}
					</details>
				) : null}
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
		worker_started: 'Worker started',
		worker_claimed: 'Worker assigned',
		resource_wait: 'Waiting for capacity',
		worker_idle: 'Worker idle',
		worker_failed: 'Worker failed',
		worker_paused: 'Worker paused',
		worker_aborted: 'Execution stopped',
		operator_note_added: 'Operator note',
		execution_cancelled: 'Execution cancelled',
		runtime_selected: 'Runtime selected',
		runtime_failover: 'Runtime failover',
		agent_running: 'Agent working',
		workspace_check: 'Workspace',
		runtime_check: 'Runtime',
		git_check: 'Git',
		discovery: 'Discovery',
		planning: 'Planning',
		backlog_ready: 'Backlog ready',
		branch_ready: 'Branch ready',
		brief_ready: 'Brief ready',
		plan_ready: 'Plan ready',
		executing: 'Execution',
		qa_running: 'QA',
		security_running: 'Security',
		quality_review: 'Analysis',
		review_ready: 'Review',
		awaiting_approval: 'Approval required',
		approval_required: 'Approval required',
		blocked: 'Blocked',
		completed: 'Completed',
		decision_required: 'Decision required',
		decision_resolved: 'Decision resolved',
		goal_received: 'Goal received',
		product_owner_completed: 'Product owner finished',
		agent_tasks_ready: 'Agent tasks ready',
		reworking: 'Reworking',
		cancelled: 'Cancelled',
	};
	return titles[type] ?? type.replace(/_/g, ' ');
}

function eventDetail(payload: Record<string, unknown>, t: Translate): string {
	const reason = textValue(payload.reason);
	if (reason) return reason;
	const roles = safeRecord(payload.summary).roles;
	if (Array.isArray(roles) && roles.length) {
		return `${t('app.threads.eventRoles', 'Roles')}: ${roles.map(String).join(', ')}`;
	}
	const fromState = textValue(payload.fromState);
	const toState = textValue(payload.toState);
	if (fromState && toState) {
		const fromLabel = t(`app.threads.event.${fromState}`, eventTitle(fromState));
		const toLabel = t(`app.threads.event.${toState}`, eventTitle(toState));
		return `${fromLabel} -> ${toLabel}`;
	}
	const assignmentId = textValue(payload.assignmentId);
	if (assignmentId) return `${t('app.threads.eventAssignment', 'Assignment')}: ${assignmentId}`;
	// The visible clock column already tells *when*; a raw ISO detail would only repeat it.
	return '';
}
