/**
 * Team tab of the Thread Inspector: the effective team card plus the agent roster grouped by state, with assignment, spend, reviewer policy and recovery actions derived from real data.
 * @author Rodrigo Mason
 */
import { AlertTriangle, SlidersHorizontal } from 'lucide-react';
import { m } from 'motion/react';
import { useMemo } from 'react';
import type { AgentProfilesResponse } from '../../../api/client';
import type { AgentProfile, Overview, ThreadDetail } from '../../../api/types';
import {
	Button,
	Disclosure,
	EmptyState,
	StatusChip,
	type StatusTone,
} from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';
import { formatCostUsd, shortId } from '../../../lib/format';
import { cardTransition, listStagger } from '../../../motion/variants';
import { EffectiveTeamCard } from '../EffectiveTeamCard';
import {
	AGENT_STATE_ORDER,
	type AgentAssignment,
	type AgentState,
	ATTENTION_STATES,
	classifyAgentState,
	describeRuntimeSelection,
} from '../teamRoster';
import type { InspectorResource } from '../useThreadInspectorData';
import { asRecord, humanize, type LoopData, textValue } from './inspectorValues';
import { ResourceGate } from './panelStates';

/** Visual tone for each roster state's chip. */
const STATE_TONE: Record<AgentState, StatusTone> = {
	active: 'ok',
	waiting: 'pending',
	blocked: 'danger',
	available: 'info',
	unconfigured: 'warn',
};

/** Localised label for a roster state, shown on the group heading and each agent's state chip. */
function stateLabel(t: (key: string, fallback: string) => string, state: AgentState): string {
	switch (state) {
		case 'active':
			return t('app.threads.inspector.team.state.active', 'Active');
		case 'waiting':
			return t('app.threads.inspector.team.state.waiting', 'Waiting');
		case 'blocked':
			return t('app.threads.inspector.team.state.blocked', 'Blocked');
		case 'available':
			return t('app.threads.inspector.team.state.available', 'Available');
		default:
			return t('app.threads.inspector.team.state.unconfigured', 'Unconfigured');
	}
}

/** One agent resolved for the roster: its profile, derived state, current assignment and spend. */
type AgentRosterEntry = {
	profile: AgentProfile;
	state: AgentState;
	assignment: AgentAssignment | null;
	spend: { knownCostUsd: number; calls: number; unknownCalls: number } | null;
};

/**
 * The Team tab, gated on the agent-profiles roster. The overview snapshot is deliberately not the
 * roster source: it serves profile rows straight from the repository, without resolving them against
 * the runtimes detected on this machine, so every agent would arrive with the `unknown` availability
 * default. Overview still supplies the spend ledger (agent runs joined to model calls).
 */
export function TeamPanel({
	overview,
	loop,
	roster,
	projectId,
	threadId,
	detail,
	onOpenSettings,
}: {
	overview: Overview;
	loop: InspectorResource<LoopData>;
	roster: InspectorResource<AgentProfilesResponse>;
	projectId: string;
	threadId: string | null;
	detail: InspectorResource<ThreadDetail>;
	onOpenSettings: (section?: string) => void;
}) {
	return (
		<>
			<EffectiveTeamCard
				projectId={projectId}
				threadId={threadId}
				threadMetadata={detail.data?.thread.metadata}
			/>
			<ResourceGate resource={roster}>
				{(data) => (
					<TeamRoster
						profiles={data.agentProfiles}
						overview={overview}
						loop={loop}
						onOpenSettings={onOpenSettings}
					/>
				)}
			</ResourceGate>
		</>
	);
}

/**
 * The roster as a manager console: agents grouped by state (active, waiting, blocked, available,
 * unconfigured) instead of a flat wall of rows. The states that demand attention lead and stay open;
 * the idle groups always render collapsed behind their count, so a bench of twenty-odd unwired agents
 * can never bury the live work. State, assignment, spend and the why-selected rationale are all
 * derived from real data — never fabricated — and a blocked agent carries an inline recovery action
 * that opens the Settings section that fixes it.
 */
