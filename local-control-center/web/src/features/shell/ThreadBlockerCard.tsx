/**
 * Actionable blocker card for a blocked thread.
 *
 * Renders one persisted remediation group as a self-contained repair card: a plain-language title and
 * one-line explanation, a labelled stage/cause/impact summary so the operator sees where it broke, why,
 * and what it costs, the single recommended repair as the primary button, every other repair as a
 * secondary, and the raw reason plus payload behind a collapsed "Technical detail" disclosure. Actions
 * the backend marked destructive are gated behind a confirmation dialog, because `execute` refuses them
 * without one. When the backend offers no runnable action — including a run it blocked without
 * persisting any remediation ({@link buildFallbackCard}) — the card falls back to "Open configuration";
 * "Copy diagnostic" is always available and "Dismiss" appears only when there is a record to dismiss.
 * Executing runs through {@link ThreadRemediationsHandle}; opening a settings section is delegated to
 * the host so the same card works in the conversation and the inspector.
 * @author Rodrigo Mason
 */

import { AlertTriangle, ClipboardCopy, ExternalLink, RefreshCw, Settings2, X } from 'lucide-react';
import { m } from 'motion/react';
import { useEffect, useState } from 'react';

import type { JsonObject } from '../../api/generated/openapi';
import {
	Button,
	Checkbox,
	DataTable,
	Dialog,
	Disclosure,
	SelectField,
	TextArea,
	useToast,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { formatTime, redactVisibleSecret } from '../../lib/format';
import { listStagger, panelTransition } from '../../motion/variants';
import {
	type BlockerActionModel,
	type BlockerCardModel,
	buildDiagnostic,
	buildFallbackCard,
} from './remediationPresentation';
import type { ThreadRemediationsHandle } from './useThreadRemediations';

/** The blocked state the host derived from the event log, used only when no remediation exists. */
export type ThreadBlockerFallback = {
	stage: string;
	reason: string;
};

type ThreadBlockerListProps = {
	handle: ThreadRemediationsHandle;
	/** Opens a Settings section — the recovery path for settings-kind and fallback actions. */
	onOpenSettings: (section?: string, providerId?: string) => void;
	/** Blocker types the host surfaces elsewhere (e.g. the queued banner owns `worker_not_running`). */
	excludeBlockerTypes?: readonly string[];
	/** Set by hosts that know the thread is blocked, so a run with no remediation still shows a fix. */
	fallback?: ThreadBlockerFallback | null;
	labelKey?: string;
	labelFallback?: string;
};

/** The list of actionable blocker cards for a thread; renders nothing when there is nothing to repair. */
export function ThreadBlockerList({
	handle,
	onOpenSettings,
	excludeBlockerTypes,
	fallback,
	labelKey = 'app.threads.remediation.title',
	labelFallback = 'Needs your action',
}: ThreadBlockerListProps) {
	const { t } = useI18n();
	const persisted = excludeBlockerTypes?.length
		? handle.cards.filter((card) => !excludeBlockerTypes.includes(card.blockerType))
		: handle.cards;
	// A blocked run with no persisted remediation still gets a card: raw console text is not a fix.
	// Only a successful read can establish that no repair action exists.
	const showFallback =
		persisted.length === 0 && Boolean(fallback) && !handle.loading && !handle.error;
	const cards =
		showFallback && fallback ? [buildFallbackCard(fallback.stage, fallback.reason)] : persisted;

	// A failed read is itself actionable: hiding it would leave the operator with stale or missing
	// repair actions and no way to distinguish that from a healthy thread.
	if (cards.length === 0 && !handle.error) return null;

	return (
		<m.section
			className="thread-remediation-list"
			aria-label={t(labelKey, labelFallback)}
			aria-live="polite"
			variants={listStagger}
			initial="initial"
			animate="animate"
		>
			{handle.error ? (
				<m.article
					className="thread-remediation-card thread-remediation-load-error"
					role="alert"
					variants={panelTransition}
				>
					<div className="thread-remediation-title">
						<AlertTriangle aria-hidden="true" size={15} />
						<strong>
							{t('app.threads.remediation.loadFailed', 'Could not load repair actions')}
						</strong>
					</div>
					<p className="thread-remediation-explanation">
						{t(
							'app.threads.remediation.loadFailedBody',
							'AIDO could not read the recovery plan. Retry before changing unrelated configuration.',
						)}
					</p>
					<div className="thread-remediation-primary">
						<Button
							variant="primary"
							icon={<RefreshCw aria-hidden="true" size={14} />}
							disabled={handle.busyId !== null}
							onClick={handle.reload}
						>
							{t('app.global.retry', 'Retry')}
						</Button>
					</div>
				</m.article>
			) : null}
			{cards.map((card) => (
				<ThreadBlockerCard
					key={card.key}
					card={card}
					handle={handle}
					onOpenSettings={onOpenSettings}
				/>
			))}
		</m.section>
	);
}

function humanize(value: string): string {
	return value.replace(/_/g, ' ');
}

function detailsText(card: BlockerCardModel): string {
	const first = card.remediations[0];
	const payload = first?.payload;
	if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return '';
	const details = (payload as Record<string, unknown>).details;
	if (!details || (typeof details === 'object' && Object.keys(details).length === 0)) return '';
	return JSON.stringify(details, null, 2);
}

function actionPayload(action: BlockerActionModel): Record<string, unknown> {
	const payload = action.remediation?.payload;
	return payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : {};
}

function settingsProviderId(action: BlockerActionModel): string | undefined {
	const payload = actionPayload(action);
	const directProviderId = payload.providerId;
	if (typeof directProviderId === 'string' && directProviderId.trim()) {
		return directProviderId.trim();
	}
	const providerSetup = payload.providerSetup;
	if (!providerSetup || typeof providerSetup !== 'object' || Array.isArray(providerSetup)) {
		return undefined;
	}
	const nestedProviderId = (providerSetup as Record<string, unknown>).providerId;
	return typeof nestedProviderId === 'string' && nestedProviderId.trim()
		? nestedProviderId.trim()
		: undefined;
}

function answerOptions(action: BlockerActionModel): string[] {
	const options = actionPayload(action).options;
	if (!Array.isArray(options)) return [];
	return options.map((option) => String(option).trim()).filter(Boolean);
}

type ResearchCandidate = {
	researchRunId: string;
	label: string;
	eligible: boolean;
	reason?: string;
};

function researchCandidates(action: BlockerActionModel): ResearchCandidate[] {
	const candidates = actionPayload(action).researchCandidates;
	if (!Array.isArray(candidates)) return [];
	return candidates.flatMap((candidate: unknown) => {
		if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) return [];
		const record = candidate as Record<string, unknown>;
		if (typeof record.researchRunId !== 'string' || !record.researchRunId.trim()) return [];
		return [
			{
				researchRunId: record.researchRunId,
				label: typeof record.label === 'string' ? record.label : record.researchRunId,
				// The backend validates scope, technical decisions and citations; ready alone is insufficient.
				eligible: record.eligible === true,
				reason: typeof record.reason === 'string' ? record.reason : undefined,
			},
		];
	});
}

