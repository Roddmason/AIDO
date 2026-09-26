/**
 * How a thread's pending decisions get answered: one form per decision — checkboxes (several
 * choices) for Product Owner questions/decisions, a single radio for the mutually-exclusive ones
 * (similarity, existing functionality, intake), and a free-text field that is always available for
 * Product Owner decisions (the only field at all when they carry no options, so a decision never
 * reaches a dead end) — plus a single "Send answers" button that resolves every decision the
 * operator answered together, in one chat message.
 *
 * Pure presentation: the host ({@link ThreadConversation}, which renders it inside the execution
 * panel) owns the actual HTTP call (`onSubmit`) and the busy/reload cycle. This is the only place
 * that answers a `po_needs_input` / `functionality_memory_decision_required` /
 * `thread_similarity_decision_required` / `thread_intake_decision_required` decision — the
 * execution panel excludes those blocker types from its generic remediation list
 * ({@link DECISION_REMEDIATION_BLOCKER_TYPES}) so the same decision never renders twice. The
 * inspector intentionally does not duplicate this form (its remediation list keeps loading/
 * reflowing independently of the thread poll, which fought this one for layout stability); it
 * still surfaces a pending-decision count via `ThreadVitals`, and the operator answers from here.
 * @author Rodrigo Mason
 */
import { AlertTriangle } from 'lucide-react';
import { m } from 'motion/react';
import { useMemo, useState } from 'react';

import type { ThreadDecision } from '../../api/types';
import { Button, Checkbox, Radio, TextArea } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { panelTransition } from '../../motion/variants';
import {
	decisionOptionDescription,
	decisionOptionLabel,
	decisionPromptText,
} from './decisionOptionCopy';

/** Remediation `blockerType`s a pending thread decision creates; this component is their one
 * answer surface, so both hosts pass this list as `excludeBlockerTypes` to `ThreadBlockerList`. */
export const DECISION_REMEDIATION_BLOCKER_TYPES = [
	'po_needs_input',
	'functionality_memory_decision_required',
	'thread_similarity_decision_required',
	'thread_intake_decision_required',
] as const;

/** `thread_decisions.metadata.source` for a question/decision the ProductOwnerAgent raised
 * (agents/product_owner_agent.py::PRODUCT_OWNER_AGENT_ID). Only these accept several selected
 * options and free text alongside options; the rest are mutually exclusive by nature. */
const PRODUCT_OWNER_DECISION_SOURCE = 'product_owner_agent';

function isProductOwnerDecision(decision: ThreadDecision): boolean {
	return decision.metadata?.source === PRODUCT_OWNER_DECISION_SOURCE;
}

export type DecisionAnswer = {
	decisionId: string;
	selectedOptions: string[];
	freeText: string;
};

type Draft = { selectedOptions: string[]; freeText: string };
type DraftState = Record<string, Draft>;

type ThreadDecisionAnswersProps = {
	decisions: ThreadDecision[];
	busy: boolean;
	onSubmit: (answers: DecisionAnswer[]) => Promise<void> | void;
};

function hasAnswer(draft: Draft | undefined): boolean {
	if (!draft) return false;
	return draft.selectedOptions.length > 0 || draft.freeText.trim().length > 0;
}

/** Every pending decision rendered as an answerable form, with a single button that resolves
 * every one the operator filled in. */
