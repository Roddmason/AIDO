/**
 * Pure presentation layer for blocker remediations.
 *
 * Turns the flat `/threads/{id}/remediations` records into user-facing {@link BlockerCardModel}s:
 * groups the persisted actions by the blocker they repair, resolves each blocker to a plain-language
 * title, a one-line "what happened" and a one-line "what this blocks" (all keyed for i18n), maps every
 * backend `actionType` to a labelled button with an execution kind, promotes the backend's `primary`
 * action to the head of the list and de-duplicates the settings-navigation actions a blocker already
 * exposes through its contextual primary. {@link buildFallbackCard} covers the honest gap: a run the
 * backend stopped without persisting any repair action. No React, no I/O — so the mapping is
 * unit-testable in isolation and the components stay thin.
 * @author Rodrigo Mason
 */

import type { RemediationActionRecord } from '../../api/client';

/** How the UI carries out one action: run the backend side effect, or navigate to a settings section. */
export type RemediationActionKind = 'execute' | 'settings';
type BlockerType = RemediationActionRecord['blockerType'];
type ActionType = RemediationActionRecord['actionType'];

/**
 * i18n descriptor for a blocker type: a headline, a one-line "what happened" for non-experts, and a
 * one-line "what this blocks" so the cost of leaving it unresolved is explicit.
 */
type BlockerCopy = {
	titleKey: string;
	titleFallback: string;
	explanationKey: string;
	explanationFallback: string;
	impactKey: string;
	impactFallback: string;
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
	impactKey: 'app.threads.remediation.blocker.generic.impact',
	impactFallback: 'The run stays stopped until this blocker is resolved.',
};

/**
 * Copy for a run the backend stopped without persisting a single repair action. It is not a real
 * blocker type: it exists so a blocked thread never renders as an empty panel with a raw event log.
 */
const UNRESOLVED_BLOCKER: BlockerCopy = {
	titleKey: 'app.threads.remediation.blocker.unresolved.title',
	titleFallback: 'Blocked without a repair plan',
	explanationKey: 'app.threads.remediation.blocker.unresolved.explanation',
	explanationFallback: 'AIDO stopped this run but did not persist any repair action for it.',
	impactKey: 'app.threads.remediation.blocker.unresolved.impact',
	impactFallback: 'The run stays stopped until you resolve the reported cause by hand.',
};

