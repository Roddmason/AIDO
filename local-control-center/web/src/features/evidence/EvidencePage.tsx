/**
 * Evidence & QA route page: lists evidence packages and audits package detail plus
 * patch and security-findings artifacts through token-protected endpoints, enforcing
 * "proof before approval" and redacting artifact text before rendering it inline.
 * @author Rodrigo Mason
 */
import { useEffect, useMemo, useState } from 'react';
import type { ArtifactPayload, EvidenceDetailResponse } from '../../api/client';
import {
	downloadEvidenceArtifact,
	fetchEvidenceArtifact,
	getEvidenceDetail,
} from '../../api/client';
import type { Artifact, Overview } from '../../api/types';
import {
	StatusChip as Badge,
	Button,
	DataTable,
	Drawer,
	EmptyState,
	ErrorState,
	PageHeader,
	Surface,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { artifactDisplayName, artifactMimeType, artifactSizeLabel } from '../../lib/artifacts';
import { type AsyncError, resolveAsyncError, toAsyncError } from '../../lib/asyncError';
import {
	evidenceDiffChangedFiles,
	findPatchArtifact,
	findSecurityArtifact,
	hasRealPatchChanges,
} from '../../lib/diff';
import { toneForStatus } from '../../lib/format';
import { redactVisibleText } from '../../lib/redaction';

function redactedJson(value: unknown, fallback = '[]') {
	return redactVisibleText(value, fallback);
}

function evidenceLink(href: string, label: string, id: unknown, notLinkedLabel: string) {
	const value = String(id ?? '');
	return value ? (
		<a href={href}>
			{label} {value}
		</a>
	) : (
		<span className="muted">{notLinkedLabel}</span>
	);
}

/**
 * Evidence & QA auditor: lists evidence packages and, on selection, fetches package
 * detail plus its patch and security-findings artifacts through token-protected
 * endpoints. Enforces "proof before approval" — flags packages whose patch artifact
 * proves no real diff — and redacts artifact text before rendering it inline.
 */
export function EvidencePage({ overview, token }: { overview: Overview; token: string }) {
	const { t } = useI18n();
	const [selectedEvidenceId, setSelectedEvidenceId] = useState('');
	const [detail, setDetail] = useState<EvidenceDetailResponse | null>(null);
	const [detailLoading, setDetailLoading] = useState(false);
	const [detailError, setDetailError] = useState<AsyncError | null>(null);
	const [diffPayload, setDiffPayload] = useState<ArtifactPayload | null>(null);
	const [diffLoading, setDiffLoading] = useState(false);
	const [diffError, setDiffError] = useState<AsyncError | null>(null);
	const [securityPayload, setSecurityPayload] = useState<ArtifactPayload | null>(null);
	const [securityLoading, setSecurityLoading] = useState(false);
	const [securityError, setSecurityError] = useState<AsyncError | null>(null);
	const [previewArtifact, setPreviewArtifact] = useState<Artifact | null>(null);
	const [previewPayload, setPreviewPayload] = useState<ArtifactPayload | null>(null);
	const [previewLoadingId, setPreviewLoadingId] = useState('');
	const [downloadLoadingId, setDownloadLoadingId] = useState('');
	const [previewError, setPreviewError] = useState('');
	const [reloadToken, setReloadToken] = useState(0);
	const reload = () => setReloadToken((token) => token + 1);
	const detailErrorText = resolveAsyncError(detailError, t);
	const diffErrorText = resolveAsyncError(diffError, t);
	const securityErrorText = resolveAsyncError(securityError, t);
	useEffect(() => {
		if (!selectedEvidenceId) {
			setDetail(null);
			setDetailError(null);
			return;
		}
		const controller = new AbortController();
		setDetail(null);
		setDetailError(null);
		setDetailLoading(true);
		void getEvidenceDetail(selectedEvidenceId, controller.signal)
			.then((payload) => setDetail(payload))
			.catch((error) => {
				if (!controller.signal.aborted)
					setDetailError(
						toAsyncError(error, 'app.pages.errEvidenceDetail', 'Evidence detail failed.'),
					);
			})
			.finally(() => {
				if (!controller.signal.aborted) setDetailLoading(false);
			});
		return () => controller.abort();
	}, [reloadToken, selectedEvidenceId]);
	const selectedPackage = detail?.evidencePackage ?? null;
	const detailArtifacts = detail?.artifacts ?? [];
	const patchArtifact = useMemo(() => findPatchArtifact(detailArtifacts), [detailArtifacts]);
	const securityArtifact = useMemo(() => findSecurityArtifact(detailArtifacts), [detailArtifacts]);
	useEffect(() => {
		setDiffPayload(null);
		setDiffError(null);
		if (!selectedEvidenceId || !patchArtifact) {
			setDiffLoading(false);
			return;
		}
		const artifactId = String(patchArtifact.id ?? '');
		if (!artifactId) return;
		let active = true;
		setDiffLoading(true);
		void fetchEvidenceArtifact(token, selectedEvidenceId, artifactId)
			.then((payload) => {
				if (active) setDiffPayload(payload);
			})
			.catch((error) => {
				if (active)
					setDiffError(
						toAsyncError(
							error,
							'app.pages.errDiffArtifactPreview',
							'Diff artifact preview failed.',
						),
					);
			})
			.finally(() => {
				if (active) setDiffLoading(false);
			});
		return () => {
			active = false;
		};
	}, [patchArtifact, reloadToken, selectedEvidenceId, token]);
	useEffect(() => {
		setSecurityPayload(null);
		setSecurityError(null);
		if (!selectedEvidenceId || !securityArtifact) {
			setSecurityLoading(false);
			return;
		}
		const artifactId = String(securityArtifact.id ?? '');
		if (!artifactId) return;
		let active = true;
		setSecurityLoading(true);
		void fetchEvidenceArtifact(token, selectedEvidenceId, artifactId)
			.then((payload) => {
				if (active) setSecurityPayload(payload);
			})
			.catch((error) => {
				if (active)
					setSecurityError(
						toAsyncError(
							error,
							'app.pages.errSecurityFindingsPreview',
							'Security findings preview failed.',
						),
					);
			})
			.finally(() => {
				if (active) setSecurityLoading(false);
			});
		return () => {
			active = false;
		};
	}, [reloadToken, securityArtifact, selectedEvidenceId, token]);
	const openPreview = async (artifact: Artifact) => {
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setPreviewError(t('app.workbench.evidence.incomplete', 'Artifact metadata is incomplete.'));
			return;
		}
		setPreviewArtifact(artifact);
		setPreviewPayload(null);
		setPreviewError('');
		setPreviewLoadingId(artifactId);
		try {
			const payload = await fetchEvidenceArtifact(token, evidenceId, artifactId);
			setPreviewPayload(payload);
		} catch (error) {
			setPreviewError(
				error instanceof Error
					? error.message
					: t('app.pages.errArtifactPreview', 'Artifact preview failed.'),
			);
		} finally {
			setPreviewLoadingId('');
		}
	};
	const downloadArtifact = async (artifact: Artifact) => {
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setPreviewError(t('app.workbench.evidence.incomplete', 'Artifact metadata is incomplete.'));
			return;
		}
		setPreviewError('');
		setDownloadLoadingId(artifactId);
		try {
			await downloadEvidenceArtifact(token, evidenceId, artifactId, artifactDisplayName(artifact));
		} catch (error) {
			setPreviewError(
				error instanceof Error
					? error.message
					: t('app.workbench.evidence.downloadError', 'Artifact download failed.'),
			);
		} finally {
			setDownloadLoadingId('');
		}
	};
	const diffText = redactVisibleText(diffPayload?.text ?? '', '');
	const patchHasChanges = hasRealPatchChanges(diffPayload?.text ?? '');
	const changedFiles = evidenceDiffChangedFiles(selectedPackage);
	const modelCalls = Array.isArray(selectedPackage?.modelCalls) ? selectedPackage.modelCalls : [];
	const toolCalls = Array.isArray(selectedPackage?.toolCalls) ? selectedPackage.toolCalls : [];
	const packageArtifactRefs = Array.isArray(selectedPackage?.artifacts)
		? selectedPackage.artifacts
		: [];
	return (
		<>
			<PageHeader
				kicker={t('ui.static.proof.before.approval.410ccd82', 'Proof before approval')}
				title={t('app.nav.evidence', 'Evidence & QA')}
				summary={t(
					'ui.static.qa.passed.requires.real.command.execution.evidence.collected.3b2b198a',
					'QA passed requires real command execution evidence; collected artifacts remain separate from QA verdicts.',
				)}
			/>
			<div className="grid two">
				<Surface title={t('app.workbench.signals.evidence', 'Evidence packages')}>
					<DataTable
						rows={overview.evidencePackages}
						empty={
							<EmptyState
								title={t('ui.static.no.evidence.packages.25648065', 'No evidence packages')}
								body={t(
									'ui.static.workflow.qa.steps.will.produce.evidence.before.review.9887a8c7',
									'Workflow QA steps will produce evidence before review.',
								)}
							/>
						}
						columns={[
							{
								key: 'task',
								label: t('ui.static.task.7bb0ddf9', 'Task'),
								render: (row) => <span className="mono">{String(row.taskId ?? '')}</span>,
							},
							{
								key: 'verdict',
								label: t('ui.static.verdict.7f6e5a6e', 'Verdict'),
								render: (row) => (
									<Badge tone={toneForStatus(String(row.qaVerdict ?? ''))}>
										{String(row.qaVerdict ?? '')}
									</Badge>
								),
							},
							{
								key: 'source',
								label: t('ui.static.source.6da13add', 'Source'),
								render: (row) => (
									<span className="mono">{String(row.evidenceSource ?? 'operator_attested')}</span>
								),
							},
							{
								key: 'agent',
								label: t('ui.static.agent.5ce2e6f4', 'Agent'),
								render: (row) => String(row.agentId ?? ''),
							},
							{
								key: 'diffs',
								label: t('ui.static.diff.refs.cc464235', 'Diff refs'),
								render: (row) => String(Array.isArray(row.diffRefs) ? row.diffRefs.length : 0),
							},
							{
								key: 'detail',
								label: t('ui.static.detail.evidence.viewer.849e56ab', 'Detail'),
								render: (row) => {
									const evidenceId = String(row.id ?? '');
									return (
										<button
											className="button"
											type="button"
											aria-label={`${t('app.pages.viewEvidencePackage', 'View evidence package')} ${evidenceId}`}
											disabled={!evidenceId || detailLoading}
											onClick={() => setSelectedEvidenceId(evidenceId)}
										>
											{selectedEvidenceId === evidenceId && detailLoading
												? t('app.pages.evidenceLoading', 'Loading')
												: t('app.pages.evidenceView', 'View')}
										</button>
									);
								},
							},
						]}
					/>
				</Surface>
				<Surface title={t('ui.static.test.result.records.d5e43bd0', 'Test result records')}>
					<DataTable
						rows={overview.testResultRecords}
						empty={
							<EmptyState
								title={t('ui.static.no.test.results.5503b785', 'No test results')}
								body={t(
									'ui.static.evidence.packages.record.command.level.qa.results.f5a353b5',
									'Evidence packages record command-level QA results.',
								)}
							/>
						}
						columns={[
							{
								key: 'command',
								label: t('app.workbench.evidence.colCommand', 'Command'),
								render: (row) => <span className="mono">{String(row.command ?? '')}</span>,
							},
							{
								key: 'status',
								label: t('ui.static.status.bae7d5be', 'Status'),
								render: (row) => (
									<Badge tone={toneForStatus(String(row.status ?? ''))}>
										{String(row.status ?? '')}
									</Badge>
								),
							},
							{
								key: 'evidence',
								label: t('ui.static.evidence.7ea014de', 'Evidence'),
								render: (row) => (
									<span className="mono">{String(row.evidencePackageId ?? '')}</span>
								),
							},
						]}
					/>
				</Surface>
				<Surface title={t('ui.static.artifacts.a5b79f59', 'Artifacts')}>
					<DataTable
						rows={overview.artifacts}
						empty={
							<EmptyState
								title={t('ui.static.no.artifacts.3e0935de', 'No artifacts')}
								body={t(
									'ui.static.evidence.artifacts.can.be.previewed.only.through.token.prote.45d0a57f',
									'Evidence artifacts can be previewed only through token-protected v1 endpoints.',
								)}
							/>
						}
						columns={[
							{
								key: 'name',
								label: t('ui.static.name.709a2322', 'Name'),
								render: (row) => artifactDisplayName(row),
							},
							{
								key: 'kind',
								label: t('app.workbench.evidence.colKind', 'Kind'),
								render: (row) => <span className="mono">{String(row.kind ?? '')}</span>,
							},
							{
								key: 'size',
								label: t('ui.static.size.b7152342', 'Size'),
								render: (row) => artifactSizeLabel(row),
							},
							{
								key: 'hash',
								label: t('ui.static.hash.873507a0', 'Hash'),
								render: (row) => (
									<span className="mono">{String(row.hash ?? '').slice(0, 12)}</span>
								),
							},
							{
								key: 'action',
								label: t('ui.static.action.97c89a4d', 'Action'),
								render: (row) => {
									const name = artifactDisplayName(row);
									const loading = previewLoadingId === String(row.id ?? '');
									const downloading = downloadLoadingId === String(row.id ?? '');
									return (
										<div className="inline" aria-busy={loading || downloading}>
											<button
												className="button"
												type="button"
												aria-label={`${t('app.review.previewArtifact', 'Preview artifact')} ${name}`}
												disabled={loading}
												onClick={() => void openPreview(row)}
											>
												{loading
													? t('app.review.opening', 'Opening')
													: t('app.workbench.evidence.preview', 'Preview')}
											</button>
											<button
												className="button"
												type="button"
												aria-label={`${t('app.review.downloadArtifact', 'Download artifact')} ${name}`}
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
			</div>
			<div className="stack">
				{detailErrorText && selectedPackage ? (
					<div className="form-error" role="alert">
						{detailErrorText}
					</div>
				) : null}
				{detailErrorText && !selectedPackage ? (
					<Surface title={t('ui.static.evidence.detail.viewer.30e34a50', 'Evidence detail')}>
						<ErrorState
							title={t('app.pages.errEvidenceDetailTitle', 'Could not load evidence detail')}
							body={detailErrorText}
							action={
								<Button variant="secondary" onClick={reload}>
									{t('app.pages.errRetry', 'Retry')}
								</Button>
							}
						/>
					</Surface>
				) : null}
				{!detailErrorText && selectedEvidenceId && !selectedPackage ? (
					<Surface title={t('ui.static.evidence.detail.viewer.30e34a50', 'Evidence detail')}>
						<EmptyState
							title={
								detailLoading
									? t('app.pages.loadingEvidenceDetail', 'Loading evidence detail')
									: t('app.pages.evidenceDetailUnavailable', 'Evidence detail unavailable')
							}
							body={t(
								'ui.static.detail.endpoint.must.return.evidence.1e754443',
								'The detail endpoint must return package metadata, test records and artifacts before evidence can be audited.',
							)}
						/>
					</Surface>
				) : null}
				{selectedPackage ? (
					<>
						<Surface title={t('ui.static.evidence.detail.viewer.30e34a50', 'Evidence detail')}>
							<div className="stack">
								<div className="inline">
									<Badge tone={toneForStatus(String(selectedPackage.qaVerdict ?? ''))}>
										QA {String(selectedPackage.qaVerdict ?? 'not_started')}
									</Badge>
									<Badge>{String(selectedPackage.evidenceSource ?? 'operator_attested')}</Badge>
									<Badge>
										{detailArtifacts.length} {t('app.pages.artifactsCountLabel', 'artifacts')}
									</Badge>
									{changedFiles !== null ? (
										<span className="mono">
											{t('app.pages.changedFilesLabel', 'changed files')} {changedFiles}
										</span>
									) : null}
								</div>
								<DataTable
									rows={[
										{
											label: t('ui.static.evidence.package.viewer.b83f1c3d', 'Evidence package'),
											value: <span className="mono">{String(selectedPackage.id ?? '')}</span>,
										},
										{
											label: t('ui.static.created.at.evidence.viewer.4f61b6bb', 'Created at'),
											value: (
												<span className="mono">{String(selectedPackage.createdAt ?? '')}</span>
											),
										},
										{
											label: t('ui.static.project.f6f4da8d', 'Project'),
											value: (
												<span className="mono">{String(selectedPackage.projectId ?? '')}</span>
											),
										},
										{
											label: t('ui.static.workflow.run.evidence.viewer.1495cb66', 'Workflow run'),
											value: evidenceLink(
												'#workflows',
												t('app.pages.evidenceLinkWorkflow', 'Workflow'),
												selectedPackage.workflowRunId,
												t('app.pages.evidenceNotLinked', 'not linked'),
											),
										},
										{
											label: t('ui.static.job.30c8cb83', 'Job'),
											value: evidenceLink(
												'#workflows',
												t('app.pages.evidenceLinkJob', 'Job'),
												selectedPackage.jobId,
												t('app.pages.evidenceNotLinked', 'not linked'),
											),
										},
										{
											label: t('ui.static.agent.run.b458871f', 'Agent run'),
											value: evidenceLink(
												'#agents',
												t('app.pages.evidenceLinkAgentRun', 'Agent run'),
												selectedPackage.agentRunId ?? selectedPackage.agentId,
												t('app.pages.evidenceNotLinked', 'not linked'),
											),
										},
										{
											label: t('ui.static.workspace.4ca0a75c', 'Workspace'),
											value: (
												<span className="mono">
													{String(
														selectedPackage.workspaceId ??
															t('app.pages.evidenceNotLinked', 'not linked'),
													)}
												</span>
											),
										},
										{
											label: t('ui.static.runtime.c4740e4c', 'Runtime'),
											value: (
												<span className="mono">
													{String(
														selectedPackage.runtimeId ??
															t('app.pages.evidenceNotLinked', 'not linked'),
													)}
												</span>
											),
										},
										{
											label: t('ui.static.test.plan.evidence.viewer.e2d86632', 'Test plan'),
											value: redactVisibleText(selectedPackage.testPlan, ''),
										},
									]}
									empty={
										<EmptyState
											title={t('ui.static.no.metadata.evidence.viewer.39ade8bd', 'No metadata')}
											body={t(
												'ui.static.evidence.detail.metadata.not.returned.a8f37a91',
												'Evidence detail metadata was not returned.',
											)}
										/>
									}
									columns={[
										{
											key: 'label',
											label: t('ui.static.metadata.251edc0e', 'Metadata'),
											render: (row) => row.label,
										},
										{
											key: 'value',
											label: t('ui.static.value.evidence.viewer.f963c4f7', 'Value'),
											render: (row) => row.value,
										},
									]}
								/>
								<div>
									<div className="metric-label">
										{t('ui.static.runtime.health.evidence.viewer.296670dd', 'Runtime health')}
									</div>
									<pre className="artifact-preview">
										{redactedJson(selectedPackage.runtimeHealth, '{}')}
									</pre>
								</div>
								<div>
									<div className="metric-label">
										{t('ui.static.hashes.evidence.viewer.d2f31199', 'Hashes')}
									</div>
									<pre className="artifact-preview">
										{redactedJson(selectedPackage.hashes, '{}')}
									</pre>
								</div>
							</div>
						</Surface>
						<Surface
							title={t('ui.static.evidence.artifacts.viewer.e5f7b7f9', 'Evidence artifacts')}
						>
							<DataTable
								rows={detailArtifacts}
								empty={
									<EmptyState
										title={t('ui.static.no.artifacts.3e0935de', 'No artifacts')}
										body={t(
											'ui.static.no.downloadable.artifacts.evidence.package.e73dbe66',
											'This evidence package has no downloadable artifacts.',
										)}
									/>
								}
								columns={[
									{
										key: 'name',
										label: t('ui.static.name.709a2322', 'Name'),
										render: (row) => artifactDisplayName(row),
									},
									{
										key: 'kind',
										label: t('app.workbench.evidence.colKind', 'Kind'),
										render: (row) => <span className="mono">{String(row.kind ?? '')}</span>,
									},
									{
										key: 'size',
										label: t('ui.static.size.b7152342', 'Size'),
										render: (row) => artifactSizeLabel(row),
									},
									{
										key: 'sha256',
										label: t('ui.static.sha256.evidence.viewer.74b330c2', 'SHA-256'),
										render: (row) => (
											<span className="mono">
												{String(row.hash ?? t('app.workbenchEvidence.notRecorded', 'not recorded'))}
											</span>
										),
									},
									{
										key: 'action',
										label: t('ui.static.action.97c89a4d', 'Action'),
										render: (row) => {
											const name = artifactDisplayName(row);
											const loading = previewLoadingId === String(row.id ?? '');
											const downloading = downloadLoadingId === String(row.id ?? '');
											return (
												<div className="inline" aria-busy={loading || downloading}>
													<button
														className="button"
														type="button"
														aria-label={`${t('app.review.previewArtifact', 'Preview artifact')} ${name}`}
														disabled={loading}
														onClick={() => void openPreview(row)}
													>
														{loading
															? t('app.review.opening', 'Opening')
															: t('app.workbench.evidence.preview', 'Preview')}
													</button>
													<button
														className="button"
														type="button"
														aria-label={`${t('app.review.downloadArtifact', 'Download artifact')} ${name}`}
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
							{packageArtifactRefs.length ? (
								<div className="stack">
									<div className="metric-label">
										{t('ui.static.package.artifact.refs.8476a86c', 'Package artifact refs')}
									</div>
									<pre className="artifact-preview">{redactedJson(packageArtifactRefs)}</pre>
								</div>
							) : null}
						</Surface>
						<Surface title={t('ui.static.diff.viewer.evidence.viewer.80e5e450', 'Diff viewer')}>
							<div className="stack">
								<div className="inline">
									<Badge tone={patchArtifact && patchHasChanges ? 'ok' : 'warn'}>
										{patchArtifact && patchHasChanges
											? t('app.workbench.diff.realChanges', 'real changes')
											: t('app.pages.diffNoRealChanges', 'no real changes')}
									</Badge>
									{changedFiles !== null ? (
										<span className="mono">
											{t('app.pages.changedFilesLabel', 'changed files')} {changedFiles}
										</span>
									) : null}
									{patchArtifact ? (
										<span className="mono">
											sha256{' '}
											{String(
												patchArtifact.hash ??
													t('app.workbenchEvidence.notRecorded', 'not recorded'),
											)}
										</span>
									) : null}
								</div>
								{diffErrorText && diffPayload ? (
									<div className="form-error" role="alert">
										{diffErrorText}
									</div>
								) : null}
								{diffErrorText && !diffPayload ? (
									<ErrorState
										title={t('app.pages.errDiffArtifactTitle', 'Could not load the diff artifact')}
										body={diffErrorText}
										action={
											<Button variant="secondary" onClick={reload}>
												{t('app.pages.errRetry', 'Retry')}
											</Button>
										}
									/>
								) : null}
								{diffLoading ? (
									<EmptyState
										title={t('ui.static.loading.diff.artifact.f72cc0b5', 'Loading diff artifact')}
										body={t(
											'app.workbench.diff.loadingBody',
											'The patch is read through the protected artifact endpoint.',
										)}
									/>
								) : null}
								{!patchArtifact ? (
									<EmptyState
										title={t(
											'ui.static.no.patch.artifact.recorded.a6eb2ba0',
											'No patch artifact recorded',
										)}
										body={t(
											'ui.static.diff.refs.without.downloadable.patch.9e7a1d44',
											'Diff refs without a downloadable patch are not sufficient evidence of code changes.',
										)}
									/>
								) : null}
								{patchArtifact && diffPayload && !patchHasChanges ? (
									<EmptyState
										title={t(
											'ui.static.patch.artifact.empty.no.changes.26b9281a',
											'Patch artifact is empty; no changes are proven.',
										)}
										body={t(
											'ui.static.evidence.package.does.not.prove.real.diff.9b3f3276',
											'The evidence package does not prove a real file diff.',
										)}
									/>
								) : null}
								{patchArtifact && diffPayload?.text ? (
									<pre className="artifact-preview">{diffText}</pre>
								) : null}
							</div>
						</Surface>
						<Surface title={t('ui.static.security.findings.viewer.6957b365', 'Security findings')}>
							<div className="stack">
								{securityErrorText && securityPayload ? (
									<div className="form-error" role="alert">
										{securityErrorText}
									</div>
								) : null}
								{securityErrorText && !securityPayload ? (
									<ErrorState
										title={t(
											'app.pages.errSecurityFindingsTitle',
											'Could not load security findings',
										)}
										body={securityErrorText}
										action={
											<Button variant="secondary" onClick={reload}>
												{t('app.pages.errRetry', 'Retry')}
											</Button>
										}
									/>
								) : null}
								{securityLoading ? (
									<EmptyState
										title={t(
											'ui.static.loading.security.findings.0b677e6f',
											'Loading security findings',
										)}
										body={t(
											'app.workbench.evidence.securityLoadingBody',
											'Findings are read from the linked artifact.',
										)}
									/>
								) : null}
								{!securityArtifact ? (
									<EmptyState
										title={t(
											'ui.static.no.security.findings.artifact.8e9970d1',
											'No security findings artifact',
										)}
										body={t(
											'app.pages.noSecurityFindingsArtifactBody',
											'No security-findings.json artifact is linked to this evidence package.',
										)}
									/>
								) : null}
								{securityArtifact ? (
									<div className="inline">
										<Badge>{artifactDisplayName(securityArtifact)}</Badge>
										<span className="mono">
											sha256{' '}
											{String(
												securityArtifact.hash ??
													t('app.workbenchEvidence.notRecorded', 'not recorded'),
											)}
										</span>
									</div>
								) : null}
								{securityPayload?.text ? (
									<pre className="artifact-preview">
										{redactVisibleText(securityPayload.text, '')}
									</pre>
								) : null}
							</div>
						</Surface>
						<Surface title={t('ui.static.model.and.tool.calls.4ee53a3a', 'Model and tool calls')}>
							<div className="stack">
								<div className="inline">
									<Badge>
										{modelCalls.length} {t('app.pages.modelCallsCountLabel', 'model calls')}
									</Badge>
									<Badge>
										{toolCalls.length} {t('app.pages.toolCallsCountLabel', 'tool calls')}
									</Badge>
								</div>
								<div>
									<div className="metric-label">
										{t('ui.static.model.calls.88e40906', 'Model calls')}
									</div>
									<pre className="artifact-preview">{redactedJson(modelCalls)}</pre>
								</div>
								<div>
									<div className="metric-label">
										{t('ui.static.tool.calls.evidence.viewer.0585df8a', 'Tool calls')}
									</div>
									<pre className="artifact-preview">{redactedJson(toolCalls)}</pre>
								</div>
								<div>
									<div className="metric-label">
										{t(
											'ui.static.policy.decisions.and.approvals.c5964304',
											'Permission checks and approvals',
										)}
									</div>
									<pre className="artifact-preview">
										{redactedJson(
											{
												policyDecisions: selectedPackage.policyDecisions,
												approvals: selectedPackage.approvals,
											},
											'{}',
										)}
									</pre>
								</div>
							</div>
						</Surface>
					</>
				) : null}
			</div>
			<Drawer
				label={t('ui.static.artifact.preview.acbf276e', 'Artifact preview')}
				open={Boolean(previewArtifact)}
				onClose={() => {
					setPreviewArtifact(null);
					setPreviewPayload(null);
					setPreviewError('');
				}}
			>
				<div className="drawer-body">
					{previewArtifact ? (
						<>
							<div className="stack">
								<div className="inline">
									<Badge>
										{String(previewArtifact.kind ?? t('app.review.artifact', 'artifact'))}
									</Badge>
									<Badge>{artifactMimeType(previewArtifact, previewPayload)}</Badge>
									<Badge>{artifactSizeLabel(previewArtifact)}</Badge>
								</div>
								<h3 className="artifact-title">{artifactDisplayName(previewArtifact)}</h3>
								<div className="mono">
									sha256{' '}
									{String(
										previewPayload?.hash ||
											previewArtifact.hash ||
											t('app.workbenchEvidence.notRecorded', 'not recorded'),
									)}
								</div>
							</div>
							{previewError ? (
								<div className="form-error" role="alert">
									{previewError}
								</div>
							) : null}
							{downloadLoadingId ? (
								<div className="sr-only" role="status">
									{t('ui.static.downloading.artifact.b640e8fe', 'Downloading artifact')}
								</div>
							) : null}
							{previewPayload?.text ? (
								<pre className="artifact-preview">{redactVisibleText(previewPayload.text, '')}</pre>
							) : (
								<EmptyState
									title={
										previewLoadingId
											? t('app.workbench.evidence.loading', 'Loading artifact')
											: t('app.review.binaryOrEmptyArtifact', 'Binary or empty artifact')
									}
									body={t(
										'ui.static.non.text.artifacts.remain.downloadable.but.are.not.rendered.42481492',
										'Non-text artifacts remain downloadable, but are not rendered inline.',
									)}
								/>
							)}
							<button
								className="button primary"
								type="button"
								disabled={downloadLoadingId === String(previewArtifact.id ?? '')}
								aria-label={`${t('app.pages.downloadPreviewArtifact', 'Download preview artifact')} ${artifactDisplayName(previewArtifact)}`}
								onClick={() => void downloadArtifact(previewArtifact)}
							>
								{downloadLoadingId === String(previewArtifact.id ?? '')
									? t('ui.static.downloading.artifact.b640e8fe', 'Downloading artifact')
									: t('app.review.downloadArtifact', 'Download artifact')}
							</button>
						</>
					) : null}
				</div>
			</Drawer>
		</>
	);
}
