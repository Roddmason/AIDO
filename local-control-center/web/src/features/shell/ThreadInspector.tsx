/**
 * Thread Inspector: the "AI manager" console rendered in the shell's right inspector pane for
 * the threads area.
 *
 * Nine tabs turn the thread from a plain chat into an operations console over real data:
 * Goal (thread objective + status), Team (agent roster with availability, assignment, blocked
 * reason, reviewer policy and recorded spend), Plan (a blocked loop stated first with the action
 * that clears it, then the phase timeline, acceptance criteria and quality gates), Backlog
 * (epics → stories → agent tasks), Cost (what the run spent, on which model and why, with the
 * controls that change the next run), Memory (what
 * AIDO recalls about work like this: similar threads, previous decisions, related evidence,
 * lessons learned, prior performance passes and functionality already implemented, all from
 * the thread memory recall endpoint), Research (report artifacts with sources and trust levels),
 * Artifacts (all thread artifacts) and Settings (resolved read-only configuration). A pinned
 * vitals strip above the tabs keeps thread status, loop state and pending decisions visible
 * on every tab. Every tab renders honest loading/error/empty/success states; data loads via
 * {@link useThreadInspectorData}. Read-only except for Cost, the one tab whose job is to change
 * what the next run may spend — every other mutation stays in the composer.
 * @author Rodrigo Mason
 */
import {
	BrainCircuit,
	CircleDollarSign,
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
import { useCallback, useRef, useState } from 'react';

import type { Overview, Project, ThreadDetail } from '../../api/types';
import type { Mutate } from '../../app/routes';
import { StatusChip, Tabs } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { threadStatusTone } from '../../lib/format';
import { crossfade } from '../../motion/variants';
import { ThreadBlockerList } from './ThreadBlockerCard';
import { ThreadCostPanel } from './ThreadCostPanel';
import { ArtifactsPanel } from './thread-inspector/ArtifactsPanel';
import { BacklogPanel } from './thread-inspector/BacklogPanel';
import { GoalPanel } from './thread-inspector/GoalPanel';
import {
	humanize,
	type LoopData,
	loopStateTone,
	threadLoopOf,
} from './thread-inspector/inspectorValues';
import { MemoryPanel } from './thread-inspector/MemoryPanel';
import { PlanPanel } from './thread-inspector/PlanPanel';
import { NoThreadState, ResourceGate } from './thread-inspector/panelStates';
import { ResearchPanel } from './thread-inspector/ResearchPanel';
import { SettingsPanel } from './thread-inspector/SettingsPanel';
import { TeamPanel } from './thread-inspector/TeamPanel';
import {
	type InspectorResource,
	type ThreadInspectorTab,
	useThreadInspectorData,
} from './useThreadInspectorData';
import { useThreadRefresh } from './useThreadRefresh';
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
		id: 'cost',
		icon: CircleDollarSign,
		labelKey: 'app.threads.inspector.tab.cost',
		fallback: 'Cost',
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

/** The threads-area inspector console: nine lazily loaded views over real data. */
export function ThreadInspector({
	overview,
	project,
	threadId,
	mutate,
	onOpenSettings,
}: ThreadInspectorProps) {
	const { t } = useI18n();
	const [tab, setTab] = useState<ThreadInspectorTab>('goal');
	const refresh = useThreadRefresh();
	const threadSnapshot = overview.threads.find((thread) => thread.id === threadId);
	const refreshSignal = `${refresh.threadId === threadId ? refresh.revision : 0}:${threadSnapshot?.updatedAt ?? ''}:${threadSnapshot?.status ?? ''}`;
	const { detail, loop, roster, cost, memory, settings } = useThreadInspectorData(
		project.id,
		threadId,
		tab,
		refreshSignal,
	);
	const remediations = useThreadRemediations(threadId, mutate, refreshSignal);
	const hasRepairCard = remediations.cards.length > 0 || remediations.error;
	const repairRef = useRef<HTMLDivElement>(null);
	const focusRepair = useCallback(() => {
		const node = repairRef.current;
		if (!node) return;
		node.scrollIntoView({ block: 'nearest' });
		node.focus();
	}, []);

	const tabs = TAB_DEFS.map((def) => {
		const Icon = def.icon;
		const label = t(def.labelKey, def.fallback);
		return {
			id: def.id,
			label: (
				<span className="thread-inspector-tab-label" title={label} data-label={label}>
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
		panel = (
			<TeamPanel
				overview={overview}
				loop={loop}
				roster={roster}
				projectId={project.id}
				threadId={threadId}
				detail={detail}
				onOpenSettings={onOpenSettings}
			/>
		);
	} else if (tab === 'plan') {
		panel = (
			<PlanPanel
				loop={loop}
				events={detail.data?.events}
				hasRepairCard={hasRepairCard}
				onFocusRepair={focusRepair}
				onOpenSettings={onOpenSettings}
			/>
		);
	} else if (tab === 'backlog') {
		panel = <BacklogPanel loop={loop} />;
	} else if (tab === 'cost') {
		panel = threadId ? (
			<ResourceGate resource={cost}>
				{(data) => (
					<ThreadCostPanel
						threadId={threadId}
						projectId={project.id}
						snapshot={data.costPerformance}
						remediations={remediations}
						mutate={mutate}
						onPolicyChanged={cost.reload}
					/>
				)}
			</ResourceGate>
		) : (
			<NoThreadState />
		);
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
			{hasRepairCard ? (
				<div className="thread-inspector-repair" ref={repairRef} tabIndex={-1}>
					<ThreadBlockerList handle={remediations} onOpenSettings={onOpenSettings} />
				</div>
			) : null}
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
	const activeLoop = loop.data ? threadLoopOf(loop.data, detail.data?.events) : null;
	const pendingDecisions =
		detail.data?.decisions.filter((decision) => decision.status === 'pending').length ?? 0;
	if (!thread && !activeLoop) return null;
	return (
		// biome-ignore lint/a11y/useSemanticElements: labeled group of read-only status chips; role=group + aria-label is valid ARIA — the native <fieldset> is for form controls, not this.
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
