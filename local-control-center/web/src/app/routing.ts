/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { pageIds } from './navigation';
import type { PageId } from './navigation';

/** The canonical destination the shell is currently showing. Backed by the
 *  URL hash; equals a {@link PageId} so the page router and navigation registry
 *  share one route vocabulary. */
export type AppRoute = PageId;

/**
 * Legacy and shorthand hashes that resolve to a current route, so old
 * bookmarks and the retired per-tab settings hashes keep working.
 */
const routeAliases: Record<string, AppRoute> = {
	active: 'projects-active',
	ide: 'workbench',
	workspace: 'workbench',
	command: 'workbench',
	runs: 'workflows',
	review: 'review-board',
	jobs: 'review-board',
	settings: 'settings-project',
	// Backward-compat: resolve the retired per-tab settings hashes to their owning group.
	'settings-projects': 'settings-project',
	'settings-user': 'settings-advanced',
	'settings-cli': 'settings-runtime',
	'settings-api': 'settings-runtime',
	'settings-parameters': 'settings-advanced',
	'settings-maintainers': 'settings-advanced',
	'settings-defaults': 'settings-advanced',
};

/** Reads `window.location.hash`, resolving aliases and unknown values to `home`. */
export function resolveHashRoute(): AppRoute {
	const value = window.location.hash.replace('#', '');
	return routeAliases[value] ?? (pageIds.includes(value as PageId) ? (value as PageId) : 'home');
}
