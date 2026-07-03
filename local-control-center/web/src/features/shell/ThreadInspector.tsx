/**
 * Thread Inspector: the "AI manager" console rendered in the shell's right inspector pane for
 * the threads area.
 *
 * Eight tabs turn the thread from a plain chat into an operations console over real data:
 * Goal (thread objective + status), Team (agent roster with availability, assignment, blocked
 * reason, reviewer policy and recorded spend), Plan (product-loop FSM state, phases, blockers,
 * acceptance criteria, quality gates), Backlog (epics → stories → agent tasks), Memory (what
 * AIDO recalls about work like this: similar threads, previous decisions, related evidence,
 * lessons learned, prior performance passes and functionality already implemented, all from
 * the thread memory recall endpoint), Research (report artifacts with sources and trust levels),
 * Artifacts (all thread artifacts) and Settings (resolved read-only configuration). A pinned
 * vitals strip above the tabs keeps thread status, loop state and pending decisions visible
 * on every tab. Every tab renders honest loading/error/empty/success states; data loads via
 * {@link useThreadInspectorData}. Read-only by design — mutations stay in the composer.
 * @author Rodrigo Mason
 */
import {
	AlertTriangle,
	BrainCircuit,
	ExternalLink,
	FileStack,
	FlaskConical,
	Layers,
	ListTree,
	type LucideIcon,
	SlidersHorizontal,
	Target,
	Users,
} from 'lucide-react';
import { AnimatePresence, m } from 'motion/react';
import type { ReactNode } from 'react';
import { useMemo, useState } from 'react';

