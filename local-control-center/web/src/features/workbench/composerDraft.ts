/**
 * Per-project persistence for the unified Workbench composer draft. Keeps the typed prompt,
 * derived/edited title, intake mode and the governed Advanced options alive across session
 * switches, mode switches, route-aways and reloads — so a task is never re-typed. Stored as one
 * versioned JSON map keyed by project id; the version suffix retires stale shapes on upgrade.
 * @author Rodrigo Mason
 */

/** Intake intent for the composer: the simple conversation flow, or one of the governed change types. */
export type ComposerMode = 'conversation' | 'fix' | 'feature' | 'refactor' | 'tests';

/** A composer draft for a single project. */
export interface ComposerDraft {
	prompt: string;
	title: string;
	titleEdited: boolean;
	mode: ComposerMode;
	advanced: {
		targetPath: string;
		runChecks: boolean;
		requireReview: boolean;
		preferredRuntime: string;
		qaPreset: string;
		maxCostUsd: string;
	};
}

const COMPOSER_DRAFT_STORAGE_KEY = 'aido:workbench:composer-drafts:v1';

function readStoredDrafts(): Record<string, ComposerDraft> {
	try {
		const raw = window.localStorage.getItem(COMPOSER_DRAFT_STORAGE_KEY);
		const parsed = raw ? JSON.parse(raw) : null;
		return parsed && typeof parsed === 'object' ? (parsed as Record<string, ComposerDraft>) : {};
	} catch {
		return {};
	}
}

/** Returns the saved draft for a project, or null when none exists. */
export function readComposerDraft(projectId: string): ComposerDraft | null {
	if (!projectId) return null;
	return readStoredDrafts()[projectId] ?? null;
}

/** Saves (overwrites) the draft for a project. No-op without a project id. */
export function persistComposerDraft(projectId: string, draft: ComposerDraft) {
	if (!projectId) return;
	try {
		const all = readStoredDrafts();
		all[projectId] = draft;
		window.localStorage.setItem(COMPOSER_DRAFT_STORAGE_KEY, JSON.stringify(all));
	} catch {}
}

/** Drops the draft for a project (called after a successful conversation intake). */
export function clearComposerDraft(projectId: string) {
	if (!projectId) return;
	try {
		const all = readStoredDrafts();
		if (!(projectId in all)) return;
		delete all[projectId];
		window.localStorage.setItem(COMPOSER_DRAFT_STORAGE_KEY, JSON.stringify(all));
	} catch {}
}
