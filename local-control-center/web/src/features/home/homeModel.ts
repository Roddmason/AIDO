/**
 * Pure model layer for the Home masonry wall: selectors that distil the overview
 * payload into per-card data and the priority-ordered card list it renders.
 * No React, no I/O — plain control-plane arrays in, plain card descriptors out,
 * so the ordering/count rules stay unit-testable and the page stays presentational.
 * @author Rodrigo Mason
 */
import type { Overview, RuntimeProviders } from '../../api/types';

export type HomeProject = Overview['projects'][number];
export type HomeReview = Overview['actionRequests'][number];
export type HomeRun = Overview['workflows'][number];
export type HomeProvider = NonNullable<RuntimeProviders>['providers'][number];

const IN_PROGRESS_JOB_STATUSES = new Set(['running', 'queued']);

/** Descending comparator over ISO-ish timestamp strings (newest first). */
function compareNewest(a: string, b: string): number {
	if (a === b) return 0;
	return a > b ? -1 : 1;
}

/** Active projects the user can continue working on. */
export function selectActiveProjects(projects: Overview['projects']): HomeProject[] {
	return projects.filter((project) => project.status === 'active');
}

/** Action requests still awaiting a human decision. */
export function selectPendingReviews(actionRequests: Overview['actionRequests']): HomeReview[] {
	return actionRequests.filter((request) => request.status === 'pending');
}

/** Runtimes that cannot execute work yet (must be fixed before runs proceed). */
export function selectRuntimeBlockers(runtimeProviders: RuntimeProviders | null): HomeProvider[] {
	return (runtimeProviders?.providers ?? []).filter((provider) => !provider.executable);
}

/** Most recent runs, newest first, capped at `limit`. */
export function selectRecentRuns(workflows: Overview['workflows'], limit = 6): HomeRun[] {
	return [...workflows].sort((a, b) => compareNewest(a.updatedAt, b.updatedAt)).slice(0, limit);
}

/**
 * Runtimes the landing surfaces, following the operator's rule "show only what is enabled":
 * `attention` holds the providers the operator switched on (or a thread is using) that still cannot
 * run, one card each; every other non-executable provider is merely not set up, which is not an
 * error, so it only feeds the count of a single neutral "Set up runtimes" card, shown while no
 * runtime can execute work at all. The manual operator is a switch, not a runtime: left out.
 */
export function summarizeRuntimeBlockers(runtimeProviders: RuntimeProviders | null): {
	attention: HomeProvider[];
	notSetUp: number;
	executable: number;
} {
	const isRuntime = (provider: HomeProvider) => provider.kind !== 'manual';
	const providers = (runtimeProviders?.providers ?? []).filter(isRuntime);
	const blockers = selectRuntimeBlockers(runtimeProviders).filter(isRuntime);
	const attention = blockers.filter(
		(provider) => provider.enabled === true || provider.inUse === true,
	);
	return {
		attention,
		notSetUp: blockers.length - attention.length,
		executable: providers.length - blockers.length,
	};
}

/** Latest activity of a project: its own record or any of its jobs and runs, whichever is newest. */
export function projectLastActivity(
	project: HomeProject,
	jobs: Overview['jobs'],
	workflows: Overview['workflows'],
): string {
	let latest = project.updatedAt || project.createdAt || '';
	for (const item of [...jobs, ...workflows]) {
		if (item.projectId === project.id && compareNewest(item.updatedAt, latest) < 0) {
			latest = item.updatedAt;
		}
	}
	return latest;
}

/**
 * Whole units between `iso` and `now` for Intl.RelativeTimeFormat (negative = past). Picks the
 * largest unit that is at least one, so "3 hours ago" rather than "180 minutes ago". Null for an
 * unparseable timestamp, so the card simply omits the line.
 */
