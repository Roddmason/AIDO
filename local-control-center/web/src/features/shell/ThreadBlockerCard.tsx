/**
 * Actionable blocker card for a blocked thread.
 *
 * Renders one persisted remediation group as a self-contained repair card: the pipeline stage as an
 * eyebrow, a plain-language title + one-line explanation, the raw reason and payload behind a collapsed
 * "Technical detail" disclosure, and the ordered repair actions (one primary, the rest secondary). When
 * the backend offers no runnable action the card falls back to "Open configuration"; "Copy diagnostic"
 * and "Dismiss" are always available. Executing runs through {@link ThreadRemediationsHandle}; opening a
 * settings section is delegated to the host so the same card works in the conversation and the inspector.
 * @author Rodrigo Mason
 */

import { AlertTriangle, ClipboardCopy, ExternalLink, Settings2, X } from 'lucide-react';
import { m } from 'motion/react';
import { useState } from 'react';

import type { JsonObject } from '../../api/generated/openapi';
import { Button, Disclosure, SelectField, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { listStagger, panelTransition } from '../../motion/variants';
import {
	type BlockerActionModel,
	type BlockerCardModel,
	buildDiagnostic,
} from './remediationPresentation';
import type { ThreadRemediationsHandle } from './useThreadRemediations';

type ThreadBlockerListProps = {
	handle: ThreadRemediationsHandle;
	/** Opens a Settings section — the recovery path for settings-kind and fallback actions. */
	onOpenSettings: (section?: string, providerId?: string) => void;
	/** Blocker types the host surfaces elsewhere (e.g. the queued banner owns `worker_not_running`). */
	excludeBlockerTypes?: readonly string[];
	labelKey?: string;
	labelFallback?: string;
};

/** The list of actionable blocker cards for a thread; renders nothing when there is nothing to repair. */
export function ThreadBlockerList({
	handle,
	onOpenSettings,
	excludeBlockerTypes,
	labelKey = 'app.threads.remediation.title',
	labelFallback = 'Needs your action',
}: ThreadBlockerListProps) {
	const { t } = useI18n();
	const cards = excludeBlockerTypes?.length
		? handle.cards.filter((card) => !excludeBlockerTypes.includes(card.blockerType))
		: handle.cards;

	// Supplementary surface: stay quiet when there is nothing to repair (or a best-effort load failed)
	// so healthy threads never grow an empty or error box — the pipeline and console remain primary.
	if (cards.length === 0) return null;

	return (
		<m.section
			className="thread-remediation-list"
			aria-label={t(labelKey, labelFallback)}
			aria-live="polite"
			variants={listStagger}
			initial="initial"
			animate="animate"
		>
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
	const anyBusy = handle.busyId !== null;
	const hasActions = card.actions.length > 0;
	const detail = detailsText(card);
	const dismissId = `${card.key}:dismiss`;

	const runExecute = async (action: BlockerActionModel, payload?: JsonObject) => {
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

	return (
		<m.article
			className="thread-remediation-card"
			role="group"
			aria-label={t(card.titleKey, card.titleFallback)}
			variants={panelTransition}
		>
			<span className="thread-remediation-stage mono">{humanize(card.stage)}</span>
			<div className="thread-remediation-title">
				<AlertTriangle aria-hidden="true" size={15} />
				<strong>{t(card.titleKey, card.titleFallback)}</strong>
			</div>
			<p className="thread-remediation-explanation">
				{t(card.explanationKey, card.explanationFallback)}
			</p>

			{card.reason || detail ? (
				<Disclosure
					headingLevel={4}
					title={t('app.threads.remediation.technicalDetail', 'Technical detail')}
				>
					<div className="thread-remediation-detail">
						{card.reason ? <p className="mono">{card.reason}</p> : null}
						{detail ? <pre>{detail}</pre> : null}
					</div>
				</Disclosure>
			) : null}

			<div className="thread-remediation-actions">
				{card.actions.map((action, index) => {
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
									variant={index === 0 ? 'primary' : 'secondary'}
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
							variant={index === 0 ? 'primary' : 'secondary'}
							loading={handle.busyId === action.id}
							disabled={anyBusy}
							icon={
								action.kind === 'settings' ? (
									<ExternalLink aria-hidden="true" size={14} />
								) : undefined
							}
							onClick={
								action.kind === 'settings'
									? () => onOpenSettings(action.section, settingsProviderId(action))
									: () => void runExecute(action)
							}
						>
							{t(action.labelKey, action.labelFallback)}
						</Button>
					);
				})}

				{hasActions ? null : (
					<Button
						variant="primary"
						icon={<Settings2 aria-hidden="true" size={14} />}
						disabled={anyBusy}
						onClick={() => onOpenSettings('providers-cli')}
					>
						{t('app.threads.remediation.action.openConfiguration', 'Open configuration')}
					</Button>
				)}

				<Button
					variant="secondary"
					icon={<ClipboardCopy aria-hidden="true" size={14} />}
					disabled={anyBusy}
					onClick={() => void copyDiagnostic()}
				>
					{t('app.threads.remediation.copyDiagnostic', 'Copy diagnostic')}
				</Button>

				<Button
					variant="secondary"
					className="thread-remediation-dismiss"
					icon={<X aria-hidden="true" size={14} />}
					loading={handle.busyId === dismissId}
					disabled={anyBusy}
					onClick={() => void handle.dismiss(card)}
				>
					{t('app.threads.remediation.dismiss', 'Dismiss')}
				</Button>
			</div>
		</m.article>
	);
}