/** Plain-language copy for every blocker type the backend can raise. */
export const BLOCKER_COPY: Record<BlockerType, BlockerCopy> = {
	runtime_not_executable: {
		titleKey: 'app.threads.remediation.blocker.runtime_not_executable.title',
		titleFallback: 'No executable runtime',
		explanationKey: 'app.threads.remediation.blocker.runtime_not_executable.explanation',
		explanationFallback:
			'AIDO could not find a runtime it can run on this machine, so the work cannot start.',
		impactKey: 'app.threads.remediation.blocker.runtime_not_executable.impact',
		impactFallback: 'No agent can run until a runtime is available, so the loop stays blocked.',
		settingsSection: 'providers-cli',
		settingsLabelKey: 'app.threads.remediation.action.configureRuntime',
		settingsLabelFallback: 'Configure runtime',
	},
	runtime_auth_missing: {
		titleKey: 'app.threads.remediation.blocker.runtime_auth_missing.title',
		titleFallback: 'Runtime not authenticated',
		explanationKey: 'app.threads.remediation.blocker.runtime_auth_missing.explanation',
		explanationFallback: 'The selected runtime needs credentials before AIDO can use it.',
		impactKey: 'app.threads.remediation.blocker.runtime_auth_missing.impact',
		impactFallback: 'Every agent step that uses this runtime stays blocked until it authenticates.',
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
		impactKey: 'app.threads.remediation.blocker.runtime_output_invalid.impact',
		impactFallback: 'The step produced no usable result, so the loop cannot reach the next stage.',
	},
	git_not_initialized: {
		titleKey: 'app.threads.remediation.blocker.git_not_initialized.title',
		titleFallback: 'Git is not initialized',
		explanationKey: 'app.threads.remediation.blocker.git_not_initialized.explanation',
		explanationFallback:
			'The project folder has no Git repository yet, so AIDO cannot track changes.',
		impactKey: 'app.threads.remediation.blocker.git_not_initialized.impact',
		impactFallback: 'AIDO cannot create branches, diffs, or delivery evidence for this project.',
	},
	git_dirty_tree: {
		titleKey: 'app.threads.remediation.blocker.git_dirty_tree.title',
		titleFallback: 'Uncommitted changes present',
		explanationKey: 'app.threads.remediation.blocker.git_dirty_tree.explanation',
		explanationFallback:
			'The working tree has uncommitted changes AIDO will not overwrite on its own.',
		impactKey: 'app.threads.remediation.blocker.git_dirty_tree.impact',
		impactFallback: 'Execution is paused so your uncommitted work is never overwritten.',
	},
	git_status_failed: {
		titleKey: 'app.threads.remediation.blocker.git_status_failed.title',
		titleFallback: 'Git status failed',
		explanationKey: 'app.threads.remediation.blocker.git_status_failed.explanation',
		explanationFallback:
			'AIDO could not read the project Git status, so it stopped before planning or execution.',
		impactKey: 'app.threads.remediation.blocker.git_status_failed.impact',
		impactFallback: 'Planning and execution stay blocked until AIDO can read the repository state.',
		settingsSection: 'workspaces',
		settingsLabelKey: 'app.threads.remediation.action.openWorkspaceSettings',
		settingsLabelFallback: 'Open workspace settings',
	},
	git_branch_missing: {
		titleKey: 'app.threads.remediation.blocker.git_branch_missing.title',
		titleFallback: 'Working branch required',
		explanationKey: 'app.threads.remediation.blocker.git_branch_missing.explanation',
		explanationFallback: 'AIDO needs a working branch before it makes changes to the project.',
		impactKey: 'app.threads.remediation.blocker.git_branch_missing.impact',
		impactFallback: 'AIDO will not modify the project until an isolated working branch exists.',
	},
	git_remote_missing: {
		titleKey: 'app.threads.remediation.blocker.git_remote_missing.title',
		titleFallback: 'Git remote is unavailable',
		explanationKey: 'app.threads.remediation.blocker.git_remote_missing.explanation',
		explanationFallback:
			'The project has a configured Git remote, but the repository can no longer reach it.',
		impactKey: 'app.threads.remediation.blocker.git_remote_missing.impact',
		impactFallback: 'Anything that needs the configured remote cannot complete.',
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
		impactKey: 'app.threads.remediation.blocker.gitleaks_missing.impact',
		impactFallback: 'The security gate cannot run, so no delivery reaches approval.',
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
		impactKey: 'app.threads.remediation.blocker.gitleaks_failed.impact',
		impactFallback: 'Delivery is held back to keep a detected secret out of the repository.',
	},
	qa_failed: {
		titleKey: 'app.threads.remediation.blocker.qa_failed.title',
		titleFallback: 'Quality checks failed',
		explanationKey: 'app.threads.remediation.blocker.qa_failed.explanation',
		explanationFallback:
			'Automated quality checks did not pass, so delivery was paused for review.',
		impactKey: 'app.threads.remediation.blocker.qa_failed.impact',
		impactFallback: 'The change stays out of approval until the quality checks pass.',
	},
	po_needs_input: {
		titleKey: 'app.threads.remediation.blocker.po_needs_input.title',
		titleFallback: 'A product decision is needed',
		explanationKey: 'app.threads.remediation.blocker.po_needs_input.explanation',
		explanationFallback: 'AIDO needs you to answer a product question before it keeps going.',
		impactKey: 'app.threads.remediation.blocker.po_needs_input.impact',
		impactFallback: 'The loop waits for your answer; nothing is planned or executed meanwhile.',
	},
	worker_not_running: {
		titleKey: 'app.threads.remediation.blocker.worker_not_running.title',
		titleFallback: 'Worker is not running',
		explanationKey: 'app.threads.remediation.blocker.worker_not_running.explanation',
		explanationFallback: 'This thread is queued but no local worker is processing it right now.',
		impactKey: 'app.threads.remediation.blocker.worker_not_running.impact',
		impactFallback: 'The queued run will not start until a worker picks it up.',
	},
	provider_missing_credentials: {
		titleKey: 'app.threads.remediation.blocker.provider_missing_credentials.title',
		titleFallback: 'Provider credentials missing',
		explanationKey: 'app.threads.remediation.blocker.provider_missing_credentials.explanation',
		explanationFallback: 'The provider needs credentials before AIDO can reach it.',
		impactKey: 'app.threads.remediation.blocker.provider_missing_credentials.impact',
		impactFallback: 'Every model call routed to this provider fails until credentials are stored.',
		settingsSection: 'providers-cli',
		settingsLabelKey: 'app.threads.remediation.action.openCredentials',
		settingsLabelFallback: 'Open credentials',
	},
	provider_health_failed: {
		titleKey: 'app.threads.remediation.blocker.provider_health_failed.title',
		titleFallback: 'Provider is unhealthy',
		explanationKey: 'app.threads.remediation.blocker.provider_health_failed.explanation',
		explanationFallback: 'The provider failed its health check, so AIDO stopped before using it.',
		impactKey: 'app.threads.remediation.blocker.provider_health_failed.impact',
		impactFallback: 'AIDO will not send work to a provider that failed its health check.',
	},
	resource_manager_unconfigured: {
		titleKey: 'app.threads.remediation.blocker.resource_manager_unconfigured.title',
		titleFallback: 'No eligible AI resource is available',
		explanationKey: 'app.threads.remediation.blocker.resource_manager_unconfigured.explanation',
		explanationFallback:
			'ResourceManager could not choose a model/runtime for the scheduled team role.',
		impactKey: 'app.threads.remediation.blocker.resource_manager_unconfigured.impact',
		impactFallback: 'No role gets a model or runtime, so execution never starts.',
		settingsSection: 'providers-cli',
		settingsLabelKey: 'app.threads.remediation.action.configureRuntime',
		settingsLabelFallback: 'Configure runtime',
	},
	resource_manager_privacy_blocked: {
		titleKey: 'app.threads.remediation.blocker.resource_manager_privacy_blocked.title',
		titleFallback: 'Project privacy policy requires a local AI resource',
		explanationKey: 'app.threads.remediation.blocker.resource_manager_privacy_blocked.explanation',
		explanationFallback:
			'The project is restricted to local AI resources, but ResourceManager found no executable local model/runtime.',
		impactKey: 'app.threads.remediation.blocker.resource_manager_privacy_blocked.impact',
		impactFallback:
			'The Product Loop stays blocked until a local runtime is configured; AIDO will not weaken the privacy policy automatically.',
		settingsSection: 'providers-cli',
		settingsLabelKey: 'app.threads.remediation.action.configureLocalRuntime',
		settingsLabelFallback: 'Configure local runtime',
	},
	resource_manager_approval_required: {
		titleKey: 'app.threads.remediation.blocker.resource_manager_approval_required.title',
		titleFallback: 'AI resource approval required',
		explanationKey:
			'app.threads.remediation.blocker.resource_manager_approval_required.explanation',
		explanationFallback:
			'ResourceManager selected a model/runtime that needs review before execution.',
		impactKey: 'app.threads.remediation.blocker.resource_manager_approval_required.impact',
		impactFallback: 'Execution waits for you to approve the selected model and its cost.',
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
		impactKey: 'app.threads.remediation.blocker.team_scheduler_failed.impact',
		impactFallback: 'Without a role schedule no agent is assigned and the loop stops here.',
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
		impactKey: 'app.threads.remediation.blocker.technical_lead_planning_failed.impact',
		impactFallback: 'DeveloperAgent has no tasks to execute, so implementation cannot start.',
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
		impactKey: 'app.threads.remediation.blocker.product_owner_output_invalid.impact',
		impactFallback: 'Without a validated brief or backlog, planning and execution cannot continue.',
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
		impactKey: 'app.threads.remediation.blocker.research_required.impact',
		impactFallback: 'The decision waits for research evidence before AIDO adopts it.',
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
		impactKey: 'app.threads.remediation.blocker.workspace_root_missing.impact',
		impactFallback: 'AIDO cannot isolate execution, so it refuses to touch the project folder.',
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
		impactKey: 'app.threads.remediation.blocker.workspace_allocation_failed.impact',
		impactFallback: 'Execution stays blocked because there is no isolated worktree to work in.',
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
		impactKey: 'app.threads.remediation.blocker.review_diff_unavailable.impact',
		impactFallback: 'Without real changed files there is nothing to review, so delivery stops.',
	},
	approval_unavailable: {
		titleKey: 'app.threads.remediation.blocker.approval_unavailable.title',
		titleFallback: 'Approval request is unavailable',
		explanationKey: 'app.threads.remediation.blocker.approval_unavailable.explanation',
		explanationFallback:
			'QA and security evidence are ready, but AIDO could not create the approval request.',
		impactKey: 'app.threads.remediation.blocker.approval_unavailable.impact',
		impactFallback: 'The finished work cannot reach the review board for your approval.',
	},
	resource_learning_failed: {
		titleKey: 'app.threads.remediation.blocker.resource_learning_failed.title',
		titleFallback: 'Resource learning failed',
		explanationKey: 'app.threads.remediation.blocker.resource_learning_failed.explanation',
		explanationFallback:
			'AIDO could not persist the cost, token, or quality observation required before approval.',
		impactKey: 'app.threads.remediation.blocker.resource_learning_failed.impact',
		impactFallback: 'Delivery approval is held back until the run cost and quality are recorded.',
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
		impactKey: 'app.threads.remediation.blocker.project_assessment_failed.impact',
		impactFallback: 'ProductOwnerAgent would plan blindly, so AIDO stops before the handoff.',
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
		impactKey: 'app.threads.remediation.blocker.functionality_memory_decision_required.impact',
		impactFallback: 'AIDO waits for your decision so it does not duplicate existing work.',
	},
	thread_similarity_decision_required: {
		titleKey: 'app.threads.remediation.blocker.thread_similarity_decision_required.title',
		titleFallback: 'Similar thread found',
		explanationKey:
			'app.threads.remediation.blocker.thread_similarity_decision_required.explanation',
		explanationFallback:
			'AIDO found a related thread and needs your decision before starting duplicate work.',
		impactKey: 'app.threads.remediation.blocker.thread_similarity_decision_required.impact',
		impactFallback: 'AIDO waits for your decision so it does not duplicate a related thread.',
	},
	thread_intake_decision_required: {
		titleKey: 'app.threads.remediation.blocker.thread_intake_decision_required.title',
		titleFallback: 'Thread decision required',
		explanationKey: 'app.threads.remediation.blocker.thread_intake_decision_required.explanation',
		explanationFallback:
			'AIDO needs you to choose one of the available options before it queues the loop.',
		impactKey: 'app.threads.remediation.blocker.thread_intake_decision_required.impact',
		impactFallback: 'The loop is not queued until you pick one of the offered options.',
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
	// Picking a different runtime is a choice, not a side effect: the backend refuses `switch_runtime`
	// unless the caller already knows the target id, so the button routes to the runtime catalog.
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

/** Section-specific labels for generic Settings actions whose destination changes their meaning. */
const SETTINGS_SECTION_ACTION_COPY: Record<
	string,
	Pick<ActionCopy, 'labelKey' | 'labelFallback'>
> = {
	routing: {
		labelKey: 'app.threads.remediation.action.openRouting',
		labelFallback: 'Open routing',
	},
};

/** One rendered action button on a blocker card. */
export type BlockerActionModel = {
	/** Stable id for the React key and for tracking the busy action. */
	id: string;
	labelKey: string;
	labelFallback: string;
	kind: RemediationActionKind;
	/** The single recommended repair, rendered as the card's primary button. */
	primary: boolean;
	/** Backend flagged the action as able to discard local work: confirm before executing it. */
	confirmationRequired: boolean;
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
	impactKey: string;
	impactFallback: string;
	/** The machine cause the backend reported (already secret-redacted), shown as the "Cause" fact. */
	cause: string;
	/** The blocker's raw reason (from payload), shown inside the collapsed technical detail. */
	reason: string;
	/** Actions the user can take, the primary repair first. */
	actions: BlockerActionModel[];
	/** The persisted records behind this card (for the technical detail and diagnostic copy). */
	remediations: RemediationActionRecord[];
};

/** An action before its position in the card is known; `buildBlockerCards` assigns `primary`. */
type DraftAction = Omit<BlockerActionModel, 'primary'>;

function payloadString(payload: unknown, key: string): string {
	if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return '';
	const value = (payload as Record<string, unknown>)[key];
	return typeof value === 'string' ? value : '';
}

function settingsSection(record: RemediationActionRecord, actionCopy: ActionCopy): string {
	return payloadString(record.payload, 'section') || actionCopy.section || 'providers-cli';
}

/** Settings actions are distinct only when their action type or destination section differs. */
function actionDedupeKey(record: RemediationActionRecord, actionCopy: ActionCopy): string {
	if (actionCopy.kind !== 'settings') return record.actionType;
	const section = settingsSection(record, actionCopy);
	return `${record.actionType}:${section}`;
}

/** Destructive remediations are refused by `execute` until the caller confirms them explicitly. */
function needsConfirmation(record: RemediationActionRecord | undefined): boolean {
	return Boolean(record?.confirmationRequired || record?.destructive);
}

/**
 * Orders the actions so the recommended repair leads. The backend flags exactly one spec as `primary`
 * per blocker; records persisted before that flag existed fall back to the first action, which keeps
 * the contextual settings navigation in the lead position it has always had.
 */
function withPrimaryFirst(actions: DraftAction[]): BlockerActionModel[] {
	const primaryId =
		actions.find((action) => action.remediation?.primary === true)?.id ?? actions[0]?.id;
	return [
		...actions.filter((action) => action.id === primaryId),
		...actions.filter((action) => action.id !== primaryId),
	].map((action) => ({ ...action, primary: action.id === primaryId }));
}

/**
 * Groups pending remediations into one card per blocker (same loop + stage + blocker type), resolves
 * their copy, and builds the ordered action list: a contextual settings primary first (when the fix
 * lives in Settings), then the backend actions — dropping any settings action the primary already
 * covers so the same section is not offered twice — and finally hoisting the backend's primary repair.
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
		const actions: DraftAction[] = [];
		const coveredSections = new Set<string>();
		const seenActionKeys = new Set<string>();

		if (copy.settingsSection) {
			const contextualSettingsRecord = records.find((record) => {
				const actionCopy = ACTION_COPY[record.actionType];
				if (actionCopy?.kind !== 'settings') return false;
				const section = settingsSection(record, actionCopy);
				return section === copy.settingsSection;
			});
			actions.push({
				id: `${key}:settings:${copy.settingsSection}`,
				labelKey: copy.settingsLabelKey ?? 'app.threads.remediation.action.openConfiguration',
				labelFallback: copy.settingsLabelFallback ?? 'Open configuration',
				kind: 'settings',
				confirmationRequired: false,
				section: copy.settingsSection,
				remediation: contextualSettingsRecord,
			});
			coveredSections.add(copy.settingsSection);
			if (contextualSettingsRecord) {
				const actionCopy = ACTION_COPY[contextualSettingsRecord.actionType];
				seenActionKeys.add(actionDedupeKey(contextualSettingsRecord, actionCopy));
			}
		}

		for (const record of records) {
			const actionCopy = ACTION_COPY[record.actionType];
			if (!actionCopy) continue;
			const dedupeKey = actionDedupeKey(record, actionCopy);
			if (seenActionKeys.has(dedupeKey)) continue;
			seenActionKeys.add(dedupeKey);
			if (actionCopy.kind === 'settings') {
				const section = settingsSection(record, actionCopy);
				if (coveredSections.has(section)) continue;
				coveredSections.add(section);
				const sectionActionCopy = SETTINGS_SECTION_ACTION_COPY[section] ?? actionCopy;
				actions.push({
					id: `${record.id}:settings`,
					labelKey: sectionActionCopy.labelKey,
					labelFallback: sectionActionCopy.labelFallback,
					kind: 'settings',
					confirmationRequired: false,
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
				confirmationRequired: needsConfirmation(record),
				remediation: record,
			});
		}

		const reason = payloadString(first.payload, 'reason');
		cards.push({
			key,
			stage: first.stage,
			blockerType: first.blockerType,
			titleKey: copy.titleKey,
			titleFallback: copy.titleFallback,
			explanationKey: copy.explanationKey,
			explanationFallback: copy.explanationFallback,
			impactKey: copy.impactKey,
			impactFallback: copy.impactFallback,
			cause: first.technicalReason || reason || first.description,
			reason,
			actions: withPrimaryFirst(actions),
			remediations: records,
		});
	}
	return cards;
}

/**
 * Builds the card for a run the backend stopped without persisting any remediation. It carries no
 * actions and no records, so the host renders the universal recovery pair — open configuration and
 * copy the diagnostic — instead of leaving the blocked thread as raw technical text in the console.
 */
export function buildFallbackCard(stage: string, reason: string): BlockerCardModel {
	return {
		key: 'blocker-fallback',
		stage,
		blockerType: 'unresolved',
		titleKey: UNRESOLVED_BLOCKER.titleKey,
		titleFallback: UNRESOLVED_BLOCKER.titleFallback,
		explanationKey: UNRESOLVED_BLOCKER.explanationKey,
		explanationFallback: UNRESOLVED_BLOCKER.explanationFallback,
		impactKey: UNRESOLVED_BLOCKER.impactKey,
		impactFallback: UNRESOLVED_BLOCKER.impactFallback,
		cause: reason,
		reason,
		actions: [],
		remediations: [],
	};
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