import type {
	ProjectProductLoopResponse,
	ResolvedSetting,
	SettingsResponse,
	ThreadMemoryRecallResponse,
} from '../../api/client';
import type {
	AgentProfile,
	Overview,
	Project,
	ThreadArtifact,
	ThreadDetail,
} from '../../api/types';
import type { Mutate } from '../../app/routes';
import {
	Button,
	Disclosure,
	EmptyState,
	ErrorState,
	Skeleton,
	StatusChip,
	type StatusTone,
	Tabs,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { formatCostUsd, shortId, threadStatusTone, toneForStatus } from '../../lib/format';
import { cardTransition, crossfade, listStagger } from '../../motion/variants';
import { PRODUCT_LOOP_PHASES, PRODUCT_LOOP_STATE_ORDER } from '../workbench/productLoopModel';
import { ThreadBlockerList } from './ThreadBlockerCard';
import {
	type InspectorResource,
	type ThreadInspectorTab,
	useThreadInspectorData,
} from './useThreadInspectorData';
import { useThreadRemediations } from './useThreadRemediations';

type ThreadInspectorProps = {
	overview: Overview;
	project: Project;
	/** Active thread id, or null while the new-thread intake is open. */
	threadId: string | null;
	mutate: Mutate;
	/** Opens a Settings section — the recovery path for blocker remediation actions. */
	onOpenSettings: (section?: string) => void;
};

type LoopData = ProjectProductLoopResponse;
type LoopRecord = LoopData['loops'][number];

const TAB_DEFS: ReadonlyArray<{
	id: ThreadInspectorTab;
	icon: LucideIcon;
	labelKey: string;
	fallback: string;
}> = [
	{ id: 'goal', icon: Target, labelKey: 'app.threads.inspector.tab.goal', fallback: 'Goal' },
	{ id: 'team', icon: Users, labelKey: 'app.threads.inspector.tab.team', fallback: 'Team' },
	{ id: 'plan', icon: ListTree, labelKey: 'app.threads.inspector.tab.plan', fallback: 'Plan' },
	{
		id: 'backlog',
		icon: Layers,
		labelKey: 'app.threads.inspector.tab.backlog',
		fallback: 'Backlog',
	},
	{
		id: 'memory',
		icon: BrainCircuit,
		labelKey: 'app.threads.inspector.tab.memory',
		fallback: 'Memory',
	},
	{
		id: 'research',
		icon: FlaskConical,
		labelKey: 'app.threads.inspector.tab.research',
		fallback: 'Research',
	},
	{
		id: 'artifacts',
		icon: FileStack,
		labelKey: 'app.threads.inspector.tab.artifacts',
		fallback: 'Artifacts',
	},
	{
		id: 'settings',
		icon: SlidersHorizontal,
		labelKey: 'app.threads.inspector.tab.settings',
		fallback: 'Settings',
	},
];

/** The threads-area inspector console: eight lazily loaded, read-only views over real data. */
export function ThreadInspector({
	overview,
	project,
	threadId,
	mutate,
	onOpenSettings,
}: ThreadInspectorProps) {
	const { t } = useI18n();
	const [tab, setTab] = useState<ThreadInspectorTab>('goal');
	const { detail, loop, memory, settings } = useThreadInspectorData(project.id, threadId, tab);
	// Actionable repair cards read from the remediations endpoint; pinned above the tabs so a blocked
	// thread always shows how to unblock it, whichever manager view is open.
	const remediations = useThreadRemediations(threadId, mutate);

	const tabs = TAB_DEFS.map((def) => {
		const Icon = def.icon;
		const label = t(def.labelKey, def.fallback);
		return {
			id: def.id,
			label: (
				// `title` carries the label as a hover tooltip when the narrow pane collapses the
				// tabs to icon-only; the wrapped text keeps the tab's accessible name for screen
				// readers and role queries even while visually hidden.
				<span className="thread-inspector-tab-label" title={label}>
					<Icon aria-hidden="true" size={14} />
					<span className="thread-inspector-tab-text">{label}</span>
				</span>
			),
		};
	});

	let panel: ReactNode;
	if (tab === 'goal') {
		panel = <GoalPanel detail={detail} threadId={threadId} />;
	} else if (tab === 'team') {
		panel = <TeamPanel overview={overview} loop={loop} />;
	} else if (tab === 'plan') {
		panel = <PlanPanel loop={loop} />;
	} else if (tab === 'backlog') {
		panel = <BacklogPanel loop={loop} />;
	} else if (tab === 'memory') {
		panel = <MemoryPanel threadId={threadId} memory={memory} />;
	} else if (tab === 'research') {
		panel = <ResearchPanel detail={detail} threadId={threadId} />;
	} else if (tab === 'artifacts') {
		panel = <ArtifactsPanel detail={detail} threadId={threadId} />;
	} else {
		panel = <SettingsPanel settings={settings} />;
	}

	return (
		<div className="thread-inspector">
			<ThreadVitals threadId={threadId} detail={detail} loop={loop} />
			<ThreadBlockerList handle={remediations} onOpenSettings={onOpenSettings} />
			<Tabs
				label={t('app.threads.inspector.tabsLabel', 'Thread inspector views')}
				activeTab={tab}
				onChange={(id) => setTab(id as ThreadInspectorTab)}
				idBase="thread-inspector"
				tabs={tabs}
			>
				<AnimatePresence mode="popLayout" initial={false}>
					<m.div key={tab} variants={crossfade} initial="initial" animate="animate" exit="exit">
						{panel}
					</m.div>
				</AnimatePresence>
			</Tabs>
		</div>
	);
}

/**
 * Loop vitals pinned above the tabs: thread status, active loop state and pending decisions
 * stay visible on every tab so the operator never loses the "is the loop healthy" answer
 * while browsing Backlog/Memory/Research. Renders only the chips whose data has arrived —
 * no fabricated placeholders.
 */
function ThreadVitals({
	threadId,
	detail,
	loop,
}: {
	threadId: string | null;
	detail: InspectorResource<ThreadDetail>;
	loop: InspectorResource<LoopData>;
}) {
	const { t } = useI18n();
	if (!threadId) return null;
	const thread = detail.data?.thread ?? null;
	const activeLoop = loop.data ? activeLoopOf(loop.data) : null;
	const pendingDecisions =
		detail.data?.decisions.filter((decision) => decision.status === 'pending').length ?? 0;
	if (!thread && !activeLoop) return null;
	return (
		<div
			className="thread-inspector-summary"
			role="group"
			aria-label={t('app.threads.inspector.summaryLabel', 'Loop vitals')}
		>
			{thread ? (
				<StatusChip tone={threadStatusTone(thread.status)}>{humanize(thread.status)}</StatusChip>
			) : null}
			{activeLoop ? (
				<StatusChip tone={loopStateTone(activeLoop.state)}>{humanize(activeLoop.state)}</StatusChip>
			) : null}
			{pendingDecisions === 1 ? (
				<StatusChip tone="pending">
					{t('app.threads.inspector.summary.decision', '1 decision pending')}
				</StatusChip>
			) : null}
			{pendingDecisions > 1 ? (
				<StatusChip tone="pending">
					{t('app.threads.inspector.summary.decisions', '{count} decisions pending').replace(
						'{count}',
						String(pendingDecisions),
					)}
				</StatusChip>
			) : null}
		</div>
	);
}

/* ---------- shared panel states ---------- */

function PanelSkeleton() {
	const { t } = useI18n();
	return (
		<div className="thread-inspector-loading">
			<Skeleton
				className="thread-inspector-skeleton-row"
				label={t('app.threads.inspector.loading', 'Loading inspector data')}
			/>
			<Skeleton className="thread-inspector-skeleton-row" />
			<Skeleton className="thread-inspector-skeleton-row" />
			<Skeleton className="thread-inspector-skeleton-row" />
		</div>
	);
}

function PanelError({ onRetry }: { onRetry: () => void }) {
	const { t } = useI18n();
	return (
		<ErrorState
			title={t('app.threads.inspector.errorTitle', 'Could not load this view')}
			body={t('app.threads.inspector.errorBody', 'The request failed. Retry to fetch it again.')}
			action={
				<Button variant="secondary" onClick={onRetry}>
					{t('app.threads.retry', 'Retry')}
				</Button>
			}
		/>
	);
}

function NoThreadState() {
	const { t } = useI18n();
	return (
		<EmptyState
			title={t('app.threads.inspector.noThreadTitle', 'No live thread')}
			body={t('app.threads.inspector.noThreadBody', 'Create or select a thread to inspect it.')}
		/>
	);
}

/** Renders skeleton/error/success for one lazily loaded resource; children receive the data. */
function ResourceGate<T>({
	resource,
	children,
}: {
	resource: InspectorResource<T>;
	children: (data: T) => ReactNode;
}) {
	if (resource.error) return <PanelError onRetry={resource.reload} />;
	if (!resource.data) return <PanelSkeleton />;
	return <>{children(resource.data)}</>;
}

function SectionTitle({ children }: { children: ReactNode }) {
	return <h4 className="thread-inspector-section-title">{children}</h4>;
}

/* ---------- shared value helpers ---------- */

function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: {};
}

function textValue(value: unknown): string | undefined {
	return typeof value === 'string' && value.trim() ? value : undefined;
}

function arrayValue(value: unknown): unknown[] {
	return Array.isArray(value) ? value : [];
}

const dateFormatters = new Map<string, Intl.DateTimeFormat>();

function formatWhen(value: string | null | undefined, language: string): string {
	if (!value) return '—';
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return value;
	const locale = language || 'en';
	let formatter = dateFormatters.get(locale);
	if (!formatter) {
		formatter = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' });
		dateFormatters.set(locale, formatter);
	}
	return formatter.format(date);
}

function humanize(value: string): string {
	return value.replace(/_/g, ' ');
}

function loopStateTone(state: string): StatusTone {
	if (state === 'delivered') return 'ok';
	if (state === 'blocked' || state === 'cancelled') return 'danger';
	if (
		state.startsWith('awaiting') ||
		state === 'goal_received' ||
		state === 'discovering' ||
		state === 'discovery' ||
		state === 'planning'
	) {
		return 'pending';
	}
	return 'warn';
}

function trustTone(level: string): StatusTone {
	if (
		level === 'official_documentation' ||
		level === 'official_repository' ||
		level === 'standard_rfc'
	) {
		return 'ok';
	}
	if (level === 'untrusted') return 'danger';
	return 'info';
}

/* ---------- Goal ---------- */

