/**
 * Pure presentation layer for blocker remediations.
 *
 * Turns the flat `/threads/{id}/remediations` records into user-facing {@link BlockerCardModel}s:
 * groups the persisted actions by the blocker they repair, resolves each blocker to a plain-language
 * title + one-line explanation (keyed for i18n), maps every backend `actionType` to a labelled button
 * with an execution kind, and de-duplicates the settings-navigation actions a blocker already exposes
 * through its contextual primary. No React, no I/O — so the mapping is unit-testable in isolation and
 * the components stay thin.
 * @author Rodrigo Mason
 */

import type { RemediationActionRecord } from '../../api/client';

/** How the UI carries out one action: run the backend side effect, or navigate to a settings section. */
export type RemediationActionKind = 'execute' | 'settings';

/** i18n descriptor for a blocker type: a headline and a one-line "what happened" for non-experts. */
type BlockerCopy = {
	titleKey: string;
	titleFallback: string;
	explanationKey: string;
	explanationFallback: string;
	/** When the fix lives in Settings, the section a contextual primary action should open. */
	settingsSection?: string;
	/** Label key for that contextual primary (defaults to the generic "Open configuration"). */
	settingsLabelKey?: string;
	settingsLabelFallback?: string;
};

/** i18n descriptor for a backend action type: its button label and how the UI executes it. */
type ActionCopy = {
	labelKey: string;
	labelFallback: string;
	kind: RemediationActionKind;
	/** Settings section to open when `kind === 'settings'` and the payload carries no explicit one. */
	section?: string;
};

const GENERIC_BLOCKER: BlockerCopy = {
	titleKey: 'app.threads.remediation.blocker.generic.title',
	titleFallback: 'Run blocked',
	explanationKey: 'app.threads.remediation.blocker.generic.explanation',
	explanationFallback: 'This run stopped and needs a manual step before it can continue.',
};

/** Plain-language copy for every blocker type the backend can raise. */
export const BLOCKER_COPY: Record<string, BlockerCopy> = {
	runtime_not_executable: {
		titleKey: 'app.threads.remediation.blocker.runtime_not_executable.title',
		titleFallback: 'No executable runtime',
		explanationKey: 'app.threads.remediation.blocker.runtime_not_executable.explanation',
		explanationFallback:
			'AIDO could not find a runtime it can run on this machine, so the work cannot start.',
		settingsSection: 'providers-cli',
		settingsLabelKey: 'app.threads.remediation.action.configureRuntime',
		settingsLabelFallback: 'Configure runtime',
	},
	runtime_auth_missing: {
		titleKey: 'app.threads.remediation.blocker.runtime_auth_missing.title',
		titleFallback: 'Runtime not authenticated',
		explanationKey: 'app.threads.remediation.blocker.runtime_auth_missing.explanation',
		explanationFallback: 'The selected runtime needs credentials before AIDO can use it.',
		settingsSection: 'providers-cli',
		settingsLabelKey: 'app.threads.remediation.action.configureRuntime',
		settingsLabelFallback: 'Configure runtime',
	},
	runtime_output_invalid: {
		titleKey: 'app.threads.remediation.blocker.runtime_output_invalid.title',
		titleFallback: 'Runtime returned invalid output',
		explanationKey: 'app.threads.remediation.blocker.runtime_output_invalid.explanation',
		explanationFallback:
			'The runtime answered in a shape AIDO could not use, so the step was stopped.',
	},
	git_not_initialized: {
		titleKey: 'app.threads.remediation.blocker.git_not_initialized.title',
		titleFallback: 'Git is not initialized',
		explanationKey: 'app.threads.remediation.blocker.git_not_initialized.explanation',
		explanationFallback:
			'The project folder has no Git repository yet, so AIDO cannot track changes.',
	},
	git_dirty_tree: {
		titleKey: 'app.threads.remediation.blocker.git_dirty_tree.title',
		titleFallback: 'Uncommitted changes present',
		explanationKey: 'app.threads.remediation.blocker.git_dirty_tree.explanation',
		explanationFallback:
			'The working tree has uncommitted changes AIDO will not overwrite on its own.',
	},
	git_branch_missing: {
		titleKey: 'app.threads.remediation.blocker.git_branch_missing.title',
		titleFallback: 'Working branch required',
		explanationKey: 'app.threads.remediation.blocker.git_branch_missing.explanation',
		explanationFallback: 'AIDO needs a working branch before it makes changes to the project.',
	},
	gitleaks_missing: {
		titleKey: 'app.threads.remediation.blocker.gitleaks_missing.title',
		titleFallback: 'Gitleaks is not installed',
		explanationKey: 'app.threads.remediation.blocker.gitleaks_missing.explanation',
		explanationFallback:
			'The secret scanner required by the security gate is not available on this machine.',
		settingsSection: 'providers-cli',
		settingsLabelKey: 'app.threads.remediation.action.openSecurityTools',
		settingsLabelFallback: 'Open security tools',
	},
	gitleaks_failed: {
		titleKey: 'app.threads.remediation.blocker.gitleaks_failed.title',
		titleFallback: 'Gitleaks blocked delivery',
		explanationKey: 'app.threads.remediation.blocker.gitleaks_failed.explanation',
		explanationFallback:
			'The secret scanner found something that must be removed before delivery continues.',
	},
	qa_failed: {
		titleKey: 'app.threads.remediation.blocker.qa_failed.title',
		titleFallback: 'Quality checks failed',
		explanationKey: 'app.threads.remediation.blocker.qa_failed.explanation',
		explanationFallback:
			'Automated quality checks did not pass, so delivery was paused for review.',
	},
	po_needs_input: {
		titleKey: 'app.threads.remediation.blocker.po_needs_input.title',
		titleFallback: 'A product decision is needed',
		explanationKey: 'app.threads.remediation.blocker.po_needs_input.explanation',
		explanationFallback: 'AIDO needs you to answer a product question before it keeps going.',
	},
	worker_not_running: {
		titleKey: 'app.threads.remediation.blocker.worker_not_running.title',
		titleFallback: 'Worker is not running',
		explanationKey: 'app.threads.remediation.blocker.worker_not_running.explanation',
		explanationFallback: 'This thread is queued but no local worker is processing it right now.',
	},
	provider_missing_credentials: {
		titleKey: 'app.threads.remediation.blocker.provider_missing_credentials.title',
		titleFallback: 'Provider credentials missing',
		explanationKey: 'app.threads.remediation.blocker.provider_missing_credentials.explanation',
		explanationFallback: 'The provider needs credentials before AIDO can reach it.',
		settingsSection: 'providers-cli',
		settingsLabelKey: 'app.threads.remediation.action.openCredentials',
		settingsLabelFallback: 'Open credentials',
	},
	provider_health_failed: {
		titleKey: 'app.threads.remediation.blocker.provider_health_failed.title',
		titleFallback: 'Provider is unhealthy',
		explanationKey: 'app.threads.remediation.blocker.provider_health_failed.explanation',
		explanationFallback: 'The provider failed its health check, so AIDO stopped before using it.',
	},
};

