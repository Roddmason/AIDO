/**
 * Lee los campos de presentación de un artefacto desde su `metadata` heterogéneo.
 * Normaliza nombre, MIME y tamaño a valores seguros para la UI, con fallbacks
 * cuando el metadata viene ausente, malformado o sin el campo esperado.
 * @author Rodrigo Mason
 */
import type { ArtifactPayload } from '../api/client';
import type { Artifact, Dictionary } from '../api/types';

/** Devuelve el `metadata` como diccionario, o `{}` si no es un objeto válido. */
export function artifactMetadata(artifact: Artifact): Dictionary {
	return (
		artifact.metadata && typeof artifact.metadata === 'object' ? artifact.metadata : {}
	) as Dictionary;
}

export function artifactDisplayName(artifact: Artifact): string {
	const metadata = artifactMetadata(artifact);
	return String(metadata.name ?? artifact.id ?? 'artifact');
}

/** Prioriza el `contentType` real del preview sobre el MIME declarado en metadata. */
export function artifactMimeType(artifact: Artifact, preview?: ArtifactPayload | null): string {
	const metadata = artifactMetadata(artifact);
	return String(preview?.contentType ?? metadata.mimeType ?? 'application/octet-stream');
}

/** Etiqueta legible del tamaño; `'not recorded'` si falta o no es un positivo finito. */
export function artifactSizeLabel(artifact: Artifact): string {
	const size = Number(artifactMetadata(artifact).sizeBytes ?? 0);
	return Number.isFinite(size) && size > 0 ? `${size} bytes` : 'not recorded';
}