function GoalPanel({
	detail,
	threadId,
}: {
	detail: InspectorResource<ThreadDetail>;
	threadId: string | null;
}) {
	const { t, language } = useI18n();
	if (!threadId) return <NoThreadState />;
	return (
		<ResourceGate resource={detail}>
			{(data) => {
				const thread = data.thread;
				const goalMessage = data.messages.find((message) => message.kind === 'user');
				const pendingDecisions = data.decisions.filter(
					(decision) => decision.status === 'pending',
				).length;
				return (
					<div className="thread-inspector-stack">
						<header className="thread-inspector-goal-head">
							<h3>{thread.title}</h3>
							<StatusChip tone={threadStatusTone(thread.status)}>
								{humanize(thread.status)}
							</StatusChip>
						</header>
						<p className="thread-inspector-goal-text">
							{goalMessage?.content.trim() ||
								thread.summary?.trim() ||
								t(
									'app.threads.inspector.goal.empty',
									'No goal captured yet — the first message sets the objective.',
								)}
						</p>
						<dl className="thread-inspector-meta">
							<div>
								<dt>{t('app.threads.inspector.goal.owner', 'Owner')}</dt>
								<dd className="mono">{humanize(thread.ownerType)}</dd>
							</div>
							<div>
								<dt>{t('app.threads.inspector.goal.created', 'Created')}</dt>
								<dd>{formatWhen(thread.createdAt, language)}</dd>
							</div>
							<div>
								<dt>{t('app.threads.inspector.goal.updated', 'Updated')}</dt>
								<dd>{formatWhen(thread.updatedAt, language)}</dd>
							</div>
							<div>
								<dt>{t('app.threads.inspector.goal.decisions', 'Pending decisions')}</dt>
								<dd>
									{pendingDecisions > 0 ? (
										<StatusChip tone="pending">{pendingDecisions}</StatusChip>
									) : (
										<span className="mono">0</span>
									)}
								</dd>
							</div>
						</dl>
						{thread.summary?.trim() && goalMessage ? (
							<Disclosure
								headingLevel={4}
								title={t('app.threads.inspector.goal.summary', 'Summary')}
							>
								<p className="thread-inspector-goal-text">{thread.summary}</p>
							</Disclosure>
						) : null}
					</div>
				);
			}}
		</ResourceGate>
	);
}

/* ---------- Team ---------- */

function TeamPanel({ overview, loop }: { overview: Overview; loop: InspectorResource<LoopData> }) {
	const { t } = useI18n();

	// Per-agent recorded spend: model calls joined to profiles through agent_runs metadata.
	const spendByProfile = useMemo(() => {
		const profileByRun = new Map<string, string>();
		for (const run of overview.agentRuns) {
			const profileId = textValue(asRecord(run.metadata).agentProfileId);
			if (profileId) profileByRun.set(run.id, profileId);
		}
		const totals = new Map<string, { costUsd: number; calls: number }>();
		for (const call of overview.modelCalls) {
			if (!call.agentRunId) continue;
			const profileId = profileByRun.get(call.agentRunId);
			if (!profileId) continue;
			const entry = totals.get(profileId) ?? { costUsd: 0, calls: 0 };
			entry.costUsd += Number.isFinite(call.costUsd) ? call.costUsd : 0;
			entry.calls += 1;
			totals.set(profileId, entry);
		}
		return totals;
	}, [overview.agentRuns, overview.modelCalls]);

	// Current (not yet released) assignment per agent, resolved to the task title.
	const assignmentByAgent = useMemo(() => {
		const map = new Map<string, { taskTitle: string; status: string }>();
		if (!loop.data) return map;
		const tasksById = new Map(loop.data.tasks.map((task) => [task.id, task]));
		for (const assignment of loop.data.assignments) {
			if (assignment.releasedAt || map.has(assignment.agentId)) continue;
			map.set(assignment.agentId, {
				taskTitle: tasksById.get(assignment.taskId)?.title ?? shortId(assignment.taskId),
				status: assignment.status,
			});
		}
		return map;
	}, [loop.data]);

	if (overview.agentProfiles.length === 0) {
		return (
			<EmptyState
				title={t('app.threads.inspector.team.empty', 'No agents configured yet')}
				body={t(
					'app.threads.inspector.team.emptyBody',
					'Configure the AI team from Settings to staff the loop.',
				)}
			/>
		);
	}

	// Roster at a glance: real, non-fabricated aggregates so a manager reads team health before
	// scanning rows. "Working" counts a live (unreleased) assignment; "Blocked" counts an agent
	// held by a runtime block or disabled for this project — the states that demand action.
	const total = overview.agentProfiles.length;
	const working = overview.agentProfiles.filter((profile) =>
		assignmentByAgent.has(profile.id),
	).length;
	const blocked = overview.agentProfiles.filter(
		(profile) =>
			Boolean(profile.runtimeAvailability?.blockedReason) ||
			textValue(asRecord(profile.projectOverride).status) === 'disabled',
	).length;
	const totalSpend = [...spendByProfile.values()].reduce((sum, entry) => sum + entry.costUsd, 0);

	return (
		<div className="thread-inspector-stack">
			<div
				className="thread-inspector-summary"
				role="group"
				aria-label={t('app.threads.inspector.team.summaryLabel', 'Team overview')}
			>
				<span className="thread-inspector-subline mono">
					{t('app.threads.inspector.team.total', 'Agents')} {total} ·{' '}
					{t('app.threads.inspector.team.working', 'Working')} {working} ·{' '}
					{t('app.threads.inspector.team.spend', 'Spend')} {formatCostUsd(totalSpend)}
				</span>
				{blocked > 0 ? (
					<StatusChip tone="danger">
						{t('app.threads.inspector.team.blockedCount', 'Blocked')} {blocked}
					</StatusChip>
				) : null}
			</div>
			<m.ul
				className="thread-inspector-list"
				variants={listStagger}
				initial="initial"
				animate="animate"
			>
				{overview.agentProfiles.map((profile) => (
					<m.li
						className="thread-inspector-row"
						key={profile.id}
						variants={cardTransition}
						data-active={assignmentByAgent.has(profile.id) ? 'true' : undefined}
					>
						<TeamMemberRow
							profile={profile}
							spend={spendByProfile.get(profile.id) ?? null}
							assignment={assignmentByAgent.get(profile.id) ?? null}
						/>
					</m.li>
				))}
			</m.ul>
		</div>
	);
}