const RUNTIME_RISK_FIELDS = [
	['role', 'Role'],
	['providerId', 'Provider'],
	['model', 'Model'],
	['runtime', 'Runtime'],
	['risk', 'Risk'],
	['decisionId', 'Decision'],
	['taskId', 'Task'],
	['agentProfileId', 'Agent profile'],
] as const;

type RuntimeRiskProposal = Record<(typeof RUNTIME_RISK_FIELDS)[number][0], string>;

/** Consent is tied to the displayed server request; only the operator's reason is sent back. */
function RuntimeRiskReview({
	action,
	handle,
}: {
	action: BlockerActionModel;
	handle: ThreadRemediationsHandle;
}) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [reason, setReason] = useState('');
	const [consentedScope, setConsentedScope] = useState<string | null>(null);
	const [error, setError] = useState('');
	const [failedReview, setFailedReview] = useState<{
		refreshFrom: BlockerActionModel | null;
	} | null>(null);
	const [clock, setClock] = useState(Date.now);
	const payload = actionPayload(action);
	const rawProposals = Array.isArray(payload.proposals) ? payload.proposals : [];
	const proposals = rawProposals.filter((value): value is RuntimeRiskProposal => {
		if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
		const record = value as Record<string, unknown>;
		return RUNTIME_RISK_FIELDS.every(([field]) => {
			const value = record[field];
			return typeof value === 'string' && value.trim().length > 0;
		});
	});
	const scope = [
		['projectId', 'Project', action.remediation?.projectId],
		['threadId', 'Thread', action.remediation?.threadId],
		['loopId', 'Loop', action.remediation?.loopId],
		['jobId', 'Job', payload.jobId],
		['actionRequestId', 'Approval request', payload.actionRequestId],
		['expiresAt', 'Expires at', payload.expiresAt],
	] as const;
	const scopeKey = JSON.stringify([scope, proposals]);
	const consented = consentedScope === scopeKey;
	const expiresAt =
		typeof payload.expiresAt === 'string' ? Date.parse(payload.expiresAt) : Number.NaN;
	const complete =
		proposals.length > 0 &&
		proposals.length === rawProposals.length &&
		scope.every(([, , value]) => typeof value === 'string' && value.trim().length > 0);
	const unavailable =
		!complete || !Number.isFinite(expiresAt)
			? t(
					'app.threads.remediation.riskReview.incomplete',
					'The approval request is incomplete. Refresh the repair actions before reviewing it.',
				)
			: expiresAt <= Math.max(clock, Date.now())
				? t(
						'app.threads.remediation.riskReview.expired',
						'This approval request has expired and cannot be approved. Refresh only re-reads its status; it does not renew approval.',
					)
				: '';
	const busy = handle.busyId !== null;
	// A refresh must return a new server snapshot before consent can be given again. A click or a
	// failed refresh alone does not make the proposal that failed safe to approve.
	const mustRefresh =
		failedReview !== null &&
		(failedReview.refreshFrom === null ||
			failedReview.refreshFrom === action ||
			handle.loading ||
			handle.error);
	const canApprove =
		!busy &&
		!handle.loading &&
		!unavailable &&
		!mustRefresh &&
		reason.trim().length > 0 &&
		consented;

	useEffect(() => {
		let timer: number | undefined;
		const update = () => {
			const now = Date.now();
			setClock(now);
			if (Number.isFinite(expiresAt) && expiresAt > now) {
				timer = window.setTimeout(update, Math.min(expiresAt - now, 2_147_483_647));
			}
		};
		update();
		return () => window.clearTimeout(timer);
	}, [expiresAt]);

	const approve = async () => {
		if (!canApprove || expiresAt <= Date.now()) return;
		setError('');
		const fallback = t(
			'app.threads.remediation.riskReview.failed',
			'Runtime risk approval did not complete. Review the current request and try again.',
		);
		const failReview = (message: string) => {
			setError(message);
			setConsentedScope(null);
			setFailedReview({ refreshFrom: null });
		};
		try {
			const result = await handle.execute(action, { reason: reason.trim() });
			if (!result) return;
			const execution = (result.execution ?? {}) as Record<string, unknown>;
			if (execution.status !== 'queued') {
				failReview(redactVisibleSecret(execution.reason, fallback));
				return;
			}
			notify({
				title: t(
					'app.threads.remediation.riskReview.queued',
					'Runtime risk approved; continuation queued',
				),
				body:
					typeof execution.reason === 'string' ? redactVisibleSecret(execution.reason) : undefined,
				tone: 'ok',
			});
		} catch (failure) {
			failReview(
				redactVisibleSecret(failure instanceof Error ? failure.message : failure, fallback),
			);
		}
	};

	return (
		<div className="thread-remediation-confirm" style={{ width: '100%', minWidth: 0 }}>
			<p>
				{t(
					'app.threads.remediation.riskReview.scope',
					'This consent applies only to these proposals in this thread and this run. It does not approve budgets, costs or permissions.',
				)}
			</p>
			<p>
				{t('app.threads.remediation.riskReview.field.expiresAt', 'Expires at')}:{' '}
				{Number.isFinite(expiresAt) && typeof payload.expiresAt === 'string' ? (
					<time dateTime={payload.expiresAt}>{formatTime(payload.expiresAt)}</time>
				) : (
					'—'
				)}
			</p>
			<ol
				className="thread-remediation-detail"
				aria-label={t('app.threads.remediation.riskReview.proposals', 'Proposed runtimes')}
			>
				{proposals.map((proposal) => (
					<li
						key={`${proposal.role}:${proposal.agentProfileId}:${proposal.taskId}:${proposal.decisionId}`}
					>
						<strong className="thread-remediation-explanation">{proposal.role}</strong>
						<p>
							<span>{proposal.providerId}</span> / <span>{proposal.model}</span>
						</p>
						<p>
							{t('app.threads.remediation.riskReview.field.risk', 'Risk')}:{' '}
							<span>{proposal.risk}</span>
						</p>
					</li>
				))}
			</ol>
			<Disclosure
				headingLevel={4}
				title={t('app.threads.remediation.riskReview.details', 'Approval details')}
			>
				<dl className="thread-remediation-facts">
					{scope.map(([field, label, value]) => (
						<div key={field}>
							<dt>{t(`app.threads.remediation.riskReview.field.${field}`, label)}</dt>
							<dd>{typeof value === 'string' ? value : '—'}</dd>
						</div>
					))}
				</dl>
				<DataTable
					caption={t('app.threads.remediation.riskReview.proposals', 'Proposed runtimes')}
					columns={RUNTIME_RISK_FIELDS.map(([field, label]) => ({
						key: field,
						label: t(`app.threads.remediation.riskReview.field.${field}`, label),
						render: (proposal: RuntimeRiskProposal) => proposal[field],
					}))}
					rows={proposals}
					empty={null}
				/>
			</Disclosure>
			<TextArea
				label={t('app.threads.remediation.riskReview.reason', 'Reason for approval')}
				value={reason}
				onChange={(event) => setReason(event.target.value)}
				error={unavailable || error}
				required
				disabled={busy || Boolean(unavailable)}
				rows={3}
			/>
			<Checkbox
				label={t(
					'app.threads.remediation.riskReview.consent',
					'I accept the runtime risk of these exact proposals for this continuation.',
				)}
				checked={consented}
				onChange={(event) => setConsentedScope(event.target.checked ? scopeKey : null)}
				disabled={busy || handle.loading || Boolean(unavailable) || mustRefresh}
				help={
					mustRefresh
						? t(
								'app.threads.remediation.riskReview.refreshRequired',
								'Refresh the actions and review the current scope before consenting again.',
							)
						: undefined
				}
			/>
			<Button
				variant={action.primary ? 'primary' : 'secondary'}
				loading={handle.busyId === action.id}
				disabled={!canApprove}
				onClick={() => void approve()}
			>
				{t('app.threads.remediation.action.approveRuntimeRisk', 'Approve runtime risk')}
			</Button>
			<Button
				variant="secondary"
				disabled={busy || handle.loading}
				onClick={() => {
					setConsentedScope(null);
					setFailedReview((current) => (current ? { refreshFrom: action } : null));
					handle.reload();
				}}
			>
				{t('app.threads.remediation.refreshActions', 'Refresh actions')}
			</Button>
		</div>
	);
}

