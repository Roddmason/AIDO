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
	| { kind: 'review'; key: string; request: HomeReview }
	| { kind: 'run'; key: string; run: HomeRun };

/**
 * Builds the ordered card list for the single masonry wall. Priority favours
 * the "continue work" goal: active projects first, then runtime blockers (they
 * stop execution), then pending reviews (need a decision), then recent runs.
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

	for (const provider of selectRuntimeBlockers(input.runtimeProviders)) {
		items.push({ kind: 'blocker', key: `blocker:${provider.id}`, provider });
	}

	for (const request of pendingReviews.slice(0, input.reviewLimit ?? 8)) {
		items.push({ kind: 'review', key: `review:${request.id}`, request });
	}

	for (const run of selectRecentRuns(input.workflows, input.runLimit ?? 6)) {
		items.push({ kind: 'run', key: `run:${run.id}`, run });
	}

	return items;
}
