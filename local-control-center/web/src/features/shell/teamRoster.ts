/**
 * Roster classification for the Thread Inspector's "Team" tab.
 *
 * Turns the flat agent-profile list into a manager-legible roster: it derives each agent's coarse
 * state — active, waiting, blocked, available or unconfigured — from its runtime availability and its
 * current (unreleased) assignment, and describes, from real governance fields only, why a runtime was
 * selected. Pure and side-effect free so the panel stays a thin render layer and the honest "unknown"
 * fallbacks live in one place instead of scattered across JSX. State names mirror the backend Team
 * Activity builder so the tab and the workbench drawer never disagree about what "active" means.
 * @author Rodrigo Mason
 */
import type { AgentProfile } from '../../api/types';

/**
 * The five roster buckets, in manager display order: the work in flight leads, the agent that needs a
 * fix comes next (a block costs the loop more than a queued assignment), then the idle bench.
 */
export const AGENT_STATE_ORDER = [
	'active',
	'blocked',
	'waiting',
	'available',
	'unconfigured',
] as const;

export type AgentState = (typeof AGENT_STATE_ORDER)[number];

/**
 * States that demand the operator's attention. Their groups stay expanded; every other group renders
 * collapsed behind its count, so an unwired bench of twenty-odd agents can never bury the live work.
 */
export const ATTENTION_STATES: ReadonlySet<AgentState> = new Set(['active', 'waiting', 'blocked']);

/** Assignment statuses that mean the agent is actively producing (mirrors the backend builder). */
const ACTIVE_ASSIGNMENT_STATUSES: ReadonlySet<string> = new Set([
	'active',
	'in_progress',
	'running',
	'accepted',
	'proposed',
]);

/** The current (unreleased) assignment an agent holds, resolved to display fields. */
export type AgentAssignment = {
	taskTitle: string;
	status: string;
	/** Canonical artifact the assignment is producing; empty string when none is recorded yet. */
	artifactId: string;
};

/** The governance signals that explain why an agent's runtime was (or was not yet) chosen. */
export type RuntimeSelection = {
	/** The provider the gateway actually selected; null while no runtime has been chosen. */
	provider: string | null;
	/** The providers still in the running when none is selected yet. */
	candidates: string[];
	/** The routing profile or role policy that governed the choice. */
	via: string | null;
	/** Capabilities the runtime had to satisfy. */
	capabilities: string[];
};

/** Reads the availability status, defaulting to `unknown` when the backend omitted it. */
function availabilityStatus(profile: AgentProfile): string {
	return profile.runtimeAvailability?.status ?? 'unknown';
}

/**
 * True when the agent is held by a runtime block or disabled for the active project — the states
 * that demand a fix before it can run.
 *
 * `configuration_required` is deliberately excluded even though the backend attaches a reason to it:
 * an agent with no provider candidates was never wired up, which is a setup gap (`unconfigured`), not
 * a runtime that stopped working. Reading its reason as a block would file the whole unwired bench
 * under Blocked and drown the agents an operator can actually unstick.
 */
export function isAgentBlocked(profile: AgentProfile): boolean {
	if (profile.projectOverride?.status === 'disabled') return true;
	const status = availabilityStatus(profile);
	if (status === 'configuration_required') return false;
	return (
		status === 'blocked' ||
		status === 'disabled' ||
		Boolean(profile.runtimeAvailability?.blockedReason)
	);
}

/**
 * Classify one agent into a roster state from its availability and current assignment.
 *
 * Precedence: blocked (needs a fix) → active/waiting (staffed) → available (idle, ready) →
 * unconfigured (no usable runtime yet). Honest by construction: an agent is only `active` when it
 * truly holds a producing assignment, only `available` when its runtime actually reports available.
 */
export function classifyAgentState(
	profile: AgentProfile,
	assignment: AgentAssignment | null,
): AgentState {
	if (isAgentBlocked(profile)) return 'blocked';
	if (assignment) {
		return ACTIVE_ASSIGNMENT_STATUSES.has(assignment.status) ? 'active' : 'waiting';
	}
	return availabilityStatus(profile) === 'available' ? 'available' : 'unconfigured';
}

/**
 * Describe why an agent's runtime was selected, from real governance fields only. Returns null when
 * the profile carries no selection signal at all, so an unknown rationale reads as honestly absent
 * rather than fabricated.
 *
 * A candidate is never reported as `provider`: an agent whose runtime is still pending must not read
 * as if the gateway had already picked one for it.
 */
export function describeRuntimeSelection(profile: AgentProfile): RuntimeSelection | null {
	const availability = profile.runtimeAvailability;
	const provider = availability?.selectedProviderId?.trim() || null;
	const candidates = (availability?.candidateProviderIds ?? []).filter(
		(id): id is string => typeof id === 'string' && id.trim().length > 0,
	);
	const capabilities = (availability?.requiredCapabilities ?? []).filter(
		(cap): cap is string => typeof cap === 'string' && cap.trim().length > 0,
	);
	const via = profile.routingProfileId?.trim() || profile.roleModelPolicyId?.trim() || null;
	if (!provider && !via && candidates.length === 0 && capabilities.length === 0) return null;
	return { provider, candidates, via, capabilities };
}
