/**
 * Workbench Evidence panel: inspects an evidence package without leaving the workbench —
 * QA results, deliverable artifacts (preview/download), security findings, model/tool
 * calls and hashes. All previewed text is redacted before display.
 * @author Rodrigo Mason
 */
import { useEffect, useMemo, useState } from 'react';

import { downloadEvidenceArtifact, fetchEvidenceArtifact } from '../../../api/client';
import type { Overview } from '../../../api/types';
import { Disclosure } from '../../../components/Disclosure';
import { Badge, DataTable, Drawer, EmptyState, Surface } from '../../../components/primitives';
import { useI18n } from '../../../i18n/I18nProvider';
import { artifactDisplayName, artifactSizeLabel } from '../../../lib/artifacts';
import { evidenceDiffChangedFiles, findSecurityArtifact } from '../../../lib/diff';
import { shortId, toneForStatus } from '../../../lib/format';
import { redactVisibleText } from '../../../lib/redaction';

type EvidencePackage = Overview['evidencePackages'][number];
type PreviewState = { open: boolean; title: string; text: string; loading: boolean; error: string };

const CLOSED_PREVIEW: PreviewState = {
	open: false,
	title: '',
	text: '',
	loading: false,
	error: '',
};

export function WorkbenchEvidencePanel({
	token,
	evidenceId,
	evidencePackage,
	testResults,
	artifacts,
}: {
	token: string;
	evidenceId: string;
	evidencePackage: EvidencePackage | null;
	testResults: Overview['testResultRecords'];
	artifacts: Overview['artifacts'];
}) {
	const { t } = useI18n();
	const [preview, setPreview] = useState<PreviewState>(CLOSED_PREVIEW);
	const [downloadingId, setDownloadingId] = useState('');
	const [actionError, setActionError] = useState('');
	const [securityText, setSecurityText] = useState('');
	const [securityLoading, setSecurityLoading] = useState(false);
	const [securityError, setSecurityError] = useState('');

	const scopedArtifacts = useMemo(() => {
		if (!evidenceId) return artifacts;
		const scoped = artifacts.filter((artifact) => artifact.evidencePackageId === evidenceId);
		return scoped.length ? scoped : artifacts;
	}, [artifacts, evidenceId]);

	const scopedTests = useMemo(() => {
		if (!evidenceId) return testResults;
		const scoped = testResults.filter((result) => result.evidencePackageId === evidenceId);
		return scoped.length ? scoped : testResults;
	}, [testResults, evidenceId]);

	const securityArtifact = useMemo(() => findSecurityArtifact(scopedArtifacts), [scopedArtifacts]);
	const securityPackageId = securityArtifact?.evidencePackageId ?? evidenceId;

	useEffect(() => {
		setSecurityText('');
		setSecurityError('');
		if (!securityArtifact || !securityPackageId) {
			setSecurityLoading(false);
			return;
		}
		const artifactId = String(securityArtifact.id ?? '');
		if (!artifactId) return;
		let active = true;
		setSecurityLoading(true);
		fetchEvidenceArtifact(token, securityPackageId, artifactId)
			.then((payload) => {
				if (active) setSecurityText(payload.text);
			})
			.catch((fetchError: unknown) => {
				if (active)
					setSecurityError(
						fetchError instanceof Error
							? fetchError.message
							: t('app.workbench.evidence.securityError', 'Security findings could not be read.'),
					);
			})
			.finally(() => {
				if (active) setSecurityLoading(false);
			});
		return () => {
			active = false;
		};
	}, [securityArtifact, securityPackageId, token, t]);

	const openPreview = async (packageId: string, artifactId: string, title: string) => {
		setPreview({ open: true, title, text: '', loading: true, error: '' });
		try {
			const payload = await fetchEvidenceArtifact(token, packageId, artifactId);
			setPreview({
				open: true,
				title,
				text:
					payload.text ||
					t('app.workbench.evidence.binary', 'Binary artifact; download to inspect.'),
				loading: false,
				error: '',
			});
		} catch (fetchError) {
			setPreview({
				open: true,
				title,
				text: '',
				loading: false,
				error:
					fetchError instanceof Error
						? fetchError.message
						: t('app.workbench.evidence.previewError', 'Artifact could not be read.'),
			});
		}
	};

	const downloadArtifact = async (artifact: Overview['artifacts'][number]) => {
		const artifactId = String(artifact.id ?? '');
		const packageId = String(artifact.evidencePackageId ?? evidenceId ?? '');
		if (!artifactId || !packageId) {
			setActionError(t('app.workbench.evidence.incomplete', 'Artifact metadata is incomplete.'));
			return;
		}
		setActionError('');
		setDownloadingId(artifactId);
		try {
			await downloadEvidenceArtifact(token, packageId, artifactId, artifactDisplayName(artifact));
		} catch (downloadError) {
			setActionError(
				downloadError instanceof Error
					? downloadError.message
					: t('app.workbench.evidence.downloadError', 'Artifact download failed.'),
			);
		} finally {
			setDownloadingId('');
		}
	};

	if (!evidencePackage) {
		return (
			<EmptyState
				title={t('app.workbench.evidence.noPackageTitle', 'No evidence yet')}
				body={t(
					'app.workbench.evidence.noPackageBody',
					'Run a governed task to produce an evidence package; QA, hashes and findings appear here without leaving the workbench.',
				)}
			/>
		);
	}

	const changedFiles = evidenceDiffChangedFiles(evidencePackage);
	const modelCalls = Array.isArray(evidencePackage.modelCalls) ? evidencePackage.modelCalls : [];
	const toolCalls = Array.isArray(evidencePackage.toolCalls) ? evidencePackage.toolCalls : [];

	return (
		<div className="stack">
			<Surface title={t('app.workbench.evidence.packageTitle', 'Evidence package')}>
				<div className="inline">
					<Badge tone={toneForStatus(String(evidencePackage.qaVerdict))}>
						QA {String(evidencePackage.qaVerdict ?? 'not_started')}
					</Badge>
					<Badge>{String(evidencePackage.evidenceSource ?? 'operator_attested')}</Badge>
					<Badge>
						{scopedArtifacts.length} {t('app.workbench.evidence.artifacts', 'artifacts')}
					</Badge>
					{changedFiles !== null ? (
						<span className="mono">
							{t('app.workbench.evidence.changedFiles', 'changed files')} {changedFiles}
						</span>
					) : null}
					<span className="mono muted">{shortId(evidencePackage.id)}</span>
				</div>
			</Surface>

			<Surface title={t('app.workbench.evidence.qaTitle', 'QA results')}>
				<DataTable
					rows={scopedTests}
					empty={
						<EmptyState
							title={t('app.workbench.evidence.testsEmptyTitle', 'No QA results')}
							body={t(
								'app.workbench.evidence.testsEmptyBody',
								'QA commands record pass/fail results against an evidence package.',
							)}
						/>
					}
					columns={[
						{
							key: 'command',
							label: t('app.workbench.evidence.colCommand', 'Command'),
							render: (row) => <span className="mono">{row.command}</span>,
						},
						{
							key: 'status',
							label: t('app.workbench.evidence.colStatus', 'Status'),
							render: (row) => <Badge tone={toneForStatus(String(row.status))}>{row.status}</Badge>,
						},
						{
							key: 'duration',
							label: t('app.workbench.evidence.colDuration', 'Duration ms'),
							render: (row) => <span className="tnum">{row.durationMs ?? 'n/a'}</span>,
						},
					]}
				/>
			</Surface>

			<Surface title={t('app.workbench.evidence.securityTitle', 'Security findings')}>
				<div className="stack">
					{securityError ? (
						<div className="form-error" role="alert">
							{securityError}
						</div>
					) : null}
					{securityLoading ? (
						<div className="empty-state" aria-busy="true">
							<strong>
								{t('app.workbench.evidence.securityLoading', 'Loading security findings')}
							</strong>
							<span>
								{t(
									'app.workbench.evidence.securityLoadingBody',
									'Findings are read from the linked artifact.',
								)}
							</span>
						</div>
					) : null}
					{!securityArtifact ? (
						<EmptyState
							title={t(
								'app.workbench.evidence.securityEmptyTitle',
								'No security findings artifact',
							)}
							body={t(
								'app.workbench.evidence.securityEmptyBody',
								'No security-findings.json artifact is linked to this evidence package.',
							)}
						/>
					) : (
						<div className="inline">
							<Badge>{artifactDisplayName(securityArtifact)}</Badge>
							<span className="mono">
								sha256{' '}
								{String(
									securityArtifact.hash ?? t('app.workbenchEvidence.notRecorded', 'not recorded'),
								)}
							</span>
						</div>
					)}
				</div>
			</Surface>

			<Surface title={t('app.workbench.evidence.deliverablesTitle', 'Deliverables & hashes')}>
				{actionError ? (
					<div className="form-error" role="alert">
						{actionError}
					</div>
				) : null}
				<DataTable
					rows={scopedArtifacts}
					empty={
						<EmptyState
							title={t('app.workbench.evidence.deliverablesEmptyTitle', 'No deliverables yet')}
							body={t(
								'app.workbench.evidence.deliverablesEmptyBody',
								'Artifacts, patches and reports appear after real execution.',
							)}
						/>
					}
					columns={[
						{
							key: 'name',
							label: t('app.workbench.evidence.colName', 'Name'),
							render: (row) => <span className="mono">{artifactDisplayName(row)}</span>,
						},
						{
							key: 'kind',
							label: t('app.workbench.evidence.colKind', 'Kind'),
							render: (row) => <Badge>{row.kind}</Badge>,
						},
						{
							key: 'size',
							label: t('app.workbench.evidence.colSize', 'Size'),
							render: (row) => artifactSizeLabel(row),
						},
						{
							key: 'sha256',
							label: t('app.workbench.evidence.colHash', 'SHA-256'),
							render: (row) => (
								<span className="mono">
									{String(row.hash ?? t('app.workbenchEvidence.notRecorded', 'not recorded'))}
								</span>
							),
						},
						{
							key: 'action',
							label: t('app.workbench.evidence.colAction', 'Action'),
							render: (row) => {
								const packageId = String(row.evidencePackageId ?? evidenceId ?? '');
								const downloading = downloadingId === String(row.id ?? '');
								if (!packageId)
									return (
										<span className="muted">
											{t('app.workbench.evidence.noPreview', 'no preview')}
										</span>
									);
								return (
									<div className="inline" aria-busy={downloading}>
										<button
											className="button"
											type="button"
											aria-label={`${t('app.workbenchEvidence.previewArtifact', 'Preview artifact')} ${artifactDisplayName(row)}`}
											onClick={() => void openPreview(packageId, row.id, artifactDisplayName(row))}
										>
											{t('app.workbench.evidence.preview', 'Preview')}
										</button>
										<button
											className="button"
											type="button"
											aria-label={`${t('app.workbenchEvidence.downloadArtifact', 'Download artifact')} ${artifactDisplayName(row)}`}
											disabled={downloading}
											onClick={() => void downloadArtifact(row)}
										>
											{downloading
												? t('app.workbench.evidence.downloading', 'Downloading')
												: t('app.workbench.evidence.download', 'Download')}
										</button>
									</div>
								);
							},
						},
					]}
				/>
			</Surface>

			<Disclosure
				title={t('app.workbench.evidence.developerTitle', 'Developer details')}
				summary={t(
					'app.workbench.evidence.developerHint',
					'Raw model, tool, policy, hash and security data for debugging.',
				)}
			>
				<div className="stack">
					<div className="inline">
						<Badge>
							{modelCalls.length} {t('app.workbench.evidence.modelCalls', 'model calls')}
						</Badge>
						<Badge>
							{toolCalls.length} {t('app.workbench.evidence.toolCalls', 'tool calls')}
						</Badge>
					</div>
					<div>
						<div className="metric-label">
							{t('app.workbench.evidence.modelCalls', 'model calls')}
						</div>
						<pre className="artifact-preview">{redactVisibleText(modelCalls, '[]')}</pre>
					</div>
					<div>
						<div className="metric-label">
							{t('app.workbench.evidence.toolCalls', 'tool calls')}
						</div>
						<pre className="artifact-preview">{redactVisibleText(toolCalls, '[]')}</pre>
					</div>
					<div>
						<div className="metric-label">
							{t('app.workbench.evidence.policy', 'policy decisions & approvals')}
						</div>
						<pre className="artifact-preview">
							{redactVisibleText(
								{
									policyDecisions: evidencePackage.policyDecisions,
									approvals: evidencePackage.approvals,
								},
								'{}',
							)}
						</pre>
					</div>
					<div>
						<div className="metric-label">
							{t('app.workbench.evidence.hashesTitle', 'Artifact hashes')}
						</div>
						<pre className="artifact-preview">
							{redactVisibleText(evidencePackage.hashes, '{}')}
						</pre>
					</div>
					{securityText ? (
						<div>
							<div className="metric-label">
								{t('app.workbench.evidence.securityTitle', 'Security findings')}
							</div>
							<pre className="artifact-preview">{redactVisibleText(securityText, '')}</pre>
						</div>
					) : null}
				</div>
			</Disclosure>

			<Drawer
				label={preview.title || t('app.workbench.evidence.artifact', 'Artifact')}
				open={preview.open}
				onClose={() => setPreview(CLOSED_PREVIEW)}
			>
				{preview.loading ? (
					<div className="empty-state" aria-busy="true">
						<strong>{t('app.workbench.evidence.loading', 'Loading artifact')}</strong>
					</div>
				) : preview.error ? (
					<div className="form-error" role="alert">
						{preview.error}
					</div>
				) : (
					<pre className="artifact-preview">{redactVisibleText(preview.text)}</pre>
				)}
			</Drawer>
		</div>
	);
}