function TeamMemberRow({
	profile,
	spend,
	assignment,
}: {
	profile: AgentProfile;
	spend: { costUsd: number; calls: number } | null;
	assignment: { taskTitle: string; status: string } | null;
}) {
	const { t } = useI18n();
	const availability = profile.runtimeAvailability;
	const availabilityStatus = availability?.status ?? 'unknown';
	const reviewer = asRecord(profile.reviewerPolicy);
	const reviewerRole = textValue(reviewer.reviewerRole);
	const reviewerMode = textValue(reviewer.mode);
	const override = asRecord(profile.projectOverride);
	const disabledForProject = textValue(override.status) === 'disabled';

	return (
		<>
			<div className="thread-inspector-row-main">
				<strong>{profile.name}</strong>
				{availability?.blockedReason ? (
					<span className="thread-inspector-blocked">
						<AlertTriangle aria-hidden="true" size={13} />
						{t('app.threads.inspector.team.blocked', 'Blocked')}: {availability.blockedReason}
					</span>
				) : null}
				{disabledForProject ? (
					<span className="thread-inspector-blocked">
						<AlertTriangle aria-hidden="true" size={13} />
						{t('app.threads.inspector.team.disabledForProject', 'Disabled for this project')}
					</span>
				) : null}
				<span className="thread-inspector-subline mono">
					{humanize(profile.role)} · {profile.runtimeType}
					{availability?.selectedProviderId ? ` · ${availability.selectedProviderId}` : ''}
				</span>
				{assignment ? (
					<span className="thread-inspector-subline">
						{t('app.threads.inspector.team.assignment', 'Assignment')}: {assignment.taskTitle} (
						{humanize(assignment.status)})
					</span>
				) : (
					<span className="thread-inspector-subline">
						{t('app.threads.inspector.team.noAssignment', 'No active assignment')}
					</span>
				)}
				<Disclosure headingLevel={4} title={t('app.threads.inspector.team.details', 'Details')}>
					<dl className="thread-inspector-meta">
						<div>
							<dt>{t('app.threads.inspector.team.reviewer', 'Reviewer')}</dt>
							<dd className="mono">
								{reviewerRole ? `${humanize(reviewerRole)} (${reviewerMode ?? 'peer'})` : '—'}
							</dd>
						</div>
						<div>
							<dt>{t('app.threads.inspector.team.costCap', 'Cost cap per run')}</dt>
							<dd className="mono">{formatCostUsd(profile.maxCostPerRun)}</dd>
						</div>
						<div>
							<dt>{t('app.threads.inspector.team.calls', 'Model calls')}</dt>
							<dd className="mono">{spend ? spend.calls : 0}</dd>
						</div>
					</dl>
				</Disclosure>
			</div>
			<div className="thread-inspector-row-side">
				<StatusChip tone={toneForStatus(availability?.status)}>
					{humanize(availabilityStatus)}
				</StatusChip>
				<span className="thread-inspector-cost mono">
					{spend ? formatCostUsd(spend.costUsd) : '—'}
				</span>
			</div>
		</>
	);
}

/* ---------- Plan ---------- */

function phaseState(isCurrent: boolean, isDone: boolean): 'current' | 'done' | 'upcoming' {
	if (isCurrent) return 'current';
	return isDone ? 'done' : 'upcoming';
}

function activeLoopOf(data: LoopData): LoopRecord | null {
	const sorted = [...data.loops].sort((left, right) => (left.updatedAt < right.updatedAt ? 1 : -1));
	return sorted.find((loop) => loop.status === 'active') ?? sorted[0] ?? null;
}

