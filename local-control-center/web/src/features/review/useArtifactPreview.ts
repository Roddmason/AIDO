/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useState } from 'react';

import { downloadEvidenceArtifact, fetchEvidenceArtifact } from '../../api/client';
import type { ArtifactPayload } from '../../api/client';
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

	const openPreview = async (next: Artifact) => {
		const artifactId = String(next.id ?? '');
		const evidenceId = String(next.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setError('Artifact metadata is incomplete.');
			return;
		}
		setArtifact(next);
		setPayload(null);
		setError('');
		setLoadingId(artifactId);
		try {
			setPayload(await fetchEvidenceArtifact(token, evidenceId, artifactId));
		} catch (caught) {
			setError(caught instanceof Error ? caught.message : 'Artifact preview failed.');
		} finally {
			setLoadingId('');
		}
	};

	const download = async (next: Artifact) => {
		const artifactId = String(next.id ?? '');
		const evidenceId = String(next.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setError('Artifact metadata is incomplete.');
			return;
		}
		setError('');
		setDownloadingId(artifactId);
		try {
			await downloadEvidenceArtifact(token, evidenceId, artifactId, artifactDisplayName(next));
		} catch (caught) {
			setError(caught instanceof Error ? caught.message : 'Artifact download failed.');
		} finally {
			setDownloadingId('');
		}
	};

	const clear = () => {
		setArtifact(null);
		setPayload(null);
		setLoadingId('');
		setDownloadingId('');
		setError('');
	};

	return { artifact, payload, loadingId, downloadingId, error, openPreview, download, clear };
}
