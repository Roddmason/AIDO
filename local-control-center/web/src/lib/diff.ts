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
		if ((line.startsWith('+') && !line.startsWith('+++')) || (line.startsWith('-') && !line.startsWith('---'))) return true;
	}
	return false;
}

export function findPatchArtifact(artifacts: Artifact[]) {
	return artifacts.find((artifact) => {
		const name = artifactDisplayName(artifact).toLowerCase();
		const kind = String(artifact.kind ?? '').toLowerCase();
		return name === 'diff.patch' || name.endsWith('.patch') || kind.includes('patch');
	}) ?? null;
}
