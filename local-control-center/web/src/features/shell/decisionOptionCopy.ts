/**
 * Plain-language copy for the options a thread decision offers. The backend keeps sending and
 * receiving the raw code (`continue_existing`, `Research`…); only the visible label and a short
 * description are translated here, so an operator never has to read an internal identifier. Intake
 * questions travel with their i18n keys (`metadata.questionKeys`), aligned one-to-one with the
 * English text in `metadata.questions`, and render in the active language.
 * @author Rodrigo Mason
 */

import type { ThreadDecision } from '../../api/types';

type Translate = (key: string, fallback?: string) => string;

type OptionCopy = {
	labelKey: string;
	labelFallback: string;
	descriptionKey: string;
	descriptionFallback: string;
};

const OPTION_COPY: Record<string, OptionCopy> = {
	continue_existing: {
		labelKey: 'app.threads.decision.option.continueExisting.label',
		labelFallback: 'Continue the existing work',
		descriptionKey: 'app.threads.decision.option.continueExisting.description',
		descriptionFallback: 'Resume the thread that already built this functionality.',
	},
	improve_existing: {
		labelKey: 'app.threads.decision.option.improveExisting.label',
		labelFallback: 'Improve what exists',
		descriptionKey: 'app.threads.decision.option.improveExisting.description',
		descriptionFallback: 'Build on the existing functionality instead of starting over.',
	},
	performance_pass: {
		labelKey: 'app.threads.decision.option.performancePass.label',
		labelFallback: 'Performance pass',
		descriptionKey: 'app.threads.decision.option.performancePass.description',
		descriptionFallback: 'Keep the behavior and focus on making it faster.',
	},
	create_new_anyway: {
		labelKey: 'app.threads.decision.option.createNewAnyway.label',
		labelFallback: 'Create a new one anyway',
		descriptionKey: 'app.threads.decision.option.createNewAnyway.description',
		descriptionFallback: 'Ignore the match and start separate work.',
	},
	Diagnosis: {
		labelKey: 'app.threads.decision.option.diagnosis.label',
		labelFallback: 'Diagnose',
		descriptionKey: 'app.threads.decision.option.diagnosis.description',
		descriptionFallback: 'Investigate the cause before deciding on a change.',
	},
	Implementation: {
		labelKey: 'app.threads.decision.option.implementation.label',
		labelFallback: 'Implement',
		descriptionKey: 'app.threads.decision.option.implementation.description',
		descriptionFallback: 'Plan and build the change with the agent team.',
	},
	Research: {
		labelKey: 'app.threads.decision.option.research.label',
		labelFallback: 'Research',
		descriptionKey: 'app.threads.decision.option.research.description',
		descriptionFallback: 'Answer from cited web sources; no code is changed.',
	},
	'Continue in plan-only mode': {
		labelKey: 'app.threads.decision.option.planOnly.label',
		labelFallback: 'Continue in plan-only mode',
		descriptionKey: 'app.threads.decision.option.planOnly.description',
		descriptionFallback: 'Produce a plan without executing anything.',
	},
};

/** Visible label for a decision option; unknown options are shown verbatim. */
export function decisionOptionLabel(option: string, t: Translate): string {
	const copy = Object.hasOwn(OPTION_COPY, option) ? OPTION_COPY[option] : undefined;
	return copy ? t(copy.labelKey, copy.labelFallback) : option;
}

/** One-line description for a decision option, or an empty string when there is none. */
export function decisionOptionDescription(option: string, t: Translate): string {
	const copy = Object.hasOwn(OPTION_COPY, option) ? OPTION_COPY[option] : undefined;
	return copy ? t(copy.descriptionKey, copy.descriptionFallback) : '';
}

function stringList(value: unknown): string[] {
	return Array.isArray(value) && value.every((item) => typeof item === 'string') ? value : [];
}

/** Decision prompt in the active language when the backend sent aligned question keys. */
export function decisionPromptText(decision: ThreadDecision, t: Translate): string {
	const metadata = decision.metadata ?? {};
	const keys = stringList(metadata.questionKeys);
	const questions = stringList(metadata.questions);
	if (!keys.length || keys.length !== questions.length) return decision.prompt;
	return keys.map((key, index) => t(key, questions[index])).join(' ');
}