/** Button label + execution kind for every backend action type. */
export const ACTION_COPY: Record<string, ActionCopy> = {
	open_settings_section: {
		labelKey: 'app.threads.remediation.action.openConfiguration',
		labelFallback: 'Open configuration',
		kind: 'settings',
	},
	validate_runtime: {
		labelKey: 'app.threads.remediation.action.validateRuntime',
		labelFallback: 'Revalidate runtime',
		kind: 'execute',
	},
	switch_runtime: {
		labelKey: 'app.threads.remediation.action.switchRuntime',
		labelFallback: 'Switch to Ollama / API / CLI',
		kind: 'settings',
		section: 'providers-cli',
	},
	continue_plan_only: {
		labelKey: 'app.threads.remediation.action.continuePlanOnly',
		labelFallback: 'Continue plan-only',
		kind: 'execute',
	},
	git_init: {
		labelKey: 'app.threads.remediation.action.gitInit',
		labelFallback: 'Initialize Git',
		kind: 'execute',
	},
	create_branch: {
		labelKey: 'app.threads.remediation.action.createBranch',
		labelFallback: 'Create branch',
		kind: 'execute',
	},
	checkout_branch: {
		labelKey: 'app.threads.remediation.action.checkoutBranch',
		labelFallback: 'Switch branch',
		kind: 'execute',
	},
	run_gitleaks: {
		labelKey: 'app.threads.remediation.action.runGitleaks',
		labelFallback: 'Run gitleaks',
		kind: 'execute',
	},
	// Reuses the established "Run now" copy so the queued banner and the blocker card read the same.
	run_worker_once: {
		labelKey: 'app.threads.runNow',
		labelFallback: 'Run now',
		kind: 'execute',
	},
	answer_question: {
		labelKey: 'app.threads.remediation.action.answerQuestion',
		labelFallback: 'Answer question',
		kind: 'execute',
	},
	retry_loop: {
		labelKey: 'app.threads.remediation.action.retryLoop',
		labelFallback: 'Retry loop',
		kind: 'execute',
	},
	view_diff: {
		labelKey: 'app.threads.remediation.action.viewDiff',
		labelFallback: 'View diff',
		kind: 'execute',
	},
	save_patch: {
		labelKey: 'app.threads.remediation.action.savePatch',
		labelFallback: 'Save patch',
		kind: 'execute',
	},
};

