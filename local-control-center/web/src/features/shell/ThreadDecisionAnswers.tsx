/**
 * How a thread's pending decisions get answered: one form per decision (a radio group when it
 * offers options, a free-text field otherwise — so a decision never reaches a dead end with
 * nothing to select) plus a single "Send answers" button that resolves every decision the
 * operator answered together.
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
import { Button, Radio, TextArea } from '../../components/ui';
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

export type DecisionAnswer = {
	decisionId: string;
	selectedOptions: string[];
	freeText: string;
};

type Draft = { selectedOption: string; freeText: string };
type DraftState = Record<string, Draft>;

type ThreadDecisionAnswersProps = {
	decisions: ThreadDecision[];
	busy: boolean;
	onSubmit: (answers: DecisionAnswer[]) => Promise<void> | void;
};

function hasAnswer(decision: ThreadDecision, draft: Draft | undefined): boolean {
	if (!draft) return false;
	return decision.options.length > 0
		? draft.selectedOption.trim().length > 0
		: draft.freeText.trim().length > 0;
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
		.filter((decision) => hasAnswer(decision, drafts[decision.id]))
		.map((decision) => {
			const draft = drafts[decision.id];
			const hasOptions = decision.options.length > 0;
			return {
				decisionId: decision.id,
				selectedOptions: hasOptions && draft ? [draft.selectedOption] : [],
				freeText: !hasOptions && draft ? draft.freeText.trim() : '',
			};
		});

	if (answerable.length === 0) return null;

	const handleSubmit = async () => {
		if (readyAnswers.length === 0) return;
		await onSubmit(readyAnswers);
		setDrafts({});
	};

	return (
		<section
			className="thread-decision-answers"
			aria-label={t('app.threads.decisionTitle', 'Decision needed')}
		>
			{answerable.map((decision) => {
				const draft = drafts[decision.id] ?? { selectedOption: '', freeText: '' };
				const hasOptions = decision.options.length > 0;
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
						{hasOptions ? (
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
										checked={draft.selectedOption === option}
										onChange={() =>
											setDrafts((current) => ({
												...current,
												[decision.id]: { selectedOption: option, freeText: '' },
											}))
										}
									/>
								))}
							</div>
						) : (
							<TextArea
								label={t('app.threads.decision.freeTextLabel', 'Your answer')}
								help={t(
									'app.threads.decision.freeTextHelp',
									'No preset options for this one: type the answer AIDO should work from.',
								)}
								rows={3}
								disabled={busy}
								value={draft.freeText}
								onChange={(event) =>
									setDrafts((current) => ({
										...current,
										[decision.id]: { selectedOption: '', freeText: event.target.value },
									}))
								}
							/>
						)}
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
