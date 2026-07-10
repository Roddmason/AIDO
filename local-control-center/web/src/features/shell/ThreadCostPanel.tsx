/**
 * Cost tab of the Thread Inspector: turns spend into an operational decision instead of a receipt.
 *
 * Reads the thread's `/cost-performance` snapshot and states, in the order an operator asks them:
 * which model ran and why it was chosen, estimated vs actual cost, how many calls reported real
 * tokens and latency, how much rework the loop paid for, and whether a strictly cheaper model with a
 * known price existed. Every unknown renders as the word "unknown" — never as `$0`, `0 tokens` or
 * `0 ms` — because a fabricated zero is the one number that would make the wrong decision look safe.
 *
 * Below the evidence sit the controls that change the next run: the team mode (economy / balanced /
 * critical), a force-local switch, and "allow premium once". The first two write project settings the
 * ThreadCoordinator stamps onto the next queued run. The third never invents an approval: it executes
 * the backend's own pending `approve_resource_decision` remediation, and stays disabled with an honest
 * reason when no premium selection is actually waiting for a human.
 * @author Rodrigo Mason
 */

import { CircleDollarSign, Cpu, ShieldCheck, TrendingDown } from 'lucide-react';
import type { ReactNode } from 'react';
import { useCallback, useState } from 'react';

