/**
 * Hook backing the reviewer's artifact preview/download UI: token-protected reads
 * of any linked artifact through the evidence endpoint, with generation guards so
 * stale responses cannot clobber newer state. State container, not a renderer.
 */
import { useRef, useState } from 'react';
import type { ArtifactPayload } from '../../api/client';
import { downloadEvidenceArtifact, fetchEvidenceArtifact } from '../../api/client';
import type { Artifact } from '../../api/types';
import { artifactDisplayName } from '../../lib/artifacts';

type ArtifactPreview = {
	artifact: Artifact | null;
	payload: ArtifactPayload | null;
	loadingId: string;
	downloadingId: string;
	error: string;
	openPreview: (artifact: Artifact) => Promise<void>;
	download: (artifact: Artifact) => Promise<void>;
	clear: () => void;
};

/**
 * Token-protected preview/download for any linked artifact, read through the v1
 * evidence-artifact endpoint. Reusable so a reviewer can inspect arbitrary
 * artifacts (logs, QA reports) during a decision — the same capability the
 * retired Jobs & Approvals page offered. Callers must apply secret redaction
 * (redactVisibleText) to `payload.text` before rendering it.
 */
export function useArtifactPreview(token: string): ArtifactPreview {
	const [artifact, setArtifact] = useState<Artifact | null>(null);
	const [payload, setPayload] = useState<ArtifactPayload | null>(null);
	const [loadingId, setLoadingId] = useState('');
	const [downloadingId, setDownloadingId] = useState('');
	const [error, setError] = useState('');
	// Invalidates an in-flight preview when the drawer closes or a different
	// artifact is opened, so a slow response cannot overwrite newer state.
	const requestRef = useRef(0);
	// Separate generation for downloads so an abandoned download cannot bleed a
	// stale error into a later drawer, without cancelling an in-flight preview.
	const downloadRef = useRef(0);

	const openPreview = async (next: Artifact) => {
		const artifactId = String(next.id ?? '');
		const evidenceId = String(next.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setError('Artifact metadata is incomplete.');
			return;
		}
		const generation = ++requestRef.current;
		setArtifact(next);
		setPayload(null);
		setError('');
		setLoadingId(artifactId);
		try {
			const result = await fetchEvidenceArtifact(token, evidenceId, artifactId);
			if (requestRef.current === generation) setPayload(result);
		} catch (caught) {
			if (requestRef.current === generation)
				setError(caught instanceof Error ? caught.message : 'Artifact preview failed.');
		} finally {
			if (requestRef.current === generation) setLoadingId('');
		}
	};

	const download = async (next: Artifact) => {
		const artifactId = String(next.id ?? '');
		const evidenceId = String(next.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setError('Artifact metadata is incomplete.');
			return;
		}
		const generation = ++downloadRef.current;
		setError('');
		setDownloadingId(artifactId);
		try {
			await downloadEvidenceArtifact(token, evidenceId, artifactId, artifactDisplayName(next));
		} catch (caught) {
			if (downloadRef.current === generation)
				setError(caught instanceof Error ? caught.message : 'Artifact download failed.');
		} finally {
			if (downloadRef.current === generation) setDownloadingId('');
		}
	};

	const clear = () => {
		requestRef.current += 1;
		downloadRef.current += 1;
		setArtifact(null);
		setPayload(null);
		setLoadingId('');
		setDownloadingId('');
		setError('');
	};

	return { artifact, payload, loadingId, downloadingId, error, openPreview, download, clear };
}
