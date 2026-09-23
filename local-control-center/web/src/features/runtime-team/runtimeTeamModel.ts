/**
 * Runtime team of a thread: selection shape, reader for the persisted `runConfiguration`, and the
 * helpers the composer panel uses to merge the backend's automatic split with manual choices.
 * Eligibility and the split are computed by the backend; nothing here re-derives them.
 * @author Rodrigo Mason
 */
import type {
	RuntimeTeamCandidate,
	RuntimeTeamCandidatesResponse,
	ThreadRunConfigurationRequest,
} from '../../api/client';
import type { StatusTone } from '../../components/ui';

export const TEAM_ROLES = ['product_owner', 'developer', 'architect', 'security'] as const;
export type TeamRole = (typeof TEAM_ROLES)[number];
/** The backend refuses to send or run while Product Owner or Developer lacks a runtime. */
export const REQUIRED_TEAM_ROLES: readonly TeamRole[] = ['product_owner', 'developer'];
/** Unassigned architect: the review is skipped. Unassigned security: deterministic scanners only. */
export const OPTIONAL_TEAM_ROLES: readonly TeamRole[] = ['architect', 'security'];
export type RoleRuntimes = Partial<Record<TeamRole, string>>;
export type RuntimeTeamSelection = { allowedRuntimes: string[]; roleRuntimes: RoleRuntimes };
/**
 * Persisted validation (including `policy_denied`, a project veto that is never selectable) plus the
 * panel-only states of an in-flight or not-attempted test.
 */
export type ValidationView = RuntimeTeamCandidate['validation']['status'] | 'running' | 'deferred';

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Reads `thread.metadata.runConfiguration`; null means the thread uses automatic routing. */
export function readRuntimeTeam(metadata: unknown): RuntimeTeamSelection | null {
	const configuration = isRecord(metadata) ? metadata.runConfiguration : null;
	if (!isRecord(configuration) || !Array.isArray(configuration.allowedRuntimes)) return null;
	const allowedRuntimes = configuration.allowedRuntimes.filter(
		(item): item is string => typeof item === 'string' && item.length > 0,
	);
	if (allowedRuntimes.length === 0) return null;
	const rawRoles = isRecord(configuration.roleRuntimes) ? configuration.roleRuntimes : {};
	const roleRuntimes: RoleRuntimes = {};
	for (const role of TEAM_ROLES) {
		const value = rawRoles[role];
		if (typeof value === 'string' && allowedRuntimes.includes(value)) roleRuntimes[role] = value;
	}
	return { allowedRuntimes, roleRuntimes };
}

/** Normalizes the backend's nullable role record into a sparse map. */
export function toRoleRuntimes(
	record: RuntimeTeamCandidatesResponse['suggestedRoleRuntimes'] | null | undefined,
): RoleRuntimes {
	const roles: RoleRuntimes = {};
	for (const role of TEAM_ROLES) {
		const value = record?.[role];
		if (typeof value === 'string' && value) roles[role] = value;
	}
	return roles;
}

/** Returns a copy with `role` set to `providerId`, or unassigned when `providerId` is empty. */
export function withRole(current: RoleRuntimes, role: TeamRole, providerId: string): RoleRuntimes {
	const next: RoleRuntimes = {};
	for (const item of TEAM_ROLES) {
		const value = item === role ? providerId : current[item];
		if (value) next[item] = value;
	}
	return next;
}

export function isEligible(candidate: RuntimeTeamCandidate, role: TeamRole): boolean {
	return candidate.eligibleRoles.includes(role);
}

/**
 * Manual roles keep a still-valid choice; every other role follows the backend split for the
 * current selection, so adding a runtime redistributes the automatic roles. A role whose split
 * has no valid proposal keeps its current valid runtime instead of flickering to empty.
 */
export function mergeRoleRuntimes(
	current: RoleRuntimes,
	suggested: RoleRuntimes,
	candidates: readonly RuntimeTeamCandidate[],
	allowed: readonly string[],
	manual: ReadonlySet<TeamRole>,
): RoleRuntimes {
	const valid = (providerId: string | undefined, role: TeamRole): providerId is string =>
		Boolean(providerId) &&
		allowed.includes(providerId as string) &&
		candidates.some(
			(candidate) => candidate.providerId === providerId && isEligible(candidate, role),
		);
	const merged: RoleRuntimes = {};
	for (const role of TEAM_ROLES) {
		const kept = current[role];
		const proposed = suggested[role];
		if (manual.has(role) && valid(kept, role)) merged[role] = kept;
		else if (valid(proposed, role)) merged[role] = proposed;
		else if (valid(kept, role)) merged[role] = kept;
	}
	return merged;
}

/**
 * Required roles without an assigned runtime, or assigned to a stale/failed/unvalidated runtime.
 * A runtime that becomes non-validated (stale, failed) after being assigned blocks Save.
 */
export function missingRequiredRoles(
	roleRuntimes: RoleRuntimes,
	candidates?: readonly RuntimeTeamCandidate[],
): TeamRole[] {
	return REQUIRED_TEAM_ROLES.filter((role) => {
		const assignedProvider = roleRuntimes[role];
		if (!assignedProvider) return true;
		// If candidates are provided, verify the assigned runtime is validated and eligible.
		if (candidates) {
			const candidate = candidates.find((c) => c.providerId === assignedProvider);
			if (candidate?.validation.status !== 'validated') return true;
		}
		return false;
	});
}

export function validationTone(status: ValidationView): StatusTone {
	switch (status) {
		case 'validated':
			return 'ok';
		case 'stale':
		case 'deferred':
			return 'warn';
		case 'failed':
		case 'policy_denied':
			return 'danger';
		case 'running':
			return 'info';
		default:
			return 'pending';
	}
}

/** Body for the run-configuration PATCH; `null` clears the team (automatic routing). */
export function runtimeTeamRequest(
	selection: RuntimeTeamSelection | null,
): ThreadRunConfigurationRequest {
	return {
		allowedRuntimes: selection?.allowedRuntimes ?? [],
		roleRuntimes: selection?.roleRuntimes ?? {},
	};
}