function TeamRoster({
	profiles,
	overview,
	loop,
	onOpenSettings,
}: {
	profiles: AgentProfile[];
	overview: Overview;
	loop: InspectorResource<LoopData>;
	onOpenSettings: (section?: string) => void;
}) {
	const { t } = useI18n();
	const unknownCostLabel = t('app.runtime.card.unknown', 'unknown');

	const spendByProfile = useMemo(() => {
		const profileByRun = new Map<string, string>();
		for (const run of overview.agentRuns) {
			const profileId = textValue(asRecord(run.metadata).agentProfileId);
			if (profileId) profileByRun.set(run.id, profileId);
		}
		const totals = new Map<string, { knownCostUsd: number; calls: number; unknownCalls: number }>();
		for (const call of overview.modelCalls) {
			if (!call.agentRunId) continue;
			const profileId = profileByRun.get(call.agentRunId);
			if (!profileId) continue;
			const entry = totals.get(profileId) ?? { knownCostUsd: 0, calls: 0, unknownCalls: 0 };
			if (typeof call.costUsd === 'number' && Number.isFinite(call.costUsd)) {
				entry.knownCostUsd += call.costUsd;
			} else {
				entry.unknownCalls += 1;
			}
			entry.calls += 1;
			totals.set(profileId, entry);
		}
		return totals;
	}, [overview.agentRuns, overview.modelCalls]);

	const assignmentByAgent = useMemo(() => {
		const map = new Map<string, AgentAssignment>();
		if (!loop.data) return map;
		const tasksById = new Map(loop.data.tasks.map((task) => [task.id, task]));
		for (const assignment of loop.data.assignments) {
			if (assignment.releasedAt || map.has(assignment.agentId)) continue;
			map.set(assignment.agentId, {
				taskTitle: tasksById.get(assignment.taskId)?.title ?? shortId(assignment.taskId),
				status: assignment.status,
				artifactId: assignment.canonicalArtifactId ?? '',
			});
		}
		return map;
	}, [loop.data]);

	if (profiles.length === 0) {
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

	const entries: AgentRosterEntry[] = profiles.map((profile) => {
		const assignment = assignmentByAgent.get(profile.id) ?? null;
		return {
			profile,
			assignment,
			state: classifyAgentState(profile, assignment),
			spend: spendByProfile.get(profile.id) ?? null,
		};
	});

	const grouped = new Map<AgentState, AgentRosterEntry[]>(
		AGENT_STATE_ORDER.map((state) => [state, [] as AgentRosterEntry[]]),
	);
	for (const entry of entries) grouped.get(entry.state)?.push(entry);
	const countFor = (state: AgentState) => grouped.get(state)?.length ?? 0;

	const activeCount = countFor('active');
	const blockedCount = countFor('blocked');
	const unconfiguredCount = countFor('unconfigured');
	const spendEntries = entries.flatMap((entry) => (entry.spend ? [entry.spend] : []));
	const hasUnknownSpend = spendEntries.some((item) => item.unknownCalls > 0);
	const totalSpend = spendEntries.reduce((sum, item) => sum + item.knownCostUsd, 0);
	const totalSpendLabel = hasUnknownSpend ? unknownCostLabel : formatCostUsd(totalSpend);

	return (
		<div className="thread-inspector-stack thread-team">
			{/* biome-ignore lint/a11y/useSemanticElements: labeled group of read-only status chips; role=group + aria-label is valid ARIA — the native <fieldset> is for form controls, not this. */}
			<div
				className="thread-inspector-summary"
				role="group"
				aria-label={t('app.threads.inspector.team.summaryLabel', 'Team overview')}
			>
				<span className="thread-inspector-subline mono">
					{t('app.threads.inspector.team.total', 'Agents')} {profiles.length} ·{' '}
					{t('app.threads.inspector.team.working', 'Working')} {activeCount} ·{' '}
					{t('app.threads.inspector.team.spend', 'Spend')} {totalSpendLabel}
				</span>
				{blockedCount > 0 ? (
					<StatusChip tone="danger">
						{t('app.threads.inspector.team.blockedCount', 'Blocked')} {blockedCount}
					</StatusChip>
				) : null}
				{unconfiguredCount > 0 ? (
					<StatusChip tone="warn">
						{t('app.threads.inspector.team.state.unconfigured', 'Unconfigured')} {unconfiguredCount}
					</StatusChip>
				) : null}
			</div>
			<div className="thread-team-groups">
				{AGENT_STATE_ORDER.map((state) => (
					<AgentGroup
						key={state}
						state={state}
						entries={grouped.get(state) ?? []}
						collapsible={!ATTENTION_STATES.has(state)}
						onOpenSettings={onOpenSettings}
					/>
				))}
			</div>
		</div>
	);
}

/**
 * One roster state as a section. Active/waiting/blocked stay expanded under a plain heading; the idle
 * available/unconfigured groups render collapsed inside a Disclosure that still states their count,
 * so a large bench never buries the live work and nothing is hidden — one click reveals it.
 * Empty groups render nothing rather than a fabricated placeholder.
 */
function AgentGroup({
	state,
	entries,
	collapsible,
	onOpenSettings,
}: {
	state: AgentState;
	entries: AgentRosterEntry[];
	collapsible: boolean;
	onOpenSettings: (section?: string) => void;
}) {
	const { t } = useI18n();
	if (entries.length === 0) return null;
	const label = stateLabel(t, state);
	const list = (
		<m.ul
			className="thread-inspector-list"
			variants={listStagger}
			initial="initial"
			animate="animate"
		>
			{entries.map((entry) => (
				<m.li
					className="thread-inspector-row"
					key={entry.profile.id}
					variants={cardTransition}
					data-state={entry.state}
					data-active={entry.state === 'active' ? 'true' : undefined}
				>
					<TeamAgentCard entry={entry} onOpenSettings={onOpenSettings} />
				</m.li>
			))}
		</m.ul>
	);

	if (collapsible) {
		return (
			<section className="thread-team-group" data-group={state}>
				<Disclosure headingLevel={4} title={label} summary={String(entries.length)}>
					{list}
				</Disclosure>
			</section>
		);
	}

	return (
		<section className="thread-team-group" data-group={state}>
			<h4 className="thread-inspector-section-title thread-team-group-title">
				<span>{label}</span>
				<span className="thread-team-group-count mono">{entries.length}</span>
			</h4>
			{list}
		</section>
	);
}

/**
 * One agent as a manager card: name, blocked callout, role/runtime/provider, current assignment and an
 * inline recovery action when it needs a fix, with the why-selected rationale, current artifact,
 * reviewer policy and cost/token caps behind a Details disclosure. Every field renders an honest "—"
 * when its data is absent rather than implying a runtime the agent does not have.
 */
function TeamAgentCard({
	entry,
	onOpenSettings,
}: {
	entry: AgentRosterEntry;
	onOpenSettings: (section?: string) => void;
}) {
	const { t } = useI18n();
	const unknownCostLabel = t('app.runtime.card.unknown', 'unknown');
	const { profile, state, assignment, spend } = entry;
	const availability = profile.runtimeAvailability;
	const reviewer = asRecord(profile.reviewerPolicy);
	const reviewerRole = textValue(reviewer.reviewerRole);
	const reviewerMode = textValue(reviewer.mode);
	const selection = describeRuntimeSelection(profile);
	const disabledForProject = profile.projectOverride?.status === 'disabled';
	const needsFix = state === 'blocked' || state === 'unconfigured';
	const fixSection = disabledForProject ? 'team' : 'providers-cli';

	return (
		<>
			<div className="thread-inspector-row-main">
				<strong>{profile.name}</strong>
				<span className="thread-inspector-subline mono">
					{humanize(profile.role)} · {profile.runtimeType}
					{availability?.selectedProviderId ? ` · ${availability.selectedProviderId}` : ''}
				</span>
				{availability?.blockedReason ? (
					<span
						className="thread-inspector-blocked"
						data-tone={state === 'blocked' ? 'danger' : 'warn'}
					>
						<AlertTriangle aria-hidden="true" size={13} />
						{stateLabel(t, state)}: {availability.blockedReason}
					</span>
				) : null}
				{disabledForProject ? (
					<span className="thread-inspector-blocked" data-tone="danger">
						<AlertTriangle aria-hidden="true" size={13} />
						{t('app.threads.inspector.team.disabledForProject', 'Disabled for this project')}
					</span>
				) : null}
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
				{needsFix ? (
					<Button
						variant="secondary"
						className="thread-team-fix"
						icon={<SlidersHorizontal aria-hidden="true" size={14} />}
						onClick={() => onOpenSettings(fixSection)}
					>
						{state === 'blocked'
							? t('app.threads.inspector.team.fixBlocked', 'Resolve blocker')
							: t('app.threads.inspector.team.fixUnconfigured', 'Configure runtime')}
					</Button>
				) : null}
				<Disclosure headingLevel={4} title={t('app.threads.inspector.team.details', 'Details')}>
					<dl className="thread-inspector-meta">
						<div>
							<dt>{t('app.threads.inspector.team.whySelected', 'Why selected')}</dt>
							<dd className="thread-inspector-subline">
								{selection ? (
									<>
										{selection.provider ? (
											<span className="mono">{selection.provider}</span>
										) : (
											<span>
												{t(
													'app.threads.inspector.team.selectionPending',
													'Awaiting runtime selection',
												)}
											</span>
										)}
										{!selection.provider && selection.candidates.length ? (
											<>
												{' · '}
												{t('app.threads.inspector.team.selectionCandidates', 'candidates')}:{' '}
												<span className="mono">{selection.candidates.join(', ')}</span>
											</>
										) : null}
										{selection.via ? (
											<>
												{' · '}
												{t('app.threads.inspector.team.selectionVia', 'via')}{' '}
												<span className="mono">{selection.via}</span>
											</>
										) : null}
										{selection.capabilities.length ? (
											<>
												{' · '}
												{t('app.threads.inspector.team.selectionCaps', 'capabilities')}:{' '}
												<span className="mono">{selection.capabilities.join(', ')}</span>
											</>
										) : null}
									</>
								) : (
									'—'
								)}
							</dd>
						</div>
						<div>
							<dt>{t('app.threads.inspector.team.currentArtifact', 'Current artifact')}</dt>
							<dd className="mono">
								{assignment?.artifactId ? shortId(assignment.artifactId) : '—'}
							</dd>
						</div>
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
							<dt>{t('app.threads.inspector.team.tokenCap', 'Token cap per run')}</dt>
							<dd className="mono">
								{Number.isFinite(profile.maxTokensPerRun)
									? profile.maxTokensPerRun.toLocaleString()
									: '—'}
							</dd>
						</div>
						<div>
							<dt>{t('app.threads.inspector.team.calls', 'Model calls')}</dt>
							<dd className="mono">{spend ? spend.calls : 0}</dd>
						</div>
					</dl>
				</Disclosure>
			</div>
			<div className="thread-inspector-row-side">
				<StatusChip tone={STATE_TONE[state]}>{stateLabel(t, state)}</StatusChip>
				<span className="thread-inspector-cost mono">
					{spend
						? spend.unknownCalls > 0
							? unknownCostLabel
							: formatCostUsd(spend.knownCostUsd)
						: '—'}
				</span>
			</div>
		</>
	);
}
