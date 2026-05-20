import type { ArtifactPayload } from '../api/client';
import type { Dictionary } from '../api/types';

export function artifactMetadata(artifact: Dictionary): Dictionary {
	return (artifact.metadata && typeof artifact.metadata === 'object' ? artifact.metadata : {}) as Dictionary;
}

export function artifactDisplayName(artifact: Dictionary): string {
	const metadata = artifactMetadata(artifact);
	return String(metadata.name ?? artifact.id ?? 'artifact');
}

export function artifactMimeType(artifact: Dictionary, preview?: ArtifactPayload | null): string {
	const metadata = artifactMetadata(artifact);
	return String(preview?.contentType ?? metadata.mimeType ?? 'application/octet-stream');
}

export function artifactSizeLabel(artifact: Dictionary): string {
	const size = Number(artifactMetadata(artifact).sizeBytes ?? 0);
	return Number.isFinite(size) && size > 0 ? `${size} bytes` : 'not recorded';
}
