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
type BlockerType = RemediationActionRecord['blockerType'];
type ActionType = RemediationActionRecord['actionType'];

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
export const BLOCKER_COPY: Record<BlockerType, BlockerCopy> = {
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
	git_status_failed: {
		titleKey: 'app.threads.remediation.blocker.git_status_failed.title',
		titleFallback: 'Git status failed',
		explanationKey: 'app.threads.remediation.blocker.git_status_failed.explanation',
		explanationFallback:
			'AIDO could not read the project Git status, so it stopped before planning or execution.',
		settingsSection: 'workspaces',
		settingsLabelKey: 'app.threads.remediation.action.openWorkspaceSettings',
		settingsLabelFallback: 'Open workspace settings',
	},
	git_branch_missing: {
		titleKey: 'app.threads.remediation.blocker.git_branch_missing.title',
		titleFallback: 'Working branch required',
		explanationKey: 'app.threads.remediation.blocker.git_branch_missing.explanation',
		explanationFallback: 'AIDO needs a working branch before it makes changes to the project.',
	},
	git_remote_missing: {
		titleKey: 'app.threads.remediation.blocker.git_remote_missing.title',
		titleFallback: 'Git remote is unavailable',
		explanationKey: 'app.threads.remediation.blocker.git_remote_missing.explanation',
		explanationFallback:
			'The project has a configured Git remote, but the repository can no longer reach it.',
		settingsSection: 'workspaces',
		settingsLabelKey: 'app.threads.remediation.action.openWorkspaceSettings',
		settingsLabelFallback: 'Open workspace settings',
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
	resource_manager_unconfigured: {
		titleKey: 'app.threads.remediation.blocker.resource_manager_unconfigured.title',
		titleFallback: 'AI resource routing is not configured',
		explanationKey: 'app.threads.remediation.blocker.resource_manager_unconfigured.explanation',
		explanationFallback:
			'ResourceManager could not choose a model/runtime for the scheduled team role.',
		settingsSection: 'routing',
		settingsLabelKey: 'app.threads.remediation.action.openRouting',
		settingsLabelFallback: 'Open routing',
	},
	resource_manager_approval_required: {
		titleKey: 'app.threads.remediation.blocker.resource_manager_approval_required.title',
		titleFallback: 'AI resource approval required',
		explanationKey:
			'app.threads.remediation.blocker.resource_manager_approval_required.explanation',
		explanationFallback:
			'ResourceManager selected a model/runtime that needs review before execution.',
		settingsSection: 'routing',
		settingsLabelKey: 'app.threads.remediation.action.openRouting',
		settingsLabelFallback: 'Open routing',
	},
	team_scheduler_failed: {
		titleKey: 'app.threads.remediation.blocker.team_scheduler_failed.title',
		titleFallback: 'Team scheduling failed',
		explanationKey: 'app.threads.remediation.blocker.team_scheduler_failed.explanation',
		explanationFallback:
			'TeamScheduler could not produce the required role schedule, so execution is blocked.',
		settingsSection: 'team',
		settingsLabelKey: 'app.threads.remediation.action.openTeam',
		settingsLabelFallback: 'Open team',
	},
	technical_lead_planning_failed: {
		titleKey: 'app.threads.remediation.blocker.technical_lead_planning_failed.title',
		titleFallback: 'Technical planning is incomplete',
		explanationKey: 'app.threads.remediation.blocker.technical_lead_planning_failed.explanation',
		explanationFallback:
			'TechnicalLeadPlanner did not produce role tasks, so DeveloperAgent execution is blocked.',
		settingsSection: 'team',
		settingsLabelKey: 'app.threads.remediation.action.openTeam',
		settingsLabelFallback: 'Open team',
	},
	product_owner_output_invalid: {
		titleKey: 'app.threads.remediation.blocker.product_owner_output_invalid.title',
		titleFallback: 'ProductOwnerAgent output is incomplete',
		explanationKey: 'app.threads.remediation.blocker.product_owner_output_invalid.explanation',
		explanationFallback:
			'The ProductOwnerAgent did not produce a validated brief or backlog for this loop.',
		settingsSection: 'team',
		settingsLabelKey: 'app.threads.remediation.action.openTeam',
		settingsLabelFallback: 'Open team',
	},
	research_required: {
		titleKey: 'app.threads.remediation.blocker.research_required.title',
		titleFallback: 'Research evidence required',
		explanationKey: 'app.threads.remediation.blocker.research_required.explanation',
		explanationFallback:
			'AIDO must process a ResearchAgent job before accepting this high-impact decision.',
		settingsSection: 'research',
		settingsLabelKey: 'app.threads.remediation.action.openResearch',
		settingsLabelFallback: 'Open research',
	},
	workspace_root_missing: {
		titleKey: 'app.threads.remediation.blocker.workspace_root_missing.title',
		titleFallback: 'Workspace root missing',
		explanationKey: 'app.threads.remediation.blocker.workspace_root_missing.explanation',
		explanationFallback:
			'AIDO needs a project workspace root before it can allocate isolated execution.',
		settingsSection: 'workspaces',
		settingsLabelKey: 'app.threads.remediation.action.openWorkspaces',
		settingsLabelFallback: 'Open workspaces',
	},
	workspace_allocation_failed: {
		titleKey: 'app.threads.remediation.blocker.workspace_allocation_failed.title',
		titleFallback: 'Workspace allocation failed',
		explanationKey: 'app.threads.remediation.blocker.workspace_allocation_failed.explanation',
		explanationFallback:
			'AIDO could not create the isolated workspace or worktree needed for execution.',
		settingsSection: 'workspaces',
		settingsLabelKey: 'app.threads.remediation.action.openWorkspaces',
		settingsLabelFallback: 'Open workspaces',
	},
	review_diff_unavailable: {
		titleKey: 'app.threads.remediation.blocker.review_diff_unavailable.title',
		titleFallback: 'Review diff is unavailable',
		explanationKey: 'app.threads.remediation.blocker.review_diff_unavailable.explanation',
		explanationFallback:
			'The runtime finished, but AIDO could not capture real changed files for review.',
	},
	approval_unavailable: {
		titleKey: 'app.threads.remediation.blocker.approval_unavailable.title',
		titleFallback: 'Approval request is unavailable',
		explanationKey: 'app.threads.remediation.blocker.approval_unavailable.explanation',
		explanationFallback:
			'QA and security evidence are ready, but AIDO could not create the approval request.',
	},
	resource_learning_failed: {
		titleKey: 'app.threads.remediation.blocker.resource_learning_failed.title',
		titleFallback: 'Resource learning failed',
		explanationKey: 'app.threads.remediation.blocker.resource_learning_failed.explanation',
		explanationFallback:
			'AIDO could not persist the cost, token, or quality observation required before approval.',
		settingsSection: 'routing',
		settingsLabelKey: 'app.threads.remediation.action.openRouting',
		settingsLabelFallback: 'Open routing',
	},
	project_assessment_failed: {
		titleKey: 'app.threads.remediation.blocker.project_assessment_failed.title',
		titleFallback: 'Project assessment failed',
		explanationKey: 'app.threads.remediation.blocker.project_assessment_failed.explanation',
		explanationFallback:
			'AIDO could not read enough project context to hand off safely to ProductOwnerAgent.',
		settingsSection: 'workspaces',
		settingsLabelKey: 'app.threads.remediation.action.openWorkspaces',
		settingsLabelFallback: 'Open workspaces',
	},
	functionality_memory_decision_required: {
		titleKey: 'app.threads.remediation.blocker.functionality_memory_decision_required.title',
		titleFallback: 'Similar functionality already exists',
		explanationKey:
			'app.threads.remediation.blocker.functionality_memory_decision_required.explanation',
		explanationFallback:
			'AIDO found existing work and needs your decision before creating or changing anything.',
	},
	thread_similarity_decision_required: {
		titleKey: 'app.threads.remediation.blocker.thread_similarity_decision_required.title',
		titleFallback: 'Similar thread found',
		explanationKey:
			'app.threads.remediation.blocker.thread_similarity_decision_required.explanation',
		explanationFallback:
			'AIDO found a related thread and needs your decision before starting duplicate work.',
	},
	thread_intake_decision_required: {
		titleKey: 'app.threads.remediation.blocker.thread_intake_decision_required.title',
		titleFallback: 'Thread decision required',
		explanationKey: 'app.threads.remediation.blocker.thread_intake_decision_required.explanation',
		explanationFallback:
			'AIDO needs you to choose one of the available options before it queues the loop.',
	},
};

/** Button label + execution kind for every backend action type. */
export const ACTION_COPY: Record<ActionType, ActionCopy> = {
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
	add_remote: {
		labelKey: 'app.threads.remediation.action.addRemote',
		labelFallback: 'Re-add remote',
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
	check_network_access: {
		labelKey: 'app.threads.remediation.action.checkNetworkAccess',
		labelFallback: 'Check network access',
		kind: 'execute',
	},
	answer_question: {
		labelKey: 'app.threads.remediation.action.answerQuestion',
		labelFallback: 'Answer question',
		kind: 'execute',
	},
	approve_resource_decision: {
		labelKey: 'app.threads.remediation.action.approveResourceDecision',
		labelFallback: 'Approve resource',
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
	/** Backend record behind the action; settings actions keep it so payload hints are not lost. */
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
		const seenActionTypes = new Set<string>();

		if (copy.settingsSection) {
			const contextualSettingsRecord = records.find((record) => {
				const actionCopy = ACTION_COPY[record.actionType];
				if (actionCopy?.kind !== 'settings') return false;
				const section =
					payloadString(record.payload, 'section') || actionCopy.section || 'providers-cli';
				return section === copy.settingsSection;
			});
			actions.push({
				id: `${key}:settings:${copy.settingsSection}`,
				labelKey: copy.settingsLabelKey ?? 'app.threads.remediation.action.openConfiguration',
				labelFallback: copy.settingsLabelFallback ?? 'Open configuration',
				kind: 'settings',
				section: copy.settingsSection,
				remediation: contextualSettingsRecord,
			});
			coveredSections.add(copy.settingsSection);
			if (contextualSettingsRecord) seenActionTypes.add(contextualSettingsRecord.actionType);
		}

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
					remediation: record,
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