import { putSetting, type ThreadCostPerformanceRecord } from '../../api/client';
import type { Mutate } from '../../app/routes';
import {
	Button,
	Checkbox,
	Disclosure,
	EmptyState,
	SegmentedControl,
	StatusChip,
	type StatusTone,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { formatCostUsd } from '../../lib/format';
import type { BlockerActionModel } from './remediationPresentation';
import type { ThreadRemediationsHandle } from './useThreadRemediations';

/** The three modes this surface exposes; `maximum` stays in Settings — it is not a cost decision. */
const COST_MODES = ['economy', 'balanced', 'critical'] as const;
type CostMode = (typeof COST_MODES)[number];

const TEAM_MODE_KEY = 'project.loop.teamMode';
const FORCE_LOCAL_KEY = 'project.routing.forceLocal';

type CostPanelProps = {
	threadId: string | null;
	projectId: string;
	snapshot: ThreadCostPerformanceRecord;
	remediations: ThreadRemediationsHandle;
	mutate: Mutate;
	/** Refetches the snapshot after a control changes the policy behind it. */
	onPolicyChanged: () => void;
};

/** Finds the backend's pending premium approval, if the loop actually raised one. */
function premiumApprovalAction(remediations: ThreadRemediationsHandle): BlockerActionModel | null {
	for (const card of remediations.cards) {
		for (const action of card.actions) {
			if (
				action.remediation?.actionType === 'approve_resource_decision' &&
				action.remediation.status === 'pending'
			) {
				return action;
			}
		}
	}
	return null;
}

/** A metric whose value may legitimately be unknown; renders the word, never a zero. */
function Metric({ term, children }: { term: string; children: ReactNode }) {
	return (
		<div>
			<dt>{term}</dt>
			<dd className="mono">{children}</dd>
		</div>
	);
}

function modeTone(mode: CostMode | string): StatusTone {
	if (mode === 'economy') return 'ok';
	if (mode === 'critical') return 'warn';
	return 'info';
}

/** The Cost tab: evidence first, then the controls that change the next run. */
export function ThreadCostPanel({
	threadId,
	projectId,
	snapshot,
	remediations,
	mutate,
	onPolicyChanged,
}: CostPanelProps) {
	const { t } = useI18n();
	const unknown = t('app.runtime.card.unknown', 'unknown');
	const [busy, setBusy] = useState(false);

	const writeSetting = useCallback(
		async (key: string, value: string | boolean) => {
			if (busy) return;
			setBusy(true);
			try {
				await mutate(
					(token) => putSetting(key, { scope: 'project', scopeId: projectId, value }, token),
					{ awaitRefresh: false },
				);
				onPolicyChanged();
			} finally {
				setBusy(false);
			}
		},
		[busy, mutate, projectId, onPolicyChanged],
	);

	if (!threadId) {
		return (
			<EmptyState
				title={t('app.threads.inspector.cost.noThreadTitle', 'No live thread')}
				body={t(
					'app.threads.inspector.cost.noThreadBody',
					'Select a thread to inspect what its run cost.',
				)}
			/>
		);
	}

	const {
		budgetUsed,
		cost,
		tokens,
		latency,
		modelChosen,
		cheaperAlternative,
		qualityRework,
		policy,
	} = snapshot;
	const premiumAction = premiumApprovalAction(remediations);
	const mode: CostMode | string = policy.mode;
	const costUnknown = cost.actualCostUsd == null && cost.estimatedCostUsd == null;

	return (
		<div className="thread-inspector-stack">
			<div
				className="thread-inspector-summary"
				role="group"
				aria-label={t('app.threads.inspector.cost.summaryLabel', 'Cost decision')}
			>
				<StatusChip tone={modeTone(mode)}>
					{t(`app.settings.enum.teamMode.${mode}`, mode)}
				</StatusChip>
				{policy.forceLocal ? (
					<StatusChip tone="ok">
						{t('app.threads.inspector.cost.localOnly', 'Local only')}
					</StatusChip>
				) : null}
				{policy.approvalRequired ? (
					<StatusChip tone="pending">
						{t('app.threads.inspector.cost.approvalPending', 'Approval required')}
					</StatusChip>
				) : null}
				<span className="thread-inspector-subline mono">
					{t('app.threads.inspector.cost.spent', 'Spent')}{' '}
					{budgetUsed.usedUsd == null ? unknown : formatCostUsd(budgetUsed.usedUsd)}
					{budgetUsed.perRunCapUsd != null ? ` / ${formatCostUsd(budgetUsed.perRunCapUsd)}` : ''}
				</span>
			</div>

			{!snapshot.hasData ? (
				<EmptyState
					title={t('app.threads.inspector.cost.emptyTitle', 'Nothing has run yet')}
					body={t(
						'app.threads.inspector.cost.empty',
						'Cost, tokens and latency appear once this thread executes. The controls below still set what the next run may spend.',
					)}
				/>
			) : (
				<>
					<section className="thread-cost-model">
						<div className="thread-cost-model-head">
							<Cpu aria-hidden="true" size={15} />
							<strong className="mono">
								{modelChosen?.model ??
									t('app.threads.inspector.cost.noModel', 'No model was selected')}
							</strong>
							{modelChosen?.runtime ? (
								<StatusChip tone="info">{modelChosen.runtime}</StatusChip>
							) : null}
						</div>
						<p className="thread-inspector-subline">
							{snapshot.reasonSelected ??
								t(
									'app.threads.inspector.cost.noReason',
									'The router recorded no rationale for this selection.',
								)}
						</p>
					</section>

					<dl className="thread-inspector-meta">
						<Metric term={t('app.threads.inspector.cost.estimated', 'Estimated cost')}>
							{cost.estimatedCostUsd == null ? unknown : formatCostUsd(cost.estimatedCostUsd)}
						</Metric>
						<Metric term={t('app.threads.inspector.cost.actual', 'Actual cost')}>
							{cost.actualCostUsd == null ? unknown : formatCostUsd(cost.actualCostUsd)}
						</Metric>
						<Metric term={t('app.threads.inspector.cost.tokens', 'Tokens')}>
							{tokens.tokenStatus === 'unknown' ? unknown : tokens.totalTokens.toLocaleString()}
						</Metric>
						<Metric term={t('app.threads.inspector.cost.tokenCalls', 'Calls reporting tokens')}>
							{`${tokens.knownCalls}/${tokens.callCount}`}
						</Metric>
						<Metric term={t('app.threads.inspector.cost.latency', 'Latency p50')}>
							{latency.p50Ms == null ? unknown : `${latency.p50Ms.toLocaleString()} ms`}
						</Metric>
						<Metric term={t('app.threads.inspector.cost.latencyMax', 'Latency max')}>
							{latency.maxMs == null ? unknown : `${latency.maxMs.toLocaleString()} ms`}
						</Metric>
						<Metric term={t('app.threads.inspector.cost.rework', 'Rework rounds')}>
							{qualityRework.reworkRounds == null
								? unknown
								: `${qualityRework.reworkRounds}${
										qualityRework.maxReworkRounds == null ? '' : `/${qualityRework.maxReworkRounds}`
									}`}
						</Metric>
						<Metric term={t('app.threads.inspector.cost.modelRework', 'Model rework rate')}>
							{qualityRework.benchmarkInsufficientData || qualityRework.modelReworkRate == null
								? unknown
								: `${Math.round(qualityRework.modelReworkRate * 100)}%`}
						</Metric>
					</dl>

					{cheaperAlternative ? (
						<section className="thread-cost-alternative">
							<div className="thread-cost-model-head">
								<TrendingDown aria-hidden="true" size={15} />
								<strong>
									{t('app.threads.inspector.cost.cheaperTitle', 'A cheaper model would fit')}
								</strong>
							</div>
							<p className="thread-inspector-subline">
								<span className="mono">
									{cheaperAlternative.provider}/{cheaperAlternative.model}
								</span>
								{cheaperAlternative.estimatedCostUsd == null
									? ''
									: ` · ${formatCostUsd(cheaperAlternative.estimatedCostUsd)}`}
								{cheaperAlternative.deltaUsd == null
									? ''
									: ` · ${t('app.threads.inspector.cost.saves', 'saves')} ${formatCostUsd(
											cheaperAlternative.deltaUsd,
										)}`}
							</p>
						</section>
					) : costUnknown ? (
						<p className="thread-inspector-subline">
							{t(
								'app.threads.inspector.cost.noComparison',
								'The chosen model has no known price, so no cheaper alternative can be compared honestly.',
							)}
						</p>
					) : null}
				</>
			)}

			<SectionHeading>
				{t('app.threads.inspector.cost.actions', 'Change the next run')}
			</SectionHeading>

			<div className="thread-cost-actions">
				<SegmentedControl<CostMode>
					label={t('app.threads.inspector.cost.modeLabel', 'Team mode')}
					value={COST_MODES.includes(mode as CostMode) ? (mode as CostMode) : 'balanced'}
					disabled={busy}
					onChange={(next) => void writeSetting(TEAM_MODE_KEY, next)}
					options={COST_MODES.map((option) => ({
						value: option,
						label: t(`app.settings.enum.teamMode.${option}`, option),
					}))}
				/>

				<Checkbox
					label={t('app.threads.inspector.cost.forceLocal', 'Force local models')}
					help={t(
						'app.threads.inspector.cost.forceLocalHelp',
						'Blocks every remote provider for this project, whatever a single message asks for.',
					)}
					checked={policy.forceLocal}
					disabled={busy}
					onChange={(event) => void writeSetting(FORCE_LOCAL_KEY, event.target.checked)}
				/>

				<div className="thread-cost-premium">
					<Button
						variant="secondary"
						icon={<ShieldCheck aria-hidden="true" size={14} />}
						disabled={!premiumAction || busy}
						loading={Boolean(premiumAction) && remediations.busyId === premiumAction?.id}
						onClick={() => {
							if (premiumAction) void remediations.execute(premiumAction);
						}}
					>
						{t('app.threads.inspector.cost.allowPremiumOnce', 'Allow premium once')}
					</Button>
					<span className="thread-inspector-subline">
						{premiumAction
							? t(
									'app.threads.inspector.cost.premiumPending',
									'Approves this exact model for one retry — the policy still gates the run after it.',
								)
							: t(
									'app.threads.inspector.cost.premiumIdle',
									'No premium selection is waiting for approval.',
								)}
					</span>
				</div>
			</div>

			<Disclosure headingLevel={4} title={t('app.threads.inspector.cost.policy', 'Cost policy')}>
				<dl className="thread-inspector-meta">
					<Metric term={t('app.threads.inspector.cost.threshold', 'Approval required over')}>
						{policy.premiumApprovalOverUsd == null
							? t('app.threads.inspector.cost.noThreshold', 'no threshold')
							: formatCostUsd(policy.premiumApprovalOverUsd)}
					</Metric>
					<Metric term={t('app.threads.inspector.cost.tier', 'Cost tier')}>
						{policy.premiumApproval?.costTier ?? unknown}
					</Metric>
					<Metric term={t('app.threads.inspector.cost.verdict', 'Policy verdict')}>
						{policy.premiumApproval?.reason ?? unknown}
					</Metric>
					<Metric term={t('app.threads.inspector.cost.calls', 'Model calls')}>
						{budgetUsed.callCount}
					</Metric>
				</dl>
			</Disclosure>

			<p className="thread-inspector-subline">
				<CircleDollarSign aria-hidden="true" size={12} />{' '}
				{t(
					'app.threads.inspector.cost.honesty',
					'"unknown" means the provider reported no value. It is never counted as zero.',
				)}
			</p>
		</div>
	);
}

function SectionHeading({ children }: { children: ReactNode }) {
	return <h4 className="thread-inspector-section-title">{children}</h4>;
}
