/**
 * Workbench Diff panel: fetches the evidence patch artifact and renders the (redacted)
 * diff. Gates review by refusing to show a verdict unless a real patch with actual
 * additions/deletions exists — diff refs alone do not prove code changed.
 * @author Rodrigo Mason
 */
import { useEffect, useMemo, useState } from 'react';

import { fetchEvidenceArtifact } from '../../../api/client';
import type { Overview } from '../../../api/types';
import { StatusChip as Badge } from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';
import { artifactDisplayName } from '../../../lib/artifacts';
import {
	changedFilesFromPatch,
	evidenceDiffChangedFiles,
	findPatchArtifact,
	hasRealPatchChanges,
} from '../../../lib/diff';
import { redactVisibleText } from '../../../lib/redaction';

type EvidencePackage = Overview['evidencePackages'][number];

export function WorkbenchDiffPanel({
	token,
	evidenceId,
	artifacts,
	evidencePackage,
}: {
	token: string;
	evidenceId: string;
	artifacts: Overview['artifacts'];
	evidencePackage: EvidencePackage | null;
}) {
	const { t } = useI18n();
	const [text, setText] = useState('');
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState('');

	const patchArtifact = useMemo(() => {
		const scoped = evidenceId
			? artifacts.filter((artifact) => artifact.evidencePackageId === evidenceId)
			: artifacts;
		return findPatchArtifact(scoped.length ? scoped : artifacts);
	}, [artifacts, evidenceId]);

	const packageId = patchArtifact?.evidencePackageId ?? evidenceId;
	const patchArtifactId = patchArtifact?.id ?? '';

	// biome-ignore lint/correctness/useExhaustiveDependencies: patchArtifactId is the stable identity for patchArtifact; keying on object identity refetches on every 5s poll and flickers the diff.
	useEffect(() => {
		if (!patchArtifact || !packageId) {
			setText('');
			setError('');
			setLoading(false);
			return;
		}
		let cancelled = false;
		setLoading(true);
		setError('');
		fetchEvidenceArtifact(token, packageId, patchArtifact.id)
			.then((payload) => {
				if (!cancelled) setText(payload.text);
			})
			.catch((fetchError: unknown) => {
				if (!cancelled)
					setError(
						fetchError instanceof Error
							? fetchError.message
							: t('app.workbench.diff.error', 'The patch artifact could not be read.'),
					);
			})
			.finally(() => {
				if (!cancelled) setLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, [token, packageId, patchArtifactId, t]);

	if (!patchArtifact) {
		return (
			<div className="form-error" role="alert">
				<div className="stack compact">
					<div className="inline">
						<Badge tone="danger">{t('app.workbench.diff.blocked', 'review blocked')}</Badge>
						<strong>{t('app.workbench.diff.noPatchTitle', 'No real diff to review')}</strong>
					</div>
					<span>
						{t(
							'app.workbench.diff.noPatchBody',
							'No downloadable patch artifact was produced; diff refs alone do not prove code changes. Approval stays blocked until a real patch exists.',
						)}
					</span>
				</div>
			</div>
		);
	}
	if (loading) {
		return (
			<div className="empty-state" aria-busy="true">
				<strong>{t('app.workbench.diff.loadingTitle', 'Loading diff artifact')}</strong>
				<span>
					{t(
						'app.workbench.diff.loadingBody',
						'The patch is read through the protected artifact endpoint.',
					)}
				</span>
			</div>
		);
	}
	if (error) {
		return (
			<div className="form-error" role="alert">
				{error}
			</div>
		);
	}

	const realChanges = hasRealPatchChanges(text);
	if (!realChanges) {
		return (
			<div className="form-error" role="alert">
				<div className="stack compact">
					<div className="inline">
						<Badge tone="danger">{t('app.workbench.diff.blocked', 'review blocked')}</Badge>
						<strong>{t('app.workbench.diff.emptyPatchTitle', 'Patch artifact is empty')}</strong>
						<span className="mono">{artifactDisplayName(patchArtifact)}</span>
					</div>
					<span>
						{t(
							'app.workbench.diff.emptyPatchBody',
							'No additions or deletions are recorded, so no changes are proven. Approval stays blocked.',
						)}
					</span>
				</div>
			</div>
		);
	}

	const changedFilesCount = evidenceDiffChangedFiles(evidencePackage);
	const changedFiles = changedFilesFromPatch(text);
	return (
		<div className="stack">
			<div className="inline">
				<Badge tone="ok">{t('app.workbench.diff.realChanges', 'real changes')}</Badge>
				<span className="mono">{artifactDisplayName(patchArtifact)}</span>
				{changedFilesCount !== null ? (
					<span className="mono muted">
						{t('app.workbench.diff.changedFiles', 'changed files')} {changedFilesCount}
					</span>
				) : null}
				{patchArtifact.hash ? (
					<span className="mono muted">sha256 {patchArtifact.hash}</span>
				) : null}
			</div>
			{changedFiles.length ? (
				<div className="stack compact">
					<div className="metric-label">
						{t('app.workbench.diff.changedFilesLabel', 'Changed files')}
					</div>
					<div className="stack compact">
						{changedFiles.map((file) => (
							<span className="mono" key={file}>
								{file}
							</span>
						))}
					</div>
				</div>
			) : null}
			<pre className="artifact-preview">{redactVisibleText(text)}</pre>
		</div>
	);
}