function PlanPanel({ loop }: { loop: InspectorResource<LoopData> }) {
	const { t, language } = useI18n();
	return (
		<ResourceGate resource={loop}>
			{(data) => {
				const activeLoop = activeLoopOf(data);
				if (!activeLoop) {
					return (
						<EmptyState
							title={t('app.threads.inspector.plan.emptyTitle', 'No product loop yet')}
							body={t(
								'app.threads.inspector.plan.empty',
								'Planning has not started — the loop begins when a goal arrives.',
							)}
						/>
					);
				}
				const currentRank = PRODUCT_LOOP_STATE_ORDER.indexOf(
					activeLoop.state as (typeof PRODUCT_LOOP_STATE_ORDER)[number],
				);
				// Blocked and cancelled both halt the loop: no phase may render as "done".
				const isHalted = activeLoop.state === 'blocked' || activeLoop.state === 'cancelled';
				const nextPhases = PRODUCT_LOOP_PHASES.filter(
					(phase) => PRODUCT_LOOP_STATE_ORDER.indexOf(phase) > currentRank,
				).slice(0, 2);
				const blockedTransitions = data.transitions
					.filter((transition) => transition.toState === 'blocked')
					.slice(-3)
					.reverse();
				const recentTransitions = [...data.transitions].slice(-5).reverse();
				const storiesById = new Map(data.stories.map((story) => [story.id, story]));
				const criteriaByStory = new Map<string, LoopData['acceptanceCriteria']>();
				for (const criterion of data.acceptanceCriteria) {
					const bucket = criteriaByStory.get(criterion.storyId) ?? [];
					bucket.push(criterion);
					criteriaByStory.set(criterion.storyId, bucket);
				}

				return (
					<div className="thread-inspector-stack">
						<header className="thread-inspector-goal-head">
							<h3>{activeLoop.title}</h3>
							<StatusChip tone={loopStateTone(activeLoop.state)}>
								{humanize(activeLoop.state)}
							</StatusChip>
						</header>

						<SectionTitle>{t('app.threads.inspector.plan.phases', 'Phases')}</SectionTitle>
						<ol className="thread-inspector-phase-list">
							{PRODUCT_LOOP_PHASES.map((phase) => {
								const nodeRank = PRODUCT_LOOP_STATE_ORDER.indexOf(phase);
								const isCurrent = phase === activeLoop.state;
								const isDone = !isHalted && currentRank >= 0 && nodeRank < currentRank;
								return (
									<li
										key={phase}
										data-state={phaseState(isCurrent, isDone)}
										aria-current={isCurrent ? 'step' : undefined}
									>
										<span className="mono">{humanize(phase)}</span>
									</li>
								);
							})}
						</ol>

						<SectionTitle>
							{t('app.threads.inspector.plan.next', 'Next expected phases')}
						</SectionTitle>
						{nextPhases.length ? (
							<p className="thread-inspector-subline mono">
								{nextPhases.map(humanize).join(' · ')}
							</p>
						) : (
							<p className="thread-inspector-subline">
								{t('app.threads.inspector.plan.noNext', 'No further phases')}
							</p>
						)}

						<SectionTitle>{t('app.threads.inspector.plan.blockers', 'Blockers')}</SectionTitle>
						{blockedTransitions.length ? (
							<ul className="thread-inspector-list">
								{blockedTransitions.map((transition) => (
									<li className="thread-inspector-row" key={transition.id}>
										<div className="thread-inspector-row-main">
											<strong>{transition.reason || humanize(transition.trigger)}</strong>
											<span className="thread-inspector-subline">
												{formatWhen(transition.createdAt, language)}
											</span>
										</div>
										<div className="thread-inspector-row-side">
											<StatusChip tone="danger">{humanize(transition.toState)}</StatusChip>
										</div>
									</li>
								))}
							</ul>
						) : (
							<p className="thread-inspector-subline">
								{t('app.threads.inspector.plan.noBlockers', 'No blockers recorded.')}
							</p>
						)}

						<Disclosure
							headingLevel={4}
							title={t('app.threads.inspector.plan.transitions', 'Recent transitions')}
							summary={String(data.transitions.length)}
						>
							<ul className="thread-inspector-list">
								{recentTransitions.map((transition) => (
									<li className="thread-inspector-row" key={transition.id}>
										<div className="thread-inspector-row-main">
											<span className="mono">
												{humanize(transition.fromState)} → {humanize(transition.toState)}
											</span>
											<span className="thread-inspector-subline">
												{transition.actor} · {formatWhen(transition.createdAt, language)}
											</span>
										</div>
									</li>
								))}
							</ul>
						</Disclosure>

						<Disclosure
							headingLevel={4}
							title={t('app.threads.inspector.plan.criteria', 'Acceptance criteria')}
							summary={String(data.acceptanceCriteria.length)}
						>
							{data.acceptanceCriteria.length ? (
								<div className="thread-inspector-stack">
									{[...criteriaByStory.entries()].map(([storyId, criteria]) => (
										<div key={storyId}>
											<SectionTitle>
												{storiesById.get(storyId)?.title ?? shortId(storyId)}
											</SectionTitle>
											<ul className="thread-inspector-list">
												{criteria.map((criterion) => (
													<li className="thread-inspector-row" key={criterion.id}>
														<div className="thread-inspector-row-main">
															<span className="thread-inspector-subline">
																{criterion.criterion}
															</span>
														</div>
														<div className="thread-inspector-row-side">
															<StatusChip tone={toneForStatus(criterion.status)}>
																{humanize(criterion.status)}
															</StatusChip>
														</div>
													</li>
												))}
											</ul>
										</div>
									))}
								</div>
							) : (
								<p className="thread-inspector-subline">
									{t('app.threads.inspector.plan.noCriteria', 'No acceptance criteria yet.')}
								</p>
							)}
						</Disclosure>

						<Disclosure
							headingLevel={4}
							title={t('app.threads.inspector.plan.gates', 'Quality gates')}
							summary={String(data.assignmentReviews.length)}
						>
							{data.assignmentReviews.length ? (
								<ul className="thread-inspector-list">
									{data.assignmentReviews.map((review) => (
										<li className="thread-inspector-row" key={review.id}>
											<div className="thread-inspector-row-main">
												<span className="mono">{shortId(review.reviewerAgentId)}</span>
												<span className="thread-inspector-subline">
													{formatWhen(review.createdAt, language)}
												</span>
											</div>
											<div className="thread-inspector-row-side">
												<StatusChip tone={toneForStatus(review.decision || review.status)}>
													{humanize(review.decision || review.status)}
												</StatusChip>
											</div>
										</li>
									))}
								</ul>
							) : (
								<p className="thread-inspector-subline">
									{t('app.threads.inspector.plan.noGates', 'No quality-gate reviews yet.')}
								</p>
							)}
						</Disclosure>
					</div>
				);
			}}
		</ResourceGate>
	);
}

/* ---------- Backlog ---------- */

function BacklogPanel({ loop }: { loop: InspectorResource<LoopData> }) {
	const { t } = useI18n();
	return (
		<ResourceGate resource={loop}>
			{(data) => {
				if (!data.epics.length && !data.stories.length && !data.tasks.length) {
					return (
						<EmptyState
							title={t('app.threads.inspector.backlog.emptyTitle', 'Backlog is empty')}
							body={t(
								'app.threads.inspector.backlog.empty',
								'Stories appear here once planning breaks the goal down.',
							)}
						/>
					);
				}
				const epicIds = new Set(data.epics.map((epic) => epic.id));
				const storiesByEpic = new Map<string, LoopData['stories']>();
				for (const story of data.stories) {
					const key = story.epicId && epicIds.has(story.epicId) ? story.epicId : '';
					const bucket = storiesByEpic.get(key) ?? [];
					bucket.push(story);
					storiesByEpic.set(key, bucket);
				}
				const tasksByStory = new Map<string, LoopData['tasks']>();
				for (const task of data.tasks) {
					const storyKey = task.storyId ?? '';
					const bucket = tasksByStory.get(storyKey) ?? [];
					bucket.push(task);
					tasksByStory.set(storyKey, bucket);
				}
				const orphanStories = storiesByEpic.get('') ?? [];

				return (
					<div className="thread-inspector-stack">
						<p className="thread-inspector-subline mono">
							{t('app.threads.inspector.backlog.epics', 'Epics')} {data.epics.length} ·{' '}
							{t('app.threads.inspector.backlog.stories', 'Stories')} {data.stories.length} ·{' '}
							{t('app.threads.inspector.backlog.tasks', 'Agent tasks')} {data.tasks.length}
						</p>
						<ul className="thread-inspector-list">
							{data.epics.map((epic) => (
								<BacklogEpicRows
									key={epic.id}
									epic={epic}
									stories={storiesByEpic.get(epic.id) ?? []}
									tasksByStory={tasksByStory}
								/>
							))}
							{orphanStories.length ? (
								<BacklogEpicRows epic={null} stories={orphanStories} tasksByStory={tasksByStory} />
							) : null}
						</ul>
					</div>
				);
			}}
		</ResourceGate>
	);
}

