/**
 * Inspecciona patches unificados y listas de artefactos para la vista de revisión.
 * Detecta si un diff trae cambios reales, ubica los artefactos de patch/seguridad
 * por nombre o `kind`, y extrae los archivos tocados sin depender de un parser externo.
 */
import type { Artifact } from '../api/types';
import { artifactDisplayName } from './artifacts';

/**
 * `true` solo si hay líneas de contenido (`+`/`-`) dentro de algún hunk `@@`,
 * ignorando cabeceras (`+++`/`---`) y diffs vacíos o de puro metadata.
 */
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

/** Primer artefacto que parece un patch (`diff.patch`, `*.patch` o `kind` con `patch`). */
export function findPatchArtifact(artifacts: Artifact[]) {
	return (
		artifacts.find((artifact) => {
			const name = artifactDisplayName(artifact).toLowerCase();
			const kind = String(artifact.kind ?? '').toLowerCase();
			return name === 'diff.patch' || name.endsWith('.patch') || kind.includes('patch');
		}) ?? null
	);
}

/** Primer artefacto de hallazgos de seguridad, por nombre conocido o `kind`. */
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

/**
 * Conteo de archivos cambiados desde el `diffSummary` de la evidencia, tolerando
 * sus tres alias (`filesChanged`/`changedFiles`/`changed_files`); `null` si no aplica.
 */
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

/**
 * Lista de rutas tocadas por un patch. Usa las cabeceras `+++`/`---` (cayendo a la
 * ruta vieja cuando el destino es `/dev/null`) y, si no hay ninguna, recurre a las
 * líneas `diff --git`. Quita el prefijo `a/`/`b/` y deduplica preservando el orden.
 */
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
