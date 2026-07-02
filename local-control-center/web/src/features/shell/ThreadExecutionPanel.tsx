/**
 * Right-hand execution panel of the threads shell: everything AIDO is *doing*, kept apart from the
 * conversation transcript. Surfaces the worker state with a manual "Run now" escape hatch while the
 * thread sits queued and no worker is running, the seven-step product pipeline derived from the
 * thread's real event log, actionable blockers (runtime setup, gitleaks, dirty git tree, missing
 * branch, pending decision, approval, worker error) and the humanized execution console. Raw event
 * payloads stay behind a per-row disclosure — never rendered by default.
 * @author Rodrigo Mason
 */
import { AlertTriangle, Play } from 'lucide-react';
import { m } from 'motion/react';
import { useMemo, useState } from 'react';

import { scanProjectGitleaks, type WorkerStatusResponse } from '../../api/client';
import type { Project, ThreadAgentEvent, ThreadDecision, ThreadMessage } from '../../api/types';
import { Button, StatusChip, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { EASE_OUT } from '../../motion/variants';
import { MESSAGE_META, safeRecord, textValue } from './threadPresentation';

type Translate = ReturnType<typeof useI18n>['t'];

export type ThreadExecutionPanelProps = {
	project: Project;
	token: string;
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
	onRunQueuedNow: () => void;
	onOpenRuntimeSetup: () => void;
	onOpenApprovals: () => void;
	onFocusDecision: () => void;
	onFocusGitBar: () => void;
	onGitRefresh: () => void;
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
	review_ready: 6,
	awaiting_approval: 6,
	approval_required: 6,
	completed: PIPELINE_STEPS.length,
	delivered: PIPELINE_STEPS.length,
};

type StepState = 'pending' | 'active' | 'done' | 'blocked';

type PipelineSnapshot = {
	states: StepState[];
	/** Sequence of the newest milestone event, used to decide whether a `blocked` event is current. */
	lastMilestoneSeq: number;
	blockedReason: string;
	blockedStage: string;
};

/**
 * Derives the seven-step pipeline from the append-only event log. The newest milestone event wins
 * (so a QA rework honestly moves the pipeline back to "executing"); the two readiness checks are
 * unordered peers, so each one counts as done as soon as its own event was seen.
 */
function derivePipeline(events: ThreadAgentEvent[], threadStatus: string): PipelineSnapshot {
	let current = -1;
	let lastMilestoneSeq = 0;
	const seenChecks = new Set<number>();
	let blockedSeq = 0;
	let blockedReason = '';
	let blockedStage = '';

	for (const event of events) {
		const milestone = MILESTONE_INDEX[event.type];
		if (milestone !== undefined) {
			current = milestone;
			lastMilestoneSeq = event.sequence;
			if (milestone <= 1) seenChecks.add(milestone);
		}
		if (event.type === 'blocked') {
			const payload = safeRecord(event.payload);
			blockedSeq = event.sequence;
			blockedReason = textValue(payload.reason) ?? '';
			blockedStage = textValue(payload.stage) ?? '';
		}
	}

	const doneAll = current >= PIPELINE_STEPS.length || threadStatus === 'resolved';
	const isBlocked = threadStatus === 'blocked' || blockedSeq > lastMilestoneSeq;
	const states = PIPELINE_STEPS.map((_, index): StepState => {
		if (doneAll) return 'done';
		if (index === current) return isBlocked ? 'blocked' : 'active';
		if (index <= 1 && seenChecks.has(index)) return 'done';
		if (index < current) return 'done';
		return 'pending';
	});
	return {
		states,
		lastMilestoneSeq,
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
};

const RUNTIME_STAGES = new Set(['runtime', 'product_owner_runtime']);
const WORKSPACE_STAGES = new Set(['workspace', 'workspace_check', 'product_owner_workspace']);

/** Everything AIDO is doing for this thread, rendered as its own panel next to the transcript. */
export function ThreadExecutionPanel({
	project,
	token,
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
	onRunQueuedNow,
	onOpenRuntimeSetup,
	onOpenApprovals,
	onFocusDecision,
	onFocusGitBar,
	onGitRefresh,
}: ThreadExecutionPanelProps) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [gitleaksBusy, setGitleaksBusy] = useState(false);

	const pipeline = useMemo(() => derivePipeline(events, threadStatus), [events, threadStatus]);

	const verifyGitleaks = async () => {
		if (gitleaksBusy) return;
		setGitleaksBusy(true);
		try {
			const result = await scanProjectGitleaks(token, project.id);
			notify({
				title:
					result.status === 'completed'
						? t('app.statusBar.git.gitleaksPassed', 'Gitleaks passed')
						: t('app.statusBar.git.gitleaksBlocked', 'Gitleaks blocked delivery'),
				body: result.reason,
				tone:
					result.status === 'completed'
						? 'ok'
						: result.status === 'configuration_required'
							? 'warn'
							: 'danger',
			});
			onGitRefresh();
		} catch (error) {
			notify({
				title: t('app.statusBar.git.gitleaksFailed', 'Gitleaks scan failed'),
				body: error instanceof Error ? error.message : undefined,
				tone: 'danger',
			});
		} finally {
			setGitleaksBusy(false);
		}
	};

	const blockers = collectBlockers({
		t,
		threadStatus,
		pipeline,
		pendingDecision,
		workerStatus,
		gitleaksBusy,
		onOpenRuntimeSetup,
		onOpenApprovals,
		onFocusDecision,
		onFocusGitBar,
		onVerifyGitleaks: () => void verifyGitleaks(),
	});

	const consoleEntries = useMemo(() => mergeConsoleEntries(events, messages), [events, messages]);
	const workerIsRunning = workerStatus?.running === true;
	const showRunNow = waitingForWorker && !workerIsRunning;

	return (
		<m.aside
			className="thread-execution-pane"
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
				<section
					className="thread-queued-banner"
					aria-label={t('app.threads.waitingWorkerTitle', 'Waiting for worker')}
				>
					<strong>{t('app.threads.waitingWorkerTitle', 'Waiting for worker')}</strong>
					<p>{t('app.threads.waitingWorker', 'Queued: waiting for a worker to claim this run.')}</p>
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

			<ol className="thread-pipeline" aria-label={t('app.threads.pipelineTitle', 'Pipeline steps')}>
				{PIPELINE_STEPS.map((step, index) => (
					<li
						className="thread-pipeline-step"
						data-state={pipeline.states[index]}
						aria-current={pipeline.states[index] === 'active' ? 'step' : undefined}
						key={step.id}
					>
						<span className="thread-pipeline-dot" aria-hidden="true" />
						<span className="thread-pipeline-label">{t(step.labelKey, step.fallback)}</span>
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
	pipeline: PipelineSnapshot;
	pendingDecision: ThreadDecision | null;
	workerStatus: WorkerStatusResponse | null;
	gitleaksBusy: boolean;
	onOpenRuntimeSetup: () => void;
	onOpenApprovals: () => void;
	onFocusDecision: () => void;
	onFocusGitBar: () => void;
	onVerifyGitleaks: () => void;
};

/** Builds the actionable blocker cards: decision first, then approval, run block, worker error. */
function collectBlockers({
	t,
	threadStatus,
	pipeline,
	pendingDecision,
	workerStatus,
	gitleaksBusy,
	onOpenRuntimeSetup,
	onOpenApprovals,
	onFocusDecision,
	onFocusGitBar,
	onVerifyGitleaks,
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
			onAction: onOpenApprovals,
		});
	}
	if (pipeline.blockedStage || pipeline.blockedReason) {
		cards.push(
			blockerForStage({
				t,
				stage: pipeline.blockedStage,
				reason: pipeline.blockedReason,
				gitleaksBusy,
				onOpenRuntimeSetup,
				onFocusGitBar,
				onVerifyGitleaks,
			}),
		);
	}
	if (
		workerStatus &&
		(workerStatus.status === 'failed' || workerStatus.status === 'blocked') &&
		workerStatus.lastError
	) {
		cards.push({
			id: 'worker-error',
			title: t('app.threads.blocker.workerTitle', 'Worker error'),
			detail: workerStatus.lastError,
			// 'blocked' only ever comes from the worker preflight, whose two failure modes are a
			// missing runtime or a missing gitleaks executable — so it always has a remedy to offer.
			...(workerStatus.status === 'blocked'
				? /gitleaks/i.test(workerStatus.lastError)
					? {
							hint: t(
								'app.threads.blocker.gitleaksHint',
								'Install gitleaks, then run the scan again.',
							),
							actionLabel: t('app.statusBar.git.runGitleaks', 'Run gitleaks scan'),
							onAction: onVerifyGitleaks,
							actionBusy: gitleaksBusy,
						}
					: {
							actionLabel: t('app.threads.blocker.configureRuntime', 'Configure runtime'),
							onAction: onOpenRuntimeSetup,
						}
				: {}),
		});
	}
	return cards;
}

type BlockerForStageInput = {
	t: Translate;
	stage: string;
	reason: string;
	gitleaksBusy: boolean;
	onOpenRuntimeSetup: () => void;
	onFocusGitBar: () => void;
	onVerifyGitleaks: () => void;
};

/** Maps the coordinator's `blocked` stage onto a card whose action actually remedies the block. */
function blockerForStage({
	t,
	stage,
	reason,
	gitleaksBusy,
	onOpenRuntimeSetup,
	onFocusGitBar,
	onVerifyGitleaks,
}: BlockerForStageInput): BlockerCard {
	if (RUNTIME_STAGES.has(stage)) {
		return {
			id: `stage-${stage}`,
			title: t('app.threads.blocker.runtimeTitle', 'Runtime unavailable'),
			detail: reason,
			actionLabel: t('app.threads.blocker.configureRuntime', 'Configure runtime'),
			onAction: onOpenRuntimeSetup,
		};
	}
	if (stage === 'gitleaks') {
		return {
			id: 'stage-gitleaks',
			title: t('app.threads.blocker.gitleaksTitle', 'Gitleaks blocked delivery'),
			detail: reason,
			hint: t('app.threads.blocker.gitleaksHint', 'Install gitleaks, then run the scan again.'),
			actionLabel: t('app.statusBar.git.runGitleaks', 'Run gitleaks scan'),
			onAction: onVerifyGitleaks,
			actionBusy: gitleaksBusy,
		};
	}
	if (stage === 'git') {
		return {
			id: 'stage-git',
			title: t('app.threads.blocker.gitTitle', 'Git workspace blocked'),
			detail: reason,
			hint: t('app.threads.blocker.gitHint', 'Clean the project working tree, then retry.'),
			actionLabel: t('app.threads.blocker.openGitControls', 'Open Git controls'),
			onAction: onFocusGitBar,
		};
	}
	if (WORKSPACE_STAGES.has(stage)) {
		return {
			id: `stage-${stage}`,
			title: t('app.threads.blocker.branchTitle', 'Working branch required'),
			detail: reason,
			hint: t(
				'app.threads.blocker.branchHint',
				'Create a working branch from the Git bar in the composer.',
			),
			actionLabel: t('app.threads.blocker.openGitControls', 'Open Git controls'),
			onAction: onFocusGitBar,
		};
	}
	return {
		id: `stage-${stage || 'unknown'}`,
		title: t('app.threads.blocker.genericTitle', 'Run blocked'),
		detail: reason,
	};
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

/** Execution-side message (agent summary, system event, error) rendered as a console row. */
function ThreadConsoleMessageRow({ message }: { message: ThreadMessage }) {
	const { t } = useI18n();
	const meta = MESSAGE_META[message.kind];
	return (
		<div className="thread-console-row" data-type={message.kind}>
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

function ThreadConsoleRow({ event }: { event: ThreadAgentEvent }) {
	const { t } = useI18n();
	const payload = safeRecord(event.payload);
	const actor =
		event.agentRole || textValue(payload.role) || textValue(payload.agentName) || 'aido';
	const title = t(`app.threads.event.${event.type}`, eventTitle(event.type));
	const detail = eventDetail(event, payload, t);
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
					<details className="thread-console-payload">
						<summary>{t('app.threads.eventDetails', 'Technical details')}</summary>
						<pre>{JSON.stringify(payload, null, 2)}</pre>
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
		worker_idle: 'Worker idle',
		worker_failed: 'Worker failed',
		worker_paused: 'Worker paused',
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

function eventDetail(
	event: ThreadAgentEvent,
	payload: Record<string, unknown>,
	t: Translate,
): string {
	const reason = textValue(payload.reason);
	if (reason) return reason;
	const roles = safeRecord(payload.summary).roles;
	if (Array.isArray(roles) && roles.length) {
		return `${t('app.threads.eventRoles', 'Roles')}: ${roles.map(String).join(', ')}`;
	}
	const fromState = textValue(payload.fromState);
	const toState = textValue(payload.toState);
	if (fromState && toState) return `${fromState} -> ${toState}`;
	const assignmentId = textValue(payload.assignmentId);
	if (assignmentId) return `${t('app.threads.eventAssignment', 'Assignment')}: ${assignmentId}`;
	return event.createdAt;
}
