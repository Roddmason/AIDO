/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import type { Artifact } from '../api/types';
import { artifactDisplayName } from './artifacts';

export function hasRealPatchChanges(text: string) {
	const trimmed = text.trim();
	if (!trimmed) return false;
	let inHunk = false;
	for (const line of trimmed.split(/\r?\n/)) {
		if (line.startsWith('diff --git')) inHunk = false;
		if (line.startsWith('@@')) {
			inHunk = true;
			continue;
		}
		if (!inHunk) continue;
		if (
			(line.startsWith('+') && !line.startsWith('+++')) ||
			(line.startsWith('-') && !line.startsWith('---'))
		)
			return true;
	}
	return false;
}

export function findPatchArtifact(artifacts: Artifact[]) {
	return (
		artifacts.find((artifact) => {
			const name = artifactDisplayName(artifact).toLowerCase();
			const kind = String(artifact.kind ?? '').toLowerCase();
			return name === 'diff.patch' || name.endsWith('.patch') || kind.includes('patch');
		}) ?? null
	);
}

export function findSecurityArtifact(artifacts: Artifact[]) {
	return (
		artifacts.find((artifact) => {
			const name = artifactDisplayName(artifact).toLowerCase();
			const kind = String(artifact.kind ?? '').toLowerCase();
			return (
				name === 'security-findings.json' ||
				(name.includes('security') && name.includes('finding')) ||
				kind.includes('security')
			);
		}) ?? null
	);
}

export function evidenceDiffChangedFiles(
	evidence: { diffSummary?: unknown } | null | undefined,
): number | null {
	const summary =
		evidence?.diffSummary && typeof evidence.diffSummary === 'object'
			? (evidence.diffSummary as Record<string, unknown>)
			: undefined;
	const changed = Number(summary?.filesChanged ?? summary?.changedFiles ?? summary?.changed_files);
	return Number.isFinite(changed) ? changed : null;
}

export function changedFilesFromPatch(text: string): string[] {
	const files: string[] = [];
	const seen = new Set<string>();
	const add = (raw: string) => {
		const path = raw.replace(/^[ab]\//, '').trim();
		if (!path || path === '/dev/null' || seen.has(path)) return;
		seen.add(path);
		files.push(path);
	};
	let pendingOld = '';
	for (const line of text.split(/\r?\n/)) {
		if (line.startsWith('--- ')) {
			pendingOld = line.slice(4).trim();
		} else if (line.startsWith('+++ ')) {
			const newPath = line.slice(4).trim();
			add(newPath === '/dev/null' ? pendingOld : newPath);
			pendingOld = '';
		}
	}
	if (!files.length) {
		for (const line of text.split(/\r?\n/)) {
			const match = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
			if (match) add(match[2]);
		}
	}
	return files;
}