function ThreadBlockerCard({
	card,
	handle,
	onOpenSettings,
}: {
	card: BlockerCardModel;
	handle: ThreadRemediationsHandle;
	onOpenSettings: (section?: string, providerId?: string) => void;
}) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [selectedAnswers, setSelectedAnswers] = useState<Record<string, string>>({});
	const [selectedResearch, setSelectedResearch] = useState<Record<string, string>>({});
	const [researchErrors, setResearchErrors] = useState<Record<string, string>>({});
	const [pendingConfirmation, setPendingConfirmation] = useState<BlockerActionModel | null>(null);
	const [partialDiff, setPartialDiff] = useState<{
		path: string;
		patch: string;
		untracked: string[];
	} | null>(null);
	const anyBusy = handle.busyId !== null;
	const [primaryAction, ...secondaryActions] = card.actions;
	const hasDestructiveAction = card.actions.some((action) => action.confirmationRequired);
	const detail = detailsText(card);
	// `cause` is the raw runtime string, which for some blockers is an internal validator message the
	// operator cannot act on (e.g. "questions[1].defaultDecision must be one of options"). When the
	// backend classified the blocker well enough to have human copy for it, that copy is the fact we
	// show; the raw string always stays reachable in the technical disclosure rather than replacing it.
	const humanCause = card.causeKey ? t(card.causeKey, card.causeFallback ?? '') : '';
	const rawCause = card.reason || card.cause;
	const technicalReason = rawCause && rawCause !== (humanCause || card.cause) ? rawCause : '';
	const dismissId = `${card.key}:dismiss`;
	const isResearchAction = (action: BlockerActionModel) =>
		card.blockerType === 'research_required' && action.remediation?.actionType === 'retry_loop';

	const runExecute = async (action: BlockerActionModel, payload?: JsonObject) => {
		const appliesResearch = isResearchAction(action);
		if (appliesResearch) setResearchErrors((current) => ({ ...current, [action.id]: '' }));
		try {
			const result = await handle.execute(action, payload);
			if (!result) return;
			const execution = (result.execution ?? {}) as Record<string, unknown>;
			const status = typeof execution.status === 'string' ? execution.status : '';
			const reason = typeof execution.reason === 'string' ? execution.reason : undefined;
			if (
				status === 'completed' &&
				action.remediation?.actionType === 'view_diff' &&
				execution.partialExecution === true
			) {
				setPartialDiff({
					path: redactVisibleSecret(execution.workspacePath),
					patch: redactVisibleSecret(execution.diff, ''),
					untracked: Array.isArray(execution.untrackedFiles)
						? execution.untrackedFiles.map((name) => redactVisibleSecret(name))
						: [],
				});
				return;
			}
			if (
				status === 'completed' ||
				status === 'queued' ||
				status === 'validating' ||
				(!appliesResearch && status === 'awaiting_approval')
			) {
				notify({
					title:
						appliesResearch && status === 'queued'
							? t(
									'app.threads.remediation.researchApplyQueued',
									'Research applied; continuation queued',
								)
							: t('app.threads.remediation.executeSuccess', 'Repair action ran'),
					body: reason,
					tone: 'ok',
				});
			} else {
				if (appliesResearch) {
					setResearchErrors((current) => ({
						...current,
						[action.id]: redactVisibleSecret(
							reason,
							t(
								'app.threads.remediation.researchApplyFailed',
								'The research report could not be applied. Review its eligibility and try again.',
							),
						),
					}));
				}
				notify({
					title: t('app.threads.remediation.executeBlocked', 'Action needs another step'),
					body: reason,
					tone: 'warn',
				});
			}
		} catch (error) {
			if (appliesResearch) {
				setResearchErrors((current) => ({
					...current,
					[action.id]: redactVisibleSecret(
						error instanceof Error ? error.message : error,
						t(
							'app.threads.remediation.researchApplyFailed',
							'The research report could not be applied. Review its eligibility and try again.',
						),
					),
				}));
			}
			notify({
				title: t('app.threads.remediation.executeFailed', 'Repair action failed'),
				body: redactVisibleSecret(
					error instanceof Error ? error.message : error,
					t('app.threads.remediation.executeFailedBody', 'The repair request did not complete.'),
				),
				tone: 'danger',
				durationMs: 0,
				action: {
					label: t('app.threads.remediation.refreshActions', 'Refresh actions'),
					onPress: handle.reload,
				},
			});
		}
	};

	const dismissCard = async () => {
		try {
			await handle.dismiss(card);
		} catch (error) {
			notify({
				title: t('app.threads.remediation.dismissFailed', 'Could not dismiss repair action'),
				body: redactVisibleSecret(
					error instanceof Error ? error.message : error,
					t('app.threads.remediation.dismissFailedBody', 'The dismiss request did not complete.'),
				),
				tone: 'danger',
				durationMs: 0,
				action: {
					label: t('app.threads.remediation.refreshActions', 'Refresh actions'),
					onPress: handle.reload,
				},
			});
		}
	};

	const copyDiagnostic = async () => {
		try {
			await navigator.clipboard.writeText(buildDiagnostic(card));
			notify({
				title: t('app.threads.remediation.copied', 'Diagnostic copied'),
				tone: 'ok',
			});
		} catch {
			notify({
				title: t('app.threads.remediation.copyFailed', 'Could not copy the diagnostic'),
				tone: 'danger',
			});
		}
	};

	const confirmPendingAction = async () => {
		const action = pendingConfirmation;
		setPendingConfirmation(null);
		if (action) await runExecute(action, { confirmed: true });
	};

	const activateAction = (action: BlockerActionModel) => {
		if (action.kind === 'settings') {
			onOpenSettings(action.section, settingsProviderId(action));
			return;
		}
		// `execute` refuses a destructive action without an explicit confirmation, so ask for it here
		// instead of letting the button fail with a "needs another step" toast forever.
		if (action.confirmationRequired) {
			setPendingConfirmation(action);
			return;
		}
		void runExecute(action);
	};

	const renderAction = (action: BlockerActionModel) => {
		const variant = action.primary ? 'primary' : 'secondary';
		if (action.remediation?.actionType === 'approve_runtime_risk') {
			return <RuntimeRiskReview key={action.id} action={action} handle={handle} />;
		}
		if (isResearchAction(action)) {
			const candidates = researchCandidates(action);
			const hasEligibleResearch = candidates.some((candidate) => candidate.eligible);
			const selected = candidates.find(
				(candidate) =>
					candidate.researchRunId === selectedResearch[action.id] && candidate.eligible,
			);
			return (
				<div className="thread-remediation-answer" key={action.id}>
					<SelectField
						label={t('app.threads.remediation.researchLabel', 'Research report')}
						help={
							hasEligibleResearch
								? t(
										'app.threads.remediation.researchHelp',
										'Adds evidence and preserves existing decisions. No version is accepted automatically.',
									)
								: t(
										'app.threads.remediation.researchEmpty',
										'No eligible report is available. A completed report needs a validated technical decision and citations.',
									)
						}
						error={researchErrors[action.id]}
						value={selected?.researchRunId ?? ''}
						disabled={anyBusy}
						onChange={(event) => {
							setSelectedResearch((current) => ({ ...current, [action.id]: event.target.value }));
							setResearchErrors((current) => ({ ...current, [action.id]: '' }));
						}}
					>
						<option value="">
							{t('app.threads.remediation.researchPlaceholder', 'Select a research report')}
						</option>
						{candidates.map((candidate) => (
							<option
								key={candidate.researchRunId}
								value={candidate.researchRunId}
								disabled={!candidate.eligible}
							>
								{candidate.eligible
									? candidate.label
									: `${candidate.label} — ${
											candidate.reason ||
											t('app.threads.remediation.researchIneligible', 'Not eligible')
										}`}
							</option>
						))}
					</SelectField>
					<Button
						variant={variant}
						loading={handle.busyId === action.id}
						disabled={anyBusy || !selected}
						onClick={() => {
							if (selected) void runExecute(action, { researchRunId: selected.researchRunId });
						}}
					>
						{t('app.threads.remediation.action.applyResearch', 'Apply research')}
					</Button>
				</div>
			);
		}
		const options = answerOptions(action);
		if (action.remediation?.actionType === 'answer_question' && options.length) {
			const selectedAnswer = selectedAnswers[action.id] ?? '';
			return (
				<div className="thread-remediation-answer" key={action.id}>
					<SelectField
						label={t('app.threads.remediation.answerLabel', 'Answer')}
						help={t(
							'app.threads.remediation.answerHelp',
							'Choose one of the options requested by the Product Loop.',
						)}
						value={selectedAnswer}
						disabled={anyBusy}
						onChange={(event) =>
							setSelectedAnswers((current) => ({
								...current,
								[action.id]: event.target.value,
							}))
						}
					>
						<option value="">
							{t('app.threads.remediation.answerPlaceholder', 'Select an answer')}
						</option>
						{options.map((option) => (
							<option key={option} value={option}>
								{option}
							</option>
						))}
					</SelectField>
					<Button
						variant={variant}
						loading={handle.busyId === action.id}
						disabled={anyBusy || !selectedAnswer}
						onClick={() => void runExecute(action, { answer: selectedAnswer })}
					>
						{t(action.labelKey, action.labelFallback)}
					</Button>
				</div>
			);
		}
		return (
			<Button
				key={action.id}
				variant={variant}
				loading={handle.busyId === action.id}
				disabled={anyBusy}
				icon={
					action.kind === 'settings' ? <ExternalLink aria-hidden="true" size={14} /> : undefined
				}
				onClick={() => activateAction(action)}
			>
				{t(action.labelKey, action.labelFallback)}
			</Button>
		);
	};

	return (
		<m.article
			className="thread-remediation-card"
			role="group"
			aria-label={t(card.titleKey, card.titleFallback)}
			variants={panelTransition}
		>
			<div className="thread-remediation-title">
				<AlertTriangle aria-hidden="true" size={15} />
				<strong>{t(card.titleKey, card.titleFallback)}</strong>
			</div>
			<p className="thread-remediation-explanation">
				{t(card.explanationKey, card.explanationFallback)}
			</p>

			<dl className="thread-remediation-facts">
				<div>
					<dt>{t('app.threads.remediation.factStage', 'Stage')}</dt>
					<dd>
						{card.stage
							? humanize(card.stage)
							: t('app.threads.remediation.stageUnknown', 'unknown')}
					</dd>
				</div>
				<div>
					<dt>{t('app.threads.remediation.factCause', 'Cause')}</dt>
					<dd>
						{humanCause ||
							card.cause ||
							t('app.threads.remediation.causeUnknown', 'The runtime reported no machine cause.')}
					</dd>
				</div>
				<div>
					<dt>{t('app.threads.remediation.factImpact', 'Impact')}</dt>
					<dd>{t(card.impactKey, card.impactFallback)}</dd>
				</div>
			</dl>

			<div className="thread-remediation-primary">
				{primaryAction ? (
					renderAction(primaryAction)
				) : (
					<Button
						variant="primary"
						icon={<Settings2 aria-hidden="true" size={14} />}
						disabled={anyBusy}
						onClick={() => onOpenSettings('providers-cli')}
					>
						{t('app.threads.remediation.action.openConfiguration', 'Open configuration')}
					</Button>
				)}
			</div>

			<div className="thread-remediation-actions">
				{secondaryActions.map(renderAction)}

				<Button
					variant="secondary"
					icon={<ClipboardCopy aria-hidden="true" size={14} />}
					disabled={anyBusy}
					onClick={() => void copyDiagnostic()}
				>
					{t('app.threads.remediation.copyDiagnostic', 'Copy diagnostic')}
				</Button>

				{card.remediations.length ? (
					<Button
						variant="secondary"
						className="thread-remediation-dismiss"
						icon={<X aria-hidden="true" size={14} />}
						loading={handle.busyId === dismissId}
						disabled={anyBusy}
						onClick={() => void dismissCard()}
					>
						{t('app.threads.remediation.dismiss', 'Dismiss')}
					</Button>
				) : null}
			</div>

			{technicalReason || detail ? (
				<Disclosure
					headingLevel={4}
					title={t('app.threads.remediation.technicalDetail', 'Technical detail')}
				>
					<div className="thread-remediation-detail">
						{technicalReason ? <p className="mono">{technicalReason}</p> : null}
						{detail ? <pre>{detail}</pre> : null}
					</div>
				</Disclosure>
			) : null}

			{partialDiff ? (
				<Dialog
					open
					onClose={() => setPartialDiff(null)}
					label={t('app.threads.remediation.partialChanges', 'Preserved workspace changes')}
				>
					<p className="mono">{partialDiff.path}</p>
					<h4>{t('app.threads.remediation.trackedChanges', 'Tracked changes')}</h4>
					<pre>
						{partialDiff.patch.slice(0, 100_000) ||
							t('app.threads.remediation.noTrackedChanges', 'No tracked changes.')}
					</pre>
					{partialDiff.patch.length > 100_000 ? (
						<p>
							{t(
								'app.threads.remediation.diffPreviewTruncated',
								'Preview truncated; full changes remain in the workspace.',
							)}
						</p>
					) : null}
					<h4>
						{t(
							'app.threads.remediation.untrackedFiles',
							'Untracked files (contents are not included in this diff)',
						)}
					</h4>
					<ul>
						{partialDiff.untracked.map((name) => (
							<li className="mono" key={name}>
								{name}
							</li>
						))}
					</ul>
				</Dialog>
			) : null}

			{hasDestructiveAction ? (
				<Dialog
					open={pendingConfirmation !== null}
					onClose={() => setPendingConfirmation(null)}
					label={t('app.threads.remediation.confirmTitle', 'Confirm this action')}
				>
					<div className="thread-remediation-confirm">
						<p>
							{t(
								'app.threads.remediation.confirmBody',
								'This action can discard or overwrite uncommitted local work. Run it only once your changes are preserved.',
							)}
						</p>
						<div className="thread-remediation-confirm-actions">
							<Button variant="secondary" onClick={() => setPendingConfirmation(null)}>
								{t('app.threads.remediation.confirmCancel', 'Cancel')}
							</Button>
							<Button variant="primary" onClick={() => void confirmPendingAction()}>
								{t('app.threads.remediation.confirmAccept', 'Run anyway')}
							</Button>
						</div>
					</div>
				</Dialog>
			) : null}
		</m.article>
	);
}
