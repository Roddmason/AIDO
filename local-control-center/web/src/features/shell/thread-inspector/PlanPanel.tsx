/**
 * Plan tab of the Thread Inspector: blocked-loop banner with its clearing action, phase timeline, transitions, acceptance criteria and quality gates for the thread's product loop.
 * @author Rodrigo Mason
 */
import { AlertTriangle } from 'lucide-react';
import type { ThreadAgentEvent } from '../../../api/types';
import { Button, Disclosure, EmptyState, StatusChip } from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';
import { shortId, toneForStatus } from '../../../lib/format';
import { PRODUCT_LOOP_PHASES, PRODUCT_LOOP_STATE_ORDER } from '../../workbench/productLoopModel';
import type { InspectorResource } from '../useThreadInspectorData';
import {
	formatWhen,
	humanize,
	type LoopData,
	loopStateTone,
	threadLoopOf,
} from './inspectorValues';
import { ResourceGate, SectionTitle } from './panelStates';

function phaseState(isCurrent: boolean, isDone: boolean): 'current' | 'done' | 'upcoming' {
	if (isCurrent) return 'current';
	return isDone ? 'done' : 'upcoming';
}

/**
 * The blocked loop, stated first and paired with the one control that clears it. The repair itself is
 * never re-implemented here: when the thread has a persisted remediation the action moves focus to that
 * card — the only place carrying the backend's recommended fix — and otherwise it falls back to opening
 * the configuration, exactly as the card does when the backend offers no runnable action.
 */
function PlanBlockerBanner({
	reason,
	when,
	hasRepairCard,
	onFocusRepair,
	onOpenSettings,
}: {
	reason: string;
	when: string;
	hasRepairCard: boolean;
	onFocusRepair: () => void;
	onOpenSettings: (section?: string) => void;
}) {
	const { t } = useI18n();
	return (
		<section className="thread-plan-blocker">
			<div className="thread-plan-blocker-head">
				<AlertTriangle aria-hidden="true" size={15} />
				<strong>{t('app.threads.inspector.plan.blockedTitle', 'Loop blocked')}</strong>
			</div>
			<p className="thread-plan-blocker-reason">{reason}</p>
			<span className="thread-inspector-subline">{when}</span>
			<Button
				variant="primary"
				className="thread-plan-blocker-cta"
				onClick={hasRepairCard ? onFocusRepair : () => onOpenSettings('providers-cli')}
			>
				{hasRepairCard
					? t('app.threads.inspector.plan.blockedCta', 'Go to the repair')
					: t('app.threads.remediation.action.openConfiguration', 'Open configuration')}
			</Button>
		</section>
	);
}

export function PlanPanel({
	loop,
	events,
	hasRepairCard,
	onFocusRepair,
	onOpenSettings,
}: {
	loop: InspectorResource<LoopData>;
	events: ReadonlyArray<ThreadAgentEvent> | undefined;
	hasRepairCard: boolean;
	onFocusRepair: () => void;
	onOpenSettings: (section?: string) => void;
}) {
	const { t, language } = useI18n();
	return (
		<ResourceGate resource={loop}>
			{(data) => {
				const activeLoop = threadLoopOf(data, events);
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
				const isHalted = activeLoop.state === 'blocked' || activeLoop.state === 'cancelled';
				const blockedTransitions = data.transitions
					.filter((transition) => transition.toState === 'blocked')
					.slice(-3)
					.reverse();
				const isBlocked = activeLoop.state === 'blocked';
				const currentBlock = isBlocked ? blockedTransitions[0] : undefined;
				const earlierBlocks = currentBlock ? blockedTransitions.slice(1) : blockedTransitions;
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

						{isBlocked ? (
							<PlanBlockerBanner
								reason={
									currentBlock?.reason ||
									(currentBlock ? humanize(currentBlock.trigger) : '') ||
									t(
										'app.threads.inspector.plan.blockedNoReason',
										'The loop is blocked and recorded no machine reason.',
									)
								}
								when={formatWhen(currentBlock?.createdAt ?? activeLoop.updatedAt, language)}
								hasRepairCard={hasRepairCard}
								onFocusRepair={onFocusRepair}
								onOpenSettings={onOpenSettings}
							/>
						) : null}

						<SectionTitle>{t('app.threads.inspector.plan.phases', 'Phases')}</SectionTitle>
						<ol className="thread-plan-timeline">
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
										<span aria-hidden="true" className="thread-plan-timeline-node" />
										<span className="mono">{humanize(phase)}</span>
									</li>
								);
							})}
						</ol>

						{earlierBlocks.length ? (
							<Disclosure
								headingLevel={4}
								title={t('app.threads.inspector.plan.blockers', 'Blockers')}
								summary={String(earlierBlocks.length)}
							>
								<ul className="thread-inspector-list">
									{earlierBlocks.map((transition) => (
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
							</Disclosure>
						) : null}

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