export function ThreadDecisionAnswers({ decisions, busy, onSubmit }: ThreadDecisionAnswersProps) {
	const { t } = useI18n();
	const [drafts, setDrafts] = useState<DraftState>({});

	const answerable = useMemo(
		() => decisions.filter((decision) => decision.status === 'pending'),
		[decisions],
	);

	const readyAnswers: DecisionAnswer[] = answerable
		.filter((decision) => hasAnswer(drafts[decision.id]))
		.map((decision) => {
			const draft = drafts[decision.id];
			return {
				decisionId: decision.id,
				selectedOptions: draft?.selectedOptions ?? [],
				freeText: (draft?.freeText ?? '').trim(),
			};
		});

	if (answerable.length === 0) return null;

	const handleSubmit = async () => {
		if (readyAnswers.length === 0) return;
		await onSubmit(readyAnswers);
		setDrafts({});
	};

	const setDraft = (decisionId: string, patch: Partial<Draft>) => {
		setDrafts((current) => ({
			...current,
			[decisionId]: {
				selectedOptions: current[decisionId]?.selectedOptions ?? [],
				freeText: current[decisionId]?.freeText ?? '',
				...patch,
			},
		}));
	};

	return (
		<section
			className="thread-decision-answers"
			aria-label={t('app.threads.decisionTitle', 'Decision needed')}
		>
			{answerable.map((decision) => {
				const draft = drafts[decision.id] ?? { selectedOptions: [], freeText: '' };
				const hasOptions = decision.options.length > 0;
				const isPoDecision = isProductOwnerDecision(decision);
				// Mutually exclusive types (similarity, existing functionality, intake) keep a single
				// choice; only Product Owner questions/decisions accept several plus free text.
				const allowsMultiple = isPoDecision;
				return (
					<m.article
						key={decision.id}
						className="thread-decision-console"
						variants={panelTransition}
						initial="initial"
						animate="animate"
					>
						<div className="thread-decision-head">
							<AlertTriangle aria-hidden="true" size={15} />
							<strong>{decision.title}</strong>
						</div>
						<p>{decisionPromptText(decision, t)}</p>
						{hasOptions && allowsMultiple ? (
							<fieldset className="thread-decision-options">
								<legend className="sr-only">{decision.title}</legend>
								{decision.options.map((option) => (
									<Checkbox
										key={option}
										label={decisionOptionLabel(option, t)}
										help={decisionOptionDescription(option, t) || undefined}
										disabled={busy}
										checked={draft.selectedOptions.includes(option)}
										onChange={(event) => {
											const next = event.target.checked
												? [...draft.selectedOptions, option]
												: draft.selectedOptions.filter((item) => item !== option);
											setDraft(decision.id, { selectedOptions: next });
										}}
									/>
								))}
							</fieldset>
						) : null}
						{hasOptions && !allowsMultiple ? (
							<div
								className="thread-decision-options"
								role="radiogroup"
								aria-label={decision.title}
							>
								{decision.options.map((option) => (
									<Radio
										key={option}
										name={`decision-${decision.id}`}
										label={decisionOptionLabel(option, t)}
										help={decisionOptionDescription(option, t) || undefined}
										value={option}
										disabled={busy}
										checked={draft.selectedOptions[0] === option}
										onChange={() => setDraft(decision.id, { selectedOptions: [option] })}
									/>
								))}
							</div>
						) : null}
						{hasOptions && isPoDecision ? (
							<TextArea
								label={t('app.threads.decision.otherAnswerLabel', 'Other answer')}
								help={t(
									'app.threads.decision.otherAnswerHelp',
									"If none of the options fit, describe what you want instead — it's used along with anything you checked above.",
								)}
								rows={2}
								disabled={busy}
								value={draft.freeText}
								onChange={(event) => setDraft(decision.id, { freeText: event.target.value })}
							/>
						) : null}
						{!hasOptions ? (
							<TextArea
								label={t('app.threads.decision.freeTextLabel', 'Your answer')}
								help={t(
									'app.threads.decision.freeTextHelp',
									'No preset options for this one: type the answer AIDO should work from.',
								)}
								rows={3}
								disabled={busy}
								value={draft.freeText}
								onChange={(event) => setDraft(decision.id, { freeText: event.target.value })}
							/>
						) : null}
					</m.article>
				);
			})}
			<Button
				variant="primary"
				loading={busy}
				disabled={busy || readyAnswers.length === 0}
				onClick={handleSubmit}
			>
				{t('app.threads.decision.submitAnswers', 'Send answers')}
			</Button>
		</section>
	);
}