export function relativeTimeParts(
	iso: string,
	now: number = Date.now(),
): { value: number; unit: 'minute' | 'hour' | 'day' | 'month' | 'year' } | null {
	const parsed = Date.parse(iso);
	if (Number.isNaN(parsed)) return null;
	const minutes = Math.round((parsed - now) / 60_000);
	const steps: Array<[number, 'minute' | 'hour' | 'day' | 'month' | 'year']> = [
		[525_600, 'year'],
		[43_200, 'month'],
		[1_440, 'day'],
		[60, 'hour'],
	];
	for (const [size, unit] of steps) {
		if (Math.abs(minutes) >= size) return { value: Math.round(minutes / size), unit };
	}
	return { value: minutes, unit: 'minute' };
}

/**
 * Shortens a long project path to its last segments ("…/parent/name"): the tail is what tells two
 * projects apart, and a CSS end-ellipsis would cut exactly that part. Short paths pass through.
 */
export function compactPath(path: string, maxLength = 30): string {
	if (path.length <= maxLength) return path;
	const separator = path.includes('\\') && !path.includes('/') ? '\\' : '/';
	const segments = path.split(separator).filter(Boolean);
	let tail = segments.pop() ?? path;
	while (segments.length) {
		const next = `${segments[segments.length - 1]}${separator}${tail}`;
		if (next.length + 2 > maxLength) break;
		tail = next;
		segments.pop();
	}
	return `…${separator}${tail}`;
}

/** Count of a project's jobs that are still running or queued. */
export function projectInProgressCount(jobs: Overview['jobs'], projectId: string): number {
	return jobs.filter(
		(job) => job.projectId === projectId && IN_PROGRESS_JOB_STATUSES.has(String(job.status)),
	).length;
}

/** Count of a project's pending reviews, given the already-filtered pending list. */
export function projectPendingReviewCount(pendingReviews: HomeReview[], projectId: string): number {
	return pendingReviews.filter((request) => request.projectId === projectId).length;
}

/** One entry in the single masonry wall, discriminated by card kind. */
export type HomeCardItem =
	| {
			kind: 'workspace';
			key: string;
			project: HomeProject;
			inProgress: number;
			pendingReviews: number;
	  }
	| { kind: 'blocker'; key: string; provider: HomeProvider }
	| { kind: 'setup'; key: string; notSetUp: number }
	| { kind: 'review'; key: string; request: HomeReview }
	| { kind: 'run'; key: string; run: HomeRun };

/**
 * Builds the ordered card list for the single masonry wall. Priority favours
 * the "continue work" goal: active projects first, then the enabled runtimes that cannot run
 * (and one "set up runtimes" card while nothing can run), then pending reviews, then recent runs.
 * Pure: takes plain control-plane arrays and returns plain card descriptors.
 */
export function buildHomeGallery(input: {
	projects: Overview['projects'];
	actionRequests: Overview['actionRequests'];
	jobs: Overview['jobs'];
	workflows: Overview['workflows'];
	runtimeProviders: RuntimeProviders | null;
	reviewLimit?: number;
	runLimit?: number;
}): HomeCardItem[] {
	const pendingReviews = selectPendingReviews(input.actionRequests);
	const items: HomeCardItem[] = [];

	for (const project of selectActiveProjects(input.projects)) {
		items.push({
			kind: 'workspace',
			key: `workspace:${project.id}`,
			project,
			inProgress: projectInProgressCount(input.jobs, project.id),
			pendingReviews: projectPendingReviewCount(pendingReviews, project.id),
		});
	}

	const runtimes = summarizeRuntimeBlockers(input.runtimeProviders);
	for (const provider of runtimes.attention) {
		items.push({ kind: 'blocker', key: `blocker:${provider.id}`, provider });
	}
	if (runtimes.executable === 0 && runtimes.notSetUp > 0) {
		items.push({ kind: 'setup', key: 'setup:runtimes', notSetUp: runtimes.notSetUp });
	}

	for (const request of pendingReviews.slice(0, input.reviewLimit ?? 8)) {
		items.push({ kind: 'review', key: `review:${request.id}`, request });
	}

	for (const run of selectRecentRuns(input.workflows, input.runLimit ?? 6)) {
		items.push({ kind: 'run', key: `run:${run.id}`, run });
	}

	return items;
}