function BacklogEpicRows({
	epic,
	stories,
	tasksByStory,
}: {
	epic: LoopData['epics'][number] | null;
	stories: LoopData['stories'];
	tasksByStory: Map<string, LoopData['tasks']>;
}) {
	const { t } = useI18n();
	return (
		<>
			<li className="thread-inspector-row" data-depth="0">
				<div className="thread-inspector-row-main">
					<strong>
						{epic ? epic.title : t('app.threads.inspector.backlog.unassigned', 'Without epic')}
					</strong>
					{epic?.priority ? (
						<span className="thread-inspector-subline mono">{humanize(epic.priority)}</span>
					) : null}
				</div>
				{epic ? (
					<div className="thread-inspector-row-side">
						<StatusChip tone={toneForStatus(epic.status)}>{humanize(epic.status)}</StatusChip>
					</div>
				) : null}
			</li>
			{stories.map((story) => (
				<BacklogStoryRows key={story.id} story={story} tasks={tasksByStory.get(story.id) ?? []} />
			))}
		</>
	);
}

function BacklogStoryRows({
	story,
	tasks,
}: {
	story: LoopData['stories'][number];
	tasks: LoopData['tasks'];
}) {
	return (
		<>
			<li className="thread-inspector-row" data-depth="1">
				<div className="thread-inspector-row-main">
					<strong>{story.title}</strong>
					{story.businessValue ? (
						<span className="thread-inspector-subline">{story.businessValue}</span>
					) : null}
				</div>
				<div className="thread-inspector-row-side">
					<StatusChip tone={toneForStatus(story.status)}>{humanize(story.status)}</StatusChip>
				</div>
			</li>
			{tasks.map((task) => (
				<li className="thread-inspector-row" data-depth="2" key={task.id}>
					<div className="thread-inspector-row-main">
						<span className="thread-inspector-subline">
							<span className="mono">{humanize(task.role)}</span> · {task.title}
						</span>
					</div>
					<div className="thread-inspector-row-side">
						<StatusChip tone={toneForStatus(task.status)}>{humanize(task.status)}</StatusChip>
					</div>
				</li>
			))}
		</>
	);
}

/* ---------- Memory ---------- */

/** One recall category: a titled list when there is data, an honest empty line when not. */
function MemorySection<T>({
	title,
	emptyText,
	items,
	renderItem,
}: {
	title: string;
	emptyText: string;
	items: ReadonlyArray<T>;
	renderItem: (item: T) => ReactNode;
}) {
	return (
		<>
			<SectionTitle>{title}</SectionTitle>
			{items.length ? (
				<ul className="thread-inspector-list">{items.map(renderItem)}</ul>
			) : (
				<p className="thread-inspector-subline">{emptyText}</p>
			)}
		</>
	);
}

