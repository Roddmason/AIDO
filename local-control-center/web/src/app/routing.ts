/**
 * Hash-based routing: maps `window.location.hash` to a canonical page, folding
 * legacy bookmarks and retired per-tab settings hashes onto their current
 * destination so old links keep resolving instead of 404-ing to home silently.
 */

import type { PageId } from './navigation';
import { pageIds } from './navigation';

/** The canonical destination the shell is currently showing. Backed by the
 *  URL hash; equals a {@link PageId} so the page router and navigation registry
 *  share one route vocabulary. */
export type AppRoute = PageId;

/**
 * Legacy and shorthand hashes that resolve to a current route, so old
 * bookmarks and the retired per-tab settings hashes keep working.
 */
const routeAliases: Record<string, AppRoute> = {
	active: 'projects',
	ide: 'workbench',
	workspace: 'workbench',
	command: 'workbench',
	runs: 'workflows',
	review: 'review-board',
	jobs: 'review-board',
	settings: 'settings-project',
	// Backward-compat: the four retired per-lane project hashes now resolve to the single
	// Projects surface; ProjectsPage reads the hash suffix to open the matching lane.
	'projects-active': 'projects',
	'projects-finished': 'projects',
	'projects-error': 'projects',
	'projects-cancelled': 'projects',
	// Backward-compat: resolve the retired per-tab settings hashes to their owning group.
	'settings-projects': 'settings-project',
	'settings-user': 'settings-advanced',
	'settings-cli': 'settings-runtime',
	'settings-api': 'settings-runtime',
	'settings-parameters': 'settings-advanced',
	'settings-maintainers': 'settings-advanced',
	'settings-defaults': 'settings-advanced',
};

/**
 * Splits the URL hash into its page token and query params. The hash carries one PageId token,
 * optionally followed by `?key=value` pairs — e.g. `#workflows?run=<id>` deep-links a run.
 */
export function splitHash(): { token: string; params: URLSearchParams } {
	const raw = window.location.hash.replace('#', '');
	const queryIndex = raw.indexOf('?');
	if (queryIndex < 0) return { token: raw, params: new URLSearchParams() };
	return {
		token: raw.slice(0, queryIndex),
		params: new URLSearchParams(raw.slice(queryIndex + 1)),
	};
}

/** Reads `window.location.hash`, resolving aliases and unknown values to the thread/loop shell
 *  (`threads`) — the primary Codex-style experience the shell now lands on. */
export function resolveHashRoute(): AppRoute {
	const { token } = splitHash();
	return routeAliases[token] ?? (pageIds.includes(token as PageId) ? (token as PageId) : 'threads');
}

/** The full shell location: the canonical page plus an optional selected run id. */
export type HashState = { page: AppRoute; runId: string | null };

/** Reads the full shell location (page + selected run) from the URL hash. */
export function resolveHashState(): HashState {
	const { params } = splitHash();
	const runId = params.get('run');
	return { page: resolveHashRoute(), runId: runId && runId.trim() ? runId : null };
}

/** Encodes a page (and optional selected run) into a URL hash value. */
export function encodeHash(page: AppRoute, runId?: string | null): string {
	return runId ? `${page}?run=${encodeURIComponent(runId)}` : page;
}