/** One rendered action button on a blocker card. */
export type BlockerActionModel = {
	/** Stable id for the React key and for tracking the busy action. */
	id: string;
	labelKey: string;
	labelFallback: string;
	kind: RemediationActionKind;
	/** Present for `execute` actions: the backend record to execute. */
	remediation?: RemediationActionRecord;
	/** Present for `settings` actions: the section to open. */
	section?: string;
};

/** One blocker rendered as a card: the grouped, plain-language view of its remediation actions. */
export type BlockerCardModel = {
	key: string;
	stage: string;
	blockerType: string;
	titleKey: string;
	titleFallback: string;
	explanationKey: string;
	explanationFallback: string;
	/** The blocker's raw reason (from payload), shown inside the collapsed technical detail. */
	reason: string;
	/** Actions the user can take, contextual settings primary first. */
	actions: BlockerActionModel[];
	/** The persisted records behind this card (for the technical detail and diagnostic copy). */
	remediations: RemediationActionRecord[];
};

function payloadString(payload: unknown, key: string): string {
	if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return '';
	const value = (payload as Record<string, unknown>)[key];
	return typeof value === 'string' ? value : '';
}

/**
 * Groups pending remediations into one card per blocker (same loop + stage + blocker type), resolves
 * their copy, and builds the ordered action list: a contextual settings primary first (when the fix
 * lives in Settings), then the backend actions — dropping any settings action the primary already
 * covers so the same section is not offered twice.
 */
export function buildBlockerCards(remediations: RemediationActionRecord[]): BlockerCardModel[] {
	const groups = new Map<string, RemediationActionRecord[]>();
	for (const record of remediations) {
		if (record.status !== 'pending') continue;
		const key = `${record.loopId}|${record.stage}|${record.blockerType}`;
		const bucket = groups.get(key);
		if (bucket) bucket.push(record);
		else groups.set(key, [record]);
	}

	const cards: BlockerCardModel[] = [];
	for (const [key, records] of groups) {
		const first = records[0];
		const copy = BLOCKER_COPY[first.blockerType] ?? GENERIC_BLOCKER;
		const actions: BlockerActionModel[] = [];
		const coveredSections = new Set<string>();

		if (copy.settingsSection) {
			actions.push({
				id: `${key}:settings:${copy.settingsSection}`,
				labelKey: copy.settingsLabelKey ?? 'app.threads.remediation.action.openConfiguration',
				labelFallback: copy.settingsLabelFallback ?? 'Open configuration',
				kind: 'settings',
				section: copy.settingsSection,
			});
			coveredSections.add(copy.settingsSection);
		}

		const seenActionTypes = new Set<string>();
		for (const record of records) {
			if (seenActionTypes.has(record.actionType)) continue;
			seenActionTypes.add(record.actionType);
			const actionCopy = ACTION_COPY[record.actionType];
			if (!actionCopy) continue;
			if (actionCopy.kind === 'settings') {
				const section =
					payloadString(record.payload, 'section') || actionCopy.section || 'providers-cli';
				if (coveredSections.has(section)) continue;
				coveredSections.add(section);
				actions.push({
					id: `${record.id}:settings`,
					labelKey: actionCopy.labelKey,
					labelFallback: actionCopy.labelFallback,
					kind: 'settings',
					section,
				});
				continue;
			}
			actions.push({
				id: `${record.id}:execute`,
				labelKey: actionCopy.labelKey,
				labelFallback: actionCopy.labelFallback,
				kind: 'execute',
				remediation: record,
			});
		}

		cards.push({
			key,
			stage: first.stage,
			blockerType: first.blockerType,
			titleKey: copy.titleKey,
			titleFallback: copy.titleFallback,
			explanationKey: copy.explanationKey,
			explanationFallback: copy.explanationFallback,
			reason: payloadString(first.payload, 'reason'),
			actions,
			remediations: records,
		});
	}
	return cards;
}

/**
 * Builds the copy-to-clipboard diagnostic for one blocker: the stage, machine cause, human reason and
 * the (already secret-redacted) payload details, as pretty JSON an operator can paste into an issue.
 */
export function buildDiagnostic(card: BlockerCardModel): string {
	const first = card.remediations[0];
	const detailsSource =
		first?.payload && typeof first.payload === 'object' && !Array.isArray(first.payload)
			? (first.payload as Record<string, unknown>).details
			: undefined;
	return JSON.stringify(
		{
			stage: card.stage,
			blockerType: card.blockerType,
			reason: card.reason || undefined,
			descriptions: card.remediations.map((record) => record.description).filter(Boolean),
			details: detailsSource ?? {},
			remediationIds: card.remediations.map((record) => record.id),
		},
		null,
		2,
	);
}