function MemoryPanel({
	threadId,
	memory,
}: {
	threadId: string | null;
	memory: InspectorResource<ThreadMemoryRecallResponse>;
}) {
	const { t, language } = useI18n();
	if (!threadId) return <NoThreadState />;
	if (memory.error) return <PanelError onRetry={memory.reload} />;
	if (!memory.data) return <PanelSkeleton />;
	const recall = memory.data;

	return (
		<div className="thread-inspector-stack">
			<p className="thread-inspector-subline">
				{t('app.threads.inspector.memory.summary', 'What AIDO remembers about work like this')} ·{' '}
				{formatWhen(recall.generatedAt, language)}
			</p>

			<MemorySection
				title={t('app.threads.inspector.memory.similar', 'Similar threads')}
				emptyText={t('app.threads.inspector.memory.similarEmpty', 'No similar threads found.')}
				items={recall.similarThreads}
				renderItem={(candidate) => (
					<li className="thread-inspector-row" key={candidate.threadId}>
						<div className="thread-inspector-row-main">
							<strong>{candidate.title}</strong>
							<span className="thread-inspector-subline">{candidate.reason}</span>
						</div>
						<div className="thread-inspector-row-side">
							<StatusChip tone="info">{Math.round(candidate.score * 100)}%</StatusChip>
						</div>
					</li>
				)}
			/>

			<MemorySection
				title={t('app.threads.inspector.memory.decisions', 'Previous decisions')}
				emptyText={t(
					'app.threads.inspector.memory.decisionsEmpty',
					'No decisions from similar work yet.',
				)}
				items={recall.previousDecisions}
				renderItem={(decision) => (
					<li className="thread-inspector-row" key={decision.id}>
						<div className="thread-inspector-row-main">
							<strong>{decision.title || decision.prompt}</strong>
							{decision.resolution ? (
								<span className="thread-inspector-subline">
									{t('app.threads.inspector.memory.resolution', 'Resolution')}:{' '}
									{decision.resolution}
								</span>
							) : null}
							<span className="thread-inspector-subline">
								{decision.threadTitle}
								{decision.threadTitle ? ' · ' : ''}
								{formatWhen(decision.decidedAt ?? decision.createdAt, language)}
							</span>
						</div>
					</li>
				)}
			/>

			<MemorySection
				title={t('app.threads.inspector.memory.evidence', 'Related evidence')}
				emptyText={t(
					'app.threads.inspector.memory.evidenceEmpty',
					'No evidence linked from similar work yet.',
				)}
				items={recall.relatedEvidence}
				renderItem={(evidence) => (
					<li className="thread-inspector-row" key={evidence.id}>
						<div className="thread-inspector-row-main">
							<strong>{evidence.title || humanize(evidence.kind)}</strong>
							<span className="thread-inspector-subline">
								{evidence.threadTitle}
								{evidence.threadTitle ? ' · ' : ''}
								{formatWhen(evidence.createdAt, language)}
							</span>
						</div>
						<div className="thread-inspector-row-side">
							<StatusChip tone="info">{humanize(evidence.kind)}</StatusChip>
						</div>
					</li>
				)}
			/>

			<MemorySection
				title={t('app.threads.inspector.memory.lessons', 'Lessons learned')}
				emptyText={t(
					'app.threads.inspector.memory.lessonsEmpty',
					'No lessons recorded for this project yet.',
				)}
				items={recall.lessonsLearned}
				renderItem={(lesson) => (
					<li className="thread-inspector-row" key={lesson.id}>
						<div className="thread-inspector-row-main">
							<span className="thread-inspector-memory-content">{lesson.content}</span>
							{lesson.matchedKeywords.length ? (
								<span className="thread-inspector-subline mono">
									{lesson.matchedKeywords.join(' · ')}
								</span>
							) : null}
							<span className="thread-inspector-subline">
								{formatWhen(lesson.createdAt, language)}
							</span>
						</div>
					</li>
				)}
			/>

			<MemorySection
				title={t('app.threads.inspector.memory.performance', 'Prior performance issues')}
				emptyText={t(
					'app.threads.inspector.memory.performanceEmpty',
					'No performance passes recorded for this work.',
				)}
				items={recall.performanceIssues}
				renderItem={(issue) => (
					<li className="thread-inspector-row" key={issue.id}>
						<div className="thread-inspector-row-main">
							<span className="thread-inspector-memory-content">{issue.note}</span>
							<span className="thread-inspector-subline">
								{issue.threadTitle}
								{issue.threadTitle ? ' · ' : ''}
								{formatWhen(issue.createdAt, language)}
							</span>
						</div>
					</li>
				)}
			/>

			<MemorySection
				title={t('app.threads.inspector.memory.implemented', 'Functionality already implemented')}
				emptyText={t(
					'app.threads.inspector.memory.implementedEmpty',
					'No resolved similar threads yet.',
				)}
				items={recall.implementedFunctionality}
				renderItem={(done) => (
					<li className="thread-inspector-row" key={done.threadId}>
						<div className="thread-inspector-row-main">
							<strong>{done.title}</strong>
							{done.summary ? (
								<span className="thread-inspector-subline">{done.summary}</span>
							) : null}
							<span className="thread-inspector-subline">
								{formatWhen(done.updatedAt, language)}
							</span>
						</div>
						<div className="thread-inspector-row-side">
							<StatusChip tone="ok">{Math.round(done.score * 100)}%</StatusChip>
						</div>
					</li>
				)}
			/>
		</div>
	);
}

/* ---------- Research ---------- */

type ResearchSourcePayload = {
	id?: string;
	url?: string;
	publisher?: string;
	trustLevel?: string;
	fetchedAt?: string;
};

function researchSources(metadata: unknown): ResearchSourcePayload[] {
	return arrayValue(asRecord(metadata).sources).map((entry) => {
		const record = asRecord(entry);
		return {
			id: textValue(record.id ?? record.sourceId),
			url: textValue(record.url),
			publisher: textValue(record.publisher),
			trustLevel: textValue(record.trustLevel),
			fetchedAt: textValue(record.fetchedAt),
		};
	});
}

function ResearchPanel({
	detail,
	threadId,
}: {
	detail: InspectorResource<ThreadDetail>;
	threadId: string | null;
}) {
	const { t } = useI18n();
	if (!threadId) return <NoThreadState />;
	return (
		<ResourceGate resource={detail}>
			{(data) => {
				const reports = data.artifacts.filter((artifact) => artifact.kind === 'research_report');
				if (!reports.length) {
					return (
						<EmptyState
							title={t('app.threads.inspector.research.emptyTitle', 'No research yet')}
							body={t(
								'app.threads.inspector.research.empty',
								'No research requested for this thread.',
							)}
						/>
					);
				}
				return (
					<div className="thread-inspector-stack">
						{reports.map((artifact) => (
							<ResearchReportCard key={artifact.id} artifact={artifact} />
						))}
					</div>
				);
			}}
		</ResourceGate>
	);
}

