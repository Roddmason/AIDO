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
import { useState } from 'react';

import type { JsonObject } from '../../api/generated/openapi';
import { Button, Dialog, Disclosure, SelectField, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { redactVisibleSecret } from '../../lib/format';
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
	// The first load is allowed to settle first, so a slow fetch never flashes the fallback.
	const showFallback = persisted.length === 0 && Boolean(fallback) && !handle.loading;
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
	const [pendingConfirmation, setPendingConfirmation] = useState<BlockerActionModel | null>(null);
	const anyBusy = handle.busyId !== null;
	const [primaryAction, ...secondaryActions] = card.actions;
	const hasDestructiveAction = card.actions.some((action) => action.confirmationRequired);
	const detail = detailsText(card);
	// The cause is now a first-class fact on the card, so the disclosure only repeats it when the
	// backend reported a machine reason that differs from it.
	const technicalReason = card.reason && card.reason !== card.cause ? card.reason : '';
	const dismissId = `${card.key}:dismiss`;

	const runExecute = async (action: BlockerActionModel, payload?: JsonObject) => {
		try {
			const result = await handle.execute(action, payload);
			if (!result) return;
			const execution = (result.execution ?? {}) as Record<string, unknown>;
			const status = typeof execution.status === 'string' ? execution.status : '';
			const reason = typeof execution.reason === 'string' ? execution.reason : undefined;
			if (status === 'completed' || status === 'queued' || status === 'awaiting_approval') {
				notify({
					title: t('app.threads.remediation.executeSuccess', 'Repair action ran'),
					body: reason,
					tone: 'ok',
				});
			} else {
				notify({
					title: t('app.threads.remediation.executeBlocked', 'Action needs another step'),
					body: reason,
					tone: 'warn',
				});
			}
		} catch (error) {
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
						{card.cause ||
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