function ResearchReportCard({ artifact }: { artifact: ThreadArtifact }) {
	const { t } = useI18n();
	const metadata = asRecord(artifact.metadata);
	const status = textValue(metadata.status) ?? 'research_required';
	const recommendation = asRecord(metadata.recommendation);
	const sources = researchSources(artifact.metadata);
	const discrepancies = arrayValue(metadata.discrepancies);

	return (
		<section className="thread-inspector-research">
			<header className="thread-inspector-goal-head">
				<h3>{artifact.title}</h3>
				<StatusChip tone={toneForStatus(status.replace(/^research_/, ''))}>
					{humanize(status)}
				</StatusChip>
			</header>
			{textValue(recommendation.title) || textValue(recommendation.decision) ? (
				<div className="thread-inspector-row-main">
					<span className="thread-inspector-subline">
						{t('app.threads.research.recommendation', 'Recommendation')}
					</span>
					<strong>{textValue(recommendation.title) ?? ''}</strong>
					<span className="thread-inspector-subline">
						{textValue(recommendation.decision) ?? ''}
					</span>
				</div>
			) : null}
			<Disclosure
				headingLevel={4}
				title={t('app.threads.research.sources', 'Sources')}
				summary={String(sources.length)}
			>
				{sources.length ? (
					<ul className="thread-inspector-list">
						{sources.map((source, index) => (
							<li className="thread-inspector-row" key={source.id ?? source.url ?? `s-${index}`}>
								<div className="thread-inspector-row-main">
									{source.url ? (
										<a href={source.url} target="_blank" rel="noreferrer">
											{source.publisher ?? source.url}
											<ExternalLink
												aria-hidden="true"
												className="thread-inspector-external-icon"
												size={12}
											/>
											<span className="sr-only">
												{t('app.threads.inspector.research.newTab', 'opens in a new tab')}
											</span>
										</a>
									) : (
										<strong>{source.publisher ?? '—'}</strong>
									)}
									<span className="thread-inspector-subline">
										{source.fetchedAt ?? t('app.threads.research.noDate', 'No date')}
									</span>
								</div>
								<div className="thread-inspector-row-side">
									<StatusChip tone={trustTone(source.trustLevel ?? 'untrusted')}>
										{humanize(source.trustLevel ?? 'untrusted')}
									</StatusChip>
								</div>
							</li>
						))}
					</ul>
				) : (
					<p className="thread-inspector-subline">
						{t('app.threads.research.noSources', 'No sources persisted yet.')}
					</p>
				)}
			</Disclosure>
			{discrepancies.length ? (
				<Disclosure
					headingLevel={4}
					title={t('app.threads.research.discrepancies', 'Discrepancies')}
					summary={String(discrepancies.length)}
				>
					<ul className="thread-inspector-list">
						{discrepancies.map((item, index) => {
							const record = asRecord(item);
							return (
								<li className="thread-inspector-row" key={textValue(record.topic) ?? `d-${index}`}>
									<div className="thread-inspector-row-main">
										<strong>{textValue(record.topic) ?? '—'}</strong>
										<span className="thread-inspector-subline">
											{textValue(record.conflictingValues) ?? ''}
										</span>
									</div>
								</li>
							);
						})}
					</ul>
				</Disclosure>
			) : null}
		</section>
	);
}

/* ---------- Artifacts ---------- */

const ARTIFACTS_LIMIT = 30;

function ArtifactsPanel({
	detail,
	threadId,
}: {
	detail: InspectorResource<ThreadDetail>;
	threadId: string | null;
}) {
	const { t, language } = useI18n();
	if (!threadId) return <NoThreadState />;
	return (
		<ResourceGate resource={detail}>
			{(data) => {
				if (!data.artifacts.length) {
					return (
						<EmptyState
							title={t('app.threads.inspector.artifacts.emptyTitle', 'No artifacts yet')}
							body={t(
								'app.threads.inspector.artifacts.empty',
								'Evidence produced by the loop appears here.',
							)}
						/>
					);
				}
				const sorted = [...data.artifacts].sort((left, right) =>
					left.createdAt < right.createdAt ? 1 : -1,
				);
				const visible = sorted.slice(0, ARTIFACTS_LIMIT);
				const hidden = sorted.length - visible.length;
				return (
					<div className="thread-inspector-stack">
						<m.ul
							className="thread-inspector-list"
							variants={listStagger}
							initial="initial"
							animate="animate"
						>
							{visible.map((artifact) => (
								<m.li className="thread-inspector-row" key={artifact.id} variants={cardTransition}>
									<div className="thread-inspector-row-main">
										<strong>{artifact.title}</strong>
										<span className="thread-inspector-subline">
											{formatWhen(artifact.createdAt, language)}
										</span>
									</div>
									<div className="thread-inspector-row-side">
										<span className="thread-inspector-subline mono">{humanize(artifact.kind)}</span>
									</div>
								</m.li>
							))}
						</m.ul>
						{hidden > 0 ? (
							<p className="thread-inspector-subline">
								{t(
									'app.threads.inspector.artifacts.more',
									'{count} older artifacts not shown',
								).replace('{count}', String(hidden))}
							</p>
						) : null}
					</div>
				);
			}}
		</ResourceGate>
	);
}

/* ---------- Settings ---------- */

function settingValueText(value: unknown): string {
	if (value == null) return '—';
	if (typeof value === 'boolean') return value ? 'on' : 'off';
	if (typeof value === 'object') return JSON.stringify(value);
	return String(value);
}

function SettingsPanel({ settings }: { settings: InspectorResource<SettingsResponse> }) {
	const { t } = useI18n();
	return (
		<ResourceGate resource={settings}>
			{(data) => {
				if (!data.general.length && !data.project.length) {
					return (
						<EmptyState
							title={t('app.threads.inspector.settings.emptyTitle', 'No settings resolved')}
							body={t(
								'app.threads.inspector.settings.empty',
								'No settings resolved for this project.',
							)}
						/>
					);
				}
				return (
					<div className="thread-inspector-stack">
						<SettingsSection
							title={t('app.threads.inspector.settings.project', 'Project')}
							rows={data.project}
						/>
						<SettingsSection
							title={t('app.threads.inspector.settings.general', 'General')}
							rows={data.general}
						/>
						<p className="thread-inspector-subline">
							{t(
								'app.threads.inspector.settings.hint',
								'Read-only view — change values from Settings or the composer.',
							)}
						</p>
					</div>
				);
			}}
		</ResourceGate>
	);
}

function SettingsSection({ title, rows }: { title: string; rows: ResolvedSetting[] }) {
	const { t } = useI18n();
	if (!rows.length) return null;
	return (
		<div>
			<SectionTitle>{title}</SectionTitle>
			<ul className="thread-inspector-list">
				{rows.map((setting) => (
					<li className="thread-inspector-row" key={`${setting.key}-${setting.source}`}>
						<div className="thread-inspector-row-main">
							<strong>{setting.labelKey ? t(setting.labelKey, setting.key) : setting.key}</strong>
							<span className="thread-inspector-subline mono">{setting.key}</span>
							{setting.inherited ? (
								<span className="thread-inspector-subline">
									{t('app.threads.inspector.settings.inherited', 'inherited')}
								</span>
							) : null}
						</div>
						<div className="thread-inspector-row-side">
							<span className="mono">{settingValueText(setting.value)}</span>
							<StatusChip tone="info">{setting.source}</StatusChip>
						</div>
					</li>
				))}
			</ul>
		</div>
	);
}
