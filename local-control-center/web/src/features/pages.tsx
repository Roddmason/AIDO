/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useEffect, useMemo, useState } from 'react';

import {
	createArchitectureDecision,
	createModelGatewayRolePolicy,
	createNextStep,
	createRisk,
	downloadEvidenceArtifact,
	fetchEvidenceArtifact,
	getEvidenceDetail,
	registerMcpServer,
	updateRisk,
	updateSandboxProfile,
} from '../api/client';
import type { ArtifactPayload, EvidenceDetailResponse } from '../api/client';
import type {
	ArchitectureDecisionStatus,
	Artifact,
	Dictionary,
	McpTransport,
	NextStepPriority,
	Overview,
	PolicyRevision,
	Project,
	RetrievalStatus,
	RiskSeverity,
	RiskStatus,
	RuntimeProviders,
} from '../api/types';
import { Badge, DataTable, Drawer, EmptyState, PageHeader, Surface } from '../components/primitives';
import { artifactDisplayName, artifactMimeType, artifactSizeLabel } from '../lib/artifacts';
import { evidenceDiffChangedFiles, findPatchArtifact, findSecurityArtifact, hasRealPatchChanges } from '../lib/diff';
import { toneForStatus } from '../lib/format';
import { redactVisibleText } from '../lib/redaction';

type Mutate = <T>(operation: (token: string) => Promise<T>, options?: { awaitRefresh?: boolean }) => Promise<T>;

function money(value: unknown) {
	if (value === null || value === undefined || value === '') return 'unknown';
	const amount = Number(value);
	return Number.isFinite(amount) ? `$${amount.toFixed(4)}` : 'unknown';
}

function sumRecordedCost(rows: Overview['costUsage']) {
	const amounts = rows.map((row) => Number(row.amountUsd)).filter((amount) => Number.isFinite(amount));
	return amounts.length ? amounts.reduce((sum, amount) => sum + amount, 0) : null;
}

function recordTimestamp(record: { updatedAt?: string; createdAt?: string }) {
	const parsed = Date.parse(String(record.updatedAt ?? record.createdAt ?? ''));
	return Number.isNaN(parsed) ? 0 : parsed;
}

function upsertNewestById<T extends { id: string; updatedAt?: string; createdAt?: string }>(records: T[], incoming: T) {
	const existing = records.find((record) => record.id === incoming.id);
	if (existing && recordTimestamp(existing) > recordTimestamp(incoming)) return records;
	return existing ? records.map((record) => (record.id === incoming.id ? incoming : record)) : [incoming, ...records];
}

function mergeNewestById<T extends { id: string; updatedAt?: string; createdAt?: string }>(current: T[], incoming: T[]) {
	return incoming.reduce((merged, record) => upsertNewestById(merged, record), current);
}

export function WorkspacesPage({ overview }: { overview: Overview }) {
	return (
		<>
			<PageHeader kicker="Isolation" title="Workspaces" summary="Task-owned workspace allocations prevent agents from sharing one mutable working tree." />
			<Surface title="Allocated workspaces">
				<DataTable rows={overview.runtimeWorkspaces} empty={<EmptyState title="No isolated workspaces" body="Workflow implementation steps will allocate workspaces." />} columns={[
					{ key: 'task', label: 'Task', render: (row) => <span className="mono">{String(row.taskId ?? '')}</span> },
					{ key: 'project', label: 'Project', render: (row) => <span className="mono">{String(row.projectId ?? '')}</span> },
					{ key: 'owner', label: 'Owner', render: (row) => String(row.ownerAgentId ?? '') },
					{ key: 'isolation', label: 'Isolation', render: (row) => <span className="mono">{String(row.isolationType ?? '')}</span> },
					{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
				]} />
			</Surface>
		</>
	);
}

export function PolicySecurityPage({ overview, mutate }: { overview: Overview; mutate: Mutate }) {
	const [sandboxProfiles, setSandboxProfiles] = useState(overview.sandboxProfiles);
	const [policyRevisions, setPolicyRevisions] = useState(overview.policyRevisions);
	const defaultProfile = String(sandboxProfiles[0]?.id ?? 'default_docker');
	const [profileId, setProfileId] = useState(defaultProfile);
	const [selectedRevision, setSelectedRevision] = useState<PolicyRevision | null>(null);
	const [sandboxReason, setSandboxReason] = useState('');
	const [sandboxImage, setSandboxImage] = useState('python:3.12-slim');
	const [sandboxMemory, setSandboxMemory] = useState('512m');
	const [sandboxCpus, setSandboxCpus] = useState('1');
	const [sandboxTimeout, setSandboxTimeout] = useState('120');
	const [error, setError] = useState('');
	const [busy, setBusy] = useState(false);
	useEffect(() => {
		setSandboxProfiles((current) => {
			const merged = new Map(current.map((profile) => [String(profile.id), profile]));
			for (const profile of overview.sandboxProfiles) {
				const existing = merged.get(String(profile.id));
				const incomingTime = Date.parse(String(profile.updatedAt ?? ''));
				const existingTime = Date.parse(String(existing?.updatedAt ?? ''));
				if (!existing || Number.isNaN(existingTime) || (!Number.isNaN(incomingTime) && incomingTime >= existingTime)) {
					merged.set(String(profile.id), profile);
				}
			}
			return Array.from(merged.values());
		});
		setPolicyRevisions((current) => {
			const merged = new Map(current.map((revision) => [String(revision.id), revision]));
			for (const revision of overview.policyRevisions) {
				merged.set(String(revision.id), revision);
			}
			return Array.from(merged.values());
		});
	}, [overview.policyRevisions, overview.sandboxProfiles]);
	useEffect(() => {
		if (!sandboxProfiles.some((profile) => String(profile.id) === profileId)) {
			setProfileId(String(sandboxProfiles[0]?.id ?? 'default_docker'));
		}
	}, [profileId, sandboxProfiles]);
	const saveSandboxProfile = async () => {
		if (!sandboxReason.trim()) {
			setError('Sandbox update reason is required.');
			return;
		}
		if (!sandboxImage.trim() || /\s/.test(sandboxImage)) {
			setError('Sandbox allowed image must be a catalog image without spaces.');
			return;
		}
		const timeoutSeconds = Number(sandboxTimeout);
		if (!Number.isInteger(timeoutSeconds) || timeoutSeconds < 1 || timeoutSeconds > 900) {
			setError('Sandbox timeout seconds must be between 1 and 900.');
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate((token) =>
				updateSandboxProfile(token, profileId, {
					reason: sandboxReason.trim(),
					allowedImages: [sandboxImage.trim()],
					allowedNetworks: ['none'],
					defaultNetwork: 'none',
					memory: sandboxMemory.trim(),
					cpus: sandboxCpus.trim(),
					timeoutSeconds,
					status: 'active',
				}),
				{ awaitRefresh: false },
			);
			setSandboxProfiles((current) => {
				const updated = result.sandboxProfile;
				return current.some((profile) => profile.id === updated.id)
					? current.map((profile) => (profile.id === updated.id ? updated : profile))
					: [updated, ...current];
			});
			const revision = result.policyRevision;
			if (revision) {
				setPolicyRevisions((current) => [revision, ...current.filter((item) => item.id !== revision.id)]);
			}
		} catch (saveError) {
			setError(saveError instanceof Error ? saveError.message : 'Sandbox profile update failed.');
		} finally {
			setBusy(false);
		}
	};
	return (
		<>
			<PageHeader kicker="Permission engine" title="Policy & Security" summary="Command classification, path boundaries, human gates and sandbox settings for every sensitive action." />
			<div className="grid two">
				<Surface title="Strict sandbox profile form">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="sandbox-profile">Sandbox profile</label>
							<select id="sandbox-profile" className="select" value={profileId} onChange={(event) => setProfileId(event.target.value)}>
								{sandboxProfiles.map((profile) => (
									<option key={String(profile.id)} value={String(profile.id)}>{String(profile.id)}</option>
								))}
							</select>
						</div>
						<div className="field">
							<label htmlFor="sandbox-reason">Sandbox update reason</label>
							<input id="sandbox-reason" className="input" value={sandboxReason} onChange={(event) => setSandboxReason(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="sandbox-image">Sandbox allowed image</label>
							<input id="sandbox-image" className="input" value={sandboxImage} onChange={(event) => setSandboxImage(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="sandbox-memory">Sandbox memory limit</label>
							<input id="sandbox-memory" className="input" value={sandboxMemory} onChange={(event) => setSandboxMemory(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="sandbox-cpus">Sandbox CPU limit</label>
							<input id="sandbox-cpus" className="input" value={sandboxCpus} onChange={(event) => setSandboxCpus(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="sandbox-timeout">Sandbox timeout seconds</label>
							<input id="sandbox-timeout" className="input" type="number" min="1" max="900" value={sandboxTimeout} onChange={(event) => setSandboxTimeout(event.target.value)} />
						</div>
						{error ? <div className="form-error" role="alert">{error}</div> : null}
						<button className="button primary" type="button" disabled={busy} onClick={() => { void saveSandboxProfile(); }}>Save sandbox profile</button>
					</div>
				</Surface>
				<Surface title="Permission checks">
					<DataTable rows={overview.permissionDecisions} empty={<EmptyState title="No permission checks" body="Tool calls and command evaluations are recorded here." />} columns={[
						{ key: 'decision', label: 'Decision', render: (row) => <Badge tone={toneForStatus(String(row.decision ?? ''))}>{String(row.decision ?? '')}</Badge> },
						{ key: 'risk', label: 'Risk', render: (row) => String(row.riskLevel ?? '') },
						{ key: 'command', label: 'Command', render: (row) => <span className="mono">{String(row.command ?? '')}</span> },
					]} />
				</Surface>
				<Surface title="Policy revisions">
					<DataTable rows={policyRevisions} empty={<EmptyState title="No revisions" body="Policy and sandbox changes will create explicit revision records." />} columns={[
						{ key: 'subject', label: 'Subject', render: (row) => <span className="mono">{String(row.subjectId ?? '')}</span> },
						{ key: 'version', label: 'Version', render: (row) => <Badge>v{String(row.version ?? '')}</Badge> },
						{ key: 'fields', label: 'Changed', render: (row) => Array.isArray(row.changedFields) ? row.changedFields.join(', ') : '' },
						{
							key: 'diff',
							label: 'Diff',
							render: (row) => (
								<button className="button" type="button" aria-label={`View policy revision diff for ${String(row.subjectId ?? '')}`} onClick={() => setSelectedRevision(row)}>
									View diff
								</button>
							),
						},
					]} />
				</Surface>
				<Surface title="Permission grants">
					<DataTable rows={overview.permissionGrants} empty={<EmptyState title="No grants" body="Approved sensitive actions create one-use execution grants." />} columns={[
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
						{ key: 'request', label: 'Request', render: (row) => <span className="mono">{String(row.actionRequestId ?? '')}</span> },
						{ key: 'scope', label: 'Scope', render: (row) => <span className="mono">{String(row.projectId ?? '')} / {String(row.jobId ?? '')} / {String(row.agentId ?? '')}</span> },
						{ key: 'tool', label: 'Tool', render: (row) => <span className="mono">{String(row.tool ?? '')} {String(row.runtimeId ?? '')}</span> },
						{ key: 'command', label: 'Command', render: (row) => <span className="mono">{String(row.command ?? '')} {(row.commandArgv ?? []).join(' ')}</span> },
						{ key: 'path', label: 'Path', render: (row) => <span className="mono">{String(row.workspaceId ?? '')} {String(row.path ?? '')}</span> },
						{ key: 'lifecycle', label: 'Lifecycle', render: (row) => <span>{String(row.grantedBy ?? '')} {String(row.grantedAt ?? '')} / expires {String(row.expiresAt ?? '')} / consumed {String(row.consumedAt ?? 'not consumed')}</span> },
						{ key: 'reason', label: 'Reason', render: (row) => String(row.revokeReason ?? row.reason ?? '') },
					]} />
				</Surface>
				<Surface title="Sandbox profiles">
					<DataTable rows={sandboxProfiles} empty={<EmptyState title="No profiles" body="Docker sandbox catalog and resource limits appear here." />} columns={[
						{ key: 'id', label: 'Profile', render: (row) => <span className="mono">{String(row.id ?? '')}</span> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
						{ key: 'images', label: 'Images', render: (row) => Array.isArray(row.allowedImages) ? row.allowedImages.length : 0 },
						{ key: 'limits', label: 'Limits', render: (row) => <span className="mono">{String(row.memory ?? '')} / {String(row.cpus ?? '')} cpu</span> },
					]} />
				</Surface>
				<Surface title="Tool-call execution">
					<DataTable rows={overview.agentToolCalls} empty={<EmptyState title="No tool calls" body="Agent runtime tool calls appear after policy evaluation." />} columns={[
						{ key: 'tool', label: 'Tool', render: (row) => <span className="mono">{String(row.toolName ?? '')}</span> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
						{ key: 'execution', label: 'Execution', render: (row) => {
							const payload = row.payload as Record<string, unknown> | undefined;
							return <span className="mono">{String(payload?.execution ?? 'not_recorded')}</span>;
						} },
						{ key: 'command', label: 'Command', render: (row) => {
							const payload = row.payload as Record<string, unknown> | undefined;
							return <span className="mono">{String(payload?.command ?? '')}</span>;
						} },
					]} />
				</Surface>
			</div>
			<Drawer label="Policy revision diff" open={Boolean(selectedRevision)} onClose={() => setSelectedRevision(null)}>
				<div className="drawer-body">
					{selectedRevision ? (
						<>
							<div className="stack">
								<div className="inline">
									<Badge>{String(selectedRevision.subjectType ?? '')}</Badge>
									<Badge>v{String(selectedRevision.version ?? '')}</Badge>
								</div>
								<h3 className="artifact-title">{String(selectedRevision.subjectId ?? '')}</h3>
								<div className="muted">{String(selectedRevision.reason ?? '')}</div>
							</div>
							<div className="diff-grid" role="table" aria-label="Policy revision changed fields">
								<div className="diff-row diff-head" role="row">
									<div role="columnheader">Field</div>
									<div role="columnheader">Before</div>
									<div role="columnheader">After</div>
								</div>
								{(Array.isArray(selectedRevision.changedFields) ? selectedRevision.changedFields : []).map((field) => {
									const previous = selectedRevision.previous as Record<string, unknown> | undefined;
									const updated = selectedRevision.updated as Record<string, unknown> | undefined;
									return (
										<div className="diff-row" role="row" key={String(field)}>
											<div role="cell" className="mono">{String(field)}</div>
											<div role="cell" className="diff-before">{JSON.stringify(previous?.[String(field)] ?? null)}</div>
											<div role="cell" className="diff-after">{JSON.stringify(updated?.[String(field)] ?? null)}</div>
										</div>
									);
								})}
							</div>
						</>
					) : null}
				</div>
			</Drawer>
		</>
	);
}

export function MemoryPage({ overview, retrievalStatus }: { overview: Overview; retrievalStatus: RetrievalStatus | null }) {
	const retrievalPosture = retrievalStatus
		? retrievalStatus.available
			? retrievalStatus.degraded
				? 'index degraded'
				: 'available'
			: retrievalStatus.status
		: 'unknown';

	return (
		<>
			<PageHeader kicker="Semantic context" title="Memory & Retrieval" summary="SQLite is canonical. FAISS, NumPy and future vector stores are rebuildable indexes, not source of truth." />
			<div className="grid two">
				<Surface title="Retrieval backend">
					<div className="metric-value">{String(retrievalStatus?.backend ?? 'unknown')}</div>
					<div className="metric-label">{retrievalPosture}</div>
				</Surface>
				<Surface title="Memory items">
					<div className="metric-value">{overview.memoryItems.length}</div>
					<div className="metric-label">records in SQLite</div>
				</Surface>
			</div>
		</>
	);
}

function redactedJson(value: unknown, fallback = '[]') {
	return redactVisibleText(value, fallback);
}

function evidenceLink(href: string, label: string, id: unknown) {
	const value = String(id ?? '');
	return value ? <a href={href}>{label} {value}</a> : <span className="muted">not linked</span>;
}

export function EvidencePage({ overview, token }: { overview: Overview; token: string }) {
	const [selectedEvidenceId, setSelectedEvidenceId] = useState('');
	const [detail, setDetail] = useState<EvidenceDetailResponse | null>(null);
	const [detailLoading, setDetailLoading] = useState(false);
	const [detailError, setDetailError] = useState('');
	const [diffPayload, setDiffPayload] = useState<ArtifactPayload | null>(null);
	const [diffLoading, setDiffLoading] = useState(false);
	const [diffError, setDiffError] = useState('');
	const [securityPayload, setSecurityPayload] = useState<ArtifactPayload | null>(null);
	const [securityLoading, setSecurityLoading] = useState(false);
	const [securityError, setSecurityError] = useState('');
	const [previewArtifact, setPreviewArtifact] = useState<Artifact | null>(null);
	const [previewPayload, setPreviewPayload] = useState<ArtifactPayload | null>(null);
	const [previewLoadingId, setPreviewLoadingId] = useState('');
	const [downloadLoadingId, setDownloadLoadingId] = useState('');
	const [previewError, setPreviewError] = useState('');
	useEffect(() => {
		if (!selectedEvidenceId) {
			setDetail(null);
			setDetailError('');
			return;
		}
		const controller = new AbortController();
		setDetail(null);
		setDetailError('');
		setDetailLoading(true);
		void getEvidenceDetail(selectedEvidenceId, controller.signal)
			.then((payload) => setDetail(payload))
			.catch((error) => {
				if (!controller.signal.aborted) setDetailError(error instanceof Error ? error.message : 'Evidence detail failed.');
			})
			.finally(() => {
				if (!controller.signal.aborted) setDetailLoading(false);
			});
		return () => controller.abort();
	}, [selectedEvidenceId]);
	const selectedPackage = detail?.evidencePackage ?? null;
	const detailArtifacts = detail?.artifacts ?? [];
	const patchArtifact = useMemo(() => findPatchArtifact(detailArtifacts), [detailArtifacts]);
	const securityArtifact = useMemo(() => findSecurityArtifact(detailArtifacts), [detailArtifacts]);
	useEffect(() => {
		setDiffPayload(null);
		setDiffError('');
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
				if (active) setDiffError(error instanceof Error ? error.message : 'Diff artifact preview failed.');
			})
			.finally(() => {
				if (active) setDiffLoading(false);
			});
		return () => { active = false; };
	}, [patchArtifact, selectedEvidenceId, token]);
	useEffect(() => {
		setSecurityPayload(null);
		setSecurityError('');
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
				if (active) setSecurityError(error instanceof Error ? error.message : 'Security findings preview failed.');
			})
			.finally(() => {
				if (active) setSecurityLoading(false);
			});
		return () => { active = false; };
	}, [securityArtifact, selectedEvidenceId, token]);
	const openPreview = async (artifact: Artifact) => {
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setPreviewError('Artifact metadata is incomplete.');
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
			setPreviewError(error instanceof Error ? error.message : 'Artifact preview failed.');
		} finally {
			setPreviewLoadingId('');
		}
	};
	const downloadArtifact = async (artifact: Artifact) => {
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setPreviewError('Artifact metadata is incomplete.');
			return;
		}
		setPreviewError('');
		setDownloadLoadingId(artifactId);
		try {
			await downloadEvidenceArtifact(token, evidenceId, artifactId, artifactDisplayName(artifact));
		} catch (error) {
			setPreviewError(error instanceof Error ? error.message : 'Artifact download failed.');
		} finally {
			setDownloadLoadingId('');
		}
	};
	const diffText = redactVisibleText(diffPayload?.text ?? '', '');
	const patchHasChanges = hasRealPatchChanges(diffPayload?.text ?? '');
	const changedFiles = evidenceDiffChangedFiles(selectedPackage);
	const modelCalls = Array.isArray(selectedPackage?.modelCalls) ? selectedPackage.modelCalls : [];
	const toolCalls = Array.isArray(selectedPackage?.toolCalls) ? selectedPackage.toolCalls : [];
	const packageArtifactRefs = Array.isArray(selectedPackage?.artifacts) ? selectedPackage.artifacts : [];
	return (
		<>
			<PageHeader kicker="Proof before approval" title="Evidence & QA" summary="QA passed requires real command execution evidence; collected artifacts remain separate from QA verdicts." />
			<div className="grid two">
				<Surface title="Evidence packages">
					<DataTable rows={overview.evidencePackages} empty={<EmptyState title="No evidence packages" body="Workflow QA steps will produce evidence before review." />} columns={[
						{ key: 'task', label: 'Task', render: (row) => <span className="mono">{String(row.taskId ?? '')}</span> },
						{ key: 'verdict', label: 'Verdict', render: (row) => <Badge tone={toneForStatus(String(row.qaVerdict ?? ''))}>{String(row.qaVerdict ?? '')}</Badge> },
						{ key: 'source', label: 'Source', render: (row) => <span className="mono">{String(row.evidenceSource ?? 'operator_attested')}</span> },
						{ key: 'agent', label: 'Agent', render: (row) => String(row.agentId ?? '') },
						{ key: 'diffs', label: 'Diff refs', render: (row) => String(Array.isArray(row.diffRefs) ? row.diffRefs.length : 0) },
						{
							key: 'detail',
							label: 'Detail',
							render: (row) => {
								const evidenceId = String(row.id ?? '');
								return (
									<button className="button" type="button" aria-label={`View evidence package ${evidenceId}`} disabled={!evidenceId || detailLoading} onClick={() => setSelectedEvidenceId(evidenceId)}>
										{selectedEvidenceId === evidenceId && detailLoading ? 'Loading' : 'View'}
									</button>
								);
							},
						},
					]} />
				</Surface>
				<Surface title="Test result records">
					<DataTable rows={overview.testResultRecords} empty={<EmptyState title="No test results" body="Evidence packages record command-level QA results." />} columns={[
						{ key: 'command', label: 'Command', render: (row) => <span className="mono">{String(row.command ?? '')}</span> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
						{ key: 'evidence', label: 'Evidence', render: (row) => <span className="mono">{String(row.evidencePackageId ?? '')}</span> },
					]} />
				</Surface>
				<Surface title="Artifacts">
					<DataTable rows={overview.artifacts} empty={<EmptyState title="No artifacts" body="Evidence artifacts can be previewed only through token-protected v1 endpoints." />} columns={[
						{ key: 'name', label: 'Name', render: (row) => artifactDisplayName(row) },
						{ key: 'kind', label: 'Kind', render: (row) => <span className="mono">{String(row.kind ?? '')}</span> },
						{ key: 'size', label: 'Size', render: (row) => artifactSizeLabel(row) },
						{ key: 'hash', label: 'Hash', render: (row) => <span className="mono">{String(row.hash ?? '').slice(0, 12)}</span> },
						{
							key: 'action',
							label: 'Action',
							render: (row) => {
								const name = artifactDisplayName(row);
								const loading = previewLoadingId === String(row.id ?? '');
								const downloading = downloadLoadingId === String(row.id ?? '');
								return (
									<div className="inline" aria-busy={loading || downloading}>
										<button className="button" type="button" aria-label={`Preview artifact ${name}`} disabled={loading} onClick={() => void openPreview(row)}>
											{loading ? 'Opening' : 'Preview'}
										</button>
										<button className="button" type="button" aria-label={`Download artifact ${name}`} disabled={downloading} onClick={() => void downloadArtifact(row)}>
											{downloading ? 'Downloading' : 'Download'}
										</button>
									</div>
								);
							},
						},
					]} />
				</Surface>
			</div>
			<div className="stack">
				{detailError ? <div className="form-error" role="alert">{detailError}</div> : null}
				{selectedEvidenceId && !selectedPackage ? (
					<Surface title="Evidence detail">
						<EmptyState title={detailLoading ? 'Loading evidence detail' : 'Evidence detail unavailable'} body="The detail endpoint must return package metadata, test records and artifacts before evidence can be audited." />
					</Surface>
				) : null}
				{selectedPackage ? (
					<>
						<Surface title="Evidence detail">
							<div className="stack">
								<div className="inline">
									<Badge tone={toneForStatus(String(selectedPackage.qaVerdict ?? ''))}>QA {String(selectedPackage.qaVerdict ?? 'not_started')}</Badge>
									<Badge>{String(selectedPackage.evidenceSource ?? 'operator_attested')}</Badge>
									<Badge>{detailArtifacts.length} artifacts</Badge>
									{changedFiles !== null ? <span className="mono">changed files {changedFiles}</span> : null}
								</div>
								<DataTable rows={[
									{ label: 'Evidence package', value: <span className="mono">{String(selectedPackage.id ?? '')}</span> },
									{ label: 'Created at', value: <span className="mono">{String(selectedPackage.createdAt ?? '')}</span> },
									{ label: 'Project', value: <span className="mono">{String(selectedPackage.projectId ?? '')}</span> },
									{ label: 'Workflow run', value: evidenceLink('#workflows', 'Workflow', selectedPackage.workflowRunId) },
									{ label: 'Job', value: evidenceLink('#jobs', 'Job', selectedPackage.jobId) },
									{ label: 'Agent run', value: evidenceLink('#agents', 'Agent run', selectedPackage.agentRunId ?? selectedPackage.agentId) },
									{ label: 'Workspace', value: <span className="mono">{String(selectedPackage.workspaceId ?? 'not linked')}</span> },
									{ label: 'Runtime', value: <span className="mono">{String(selectedPackage.runtimeId ?? 'not linked')}</span> },
									{ label: 'Test plan', value: redactVisibleText(selectedPackage.testPlan, '') },
								]} empty={<EmptyState title="No metadata" body="Evidence detail metadata was not returned." />} columns={[
									{ key: 'label', label: 'Metadata', render: (row) => row.label },
									{ key: 'value', label: 'Value', render: (row) => row.value },
								]} />
								<div>
									<div className="metric-label">Runtime health</div>
									<pre className="artifact-preview">{redactedJson(selectedPackage.runtimeHealth, '{}')}</pre>
								</div>
								<div>
									<div className="metric-label">Hashes</div>
									<pre className="artifact-preview">{redactedJson(selectedPackage.hashes, '{}')}</pre>
								</div>
							</div>
						</Surface>
						<Surface title="Evidence artifacts">
							<DataTable rows={detailArtifacts} empty={<EmptyState title="No artifacts" body="This evidence package has no downloadable artifacts." />} columns={[
								{ key: 'name', label: 'Name', render: (row) => artifactDisplayName(row) },
								{ key: 'kind', label: 'Kind', render: (row) => <span className="mono">{String(row.kind ?? '')}</span> },
								{ key: 'size', label: 'Size', render: (row) => artifactSizeLabel(row) },
								{ key: 'sha256', label: 'SHA-256', render: (row) => <span className="mono">{String(row.hash ?? 'not recorded')}</span> },
								{
									key: 'action',
									label: 'Action',
									render: (row) => {
										const name = artifactDisplayName(row);
										const loading = previewLoadingId === String(row.id ?? '');
										const downloading = downloadLoadingId === String(row.id ?? '');
										return (
											<div className="inline" aria-busy={loading || downloading}>
												<button className="button" type="button" aria-label={`Preview artifact ${name}`} disabled={loading} onClick={() => void openPreview(row)}>
													{loading ? 'Opening' : 'Preview'}
												</button>
												<button className="button" type="button" aria-label={`Download artifact ${name}`} disabled={downloading} onClick={() => void downloadArtifact(row)}>
													{downloading ? 'Downloading' : 'Download'}
												</button>
											</div>
										);
									},
								},
							]} />
							{packageArtifactRefs.length ? (
								<div className="stack">
									<div className="metric-label">Package artifact refs</div>
									<pre className="artifact-preview">{redactedJson(packageArtifactRefs)}</pre>
								</div>
							) : null}
						</Surface>
						<Surface title="Diff viewer">
							<div className="stack">
								<div className="inline">
									<Badge tone={patchArtifact && patchHasChanges ? 'ok' : 'warn'}>{patchArtifact && patchHasChanges ? 'real changes' : 'no real changes'}</Badge>
									{changedFiles !== null ? <span className="mono">changed files {changedFiles}</span> : null}
									{patchArtifact ? <span className="mono">sha256 {String(patchArtifact.hash ?? 'not recorded')}</span> : null}
								</div>
								{diffError ? <div className="form-error" role="alert">{diffError}</div> : null}
								{diffLoading ? <EmptyState title="Loading diff artifact" body="The patch is read through the protected artifact endpoint." /> : null}
								{!patchArtifact ? <EmptyState title="No patch artifact recorded" body="Diff refs without a downloadable patch are not sufficient evidence of code changes." /> : null}
								{patchArtifact && diffPayload && !patchHasChanges ? <EmptyState title="Patch artifact is empty; no changes are proven." body="The evidence package does not prove a real file diff." /> : null}
								{patchArtifact && diffPayload?.text ? <pre className="artifact-preview">{diffText}</pre> : null}
							</div>
						</Surface>
						<Surface title="Security findings">
							<div className="stack">
								{securityError ? <div className="form-error" role="alert">{securityError}</div> : null}
								{securityLoading ? <EmptyState title="Loading security findings" body="Findings are read from the linked artifact." /> : null}
								{!securityArtifact ? <EmptyState title="No security findings artifact" body="No security-findings.json artifact is linked to this evidence package." /> : null}
								{securityArtifact ? (
									<div className="inline">
										<Badge>{artifactDisplayName(securityArtifact)}</Badge>
										<span className="mono">sha256 {String(securityArtifact.hash ?? 'not recorded')}</span>
									</div>
								) : null}
								{securityPayload?.text ? <pre className="artifact-preview">{redactVisibleText(securityPayload.text, '')}</pre> : null}
							</div>
						</Surface>
						<Surface title="Model and tool calls">
							<div className="stack">
								<div className="inline">
									<Badge>{modelCalls.length} model calls</Badge>
									<Badge>{toolCalls.length} tool calls</Badge>
								</div>
								<div>
									<div className="metric-label">Model calls</div>
									<pre className="artifact-preview">{redactedJson(modelCalls)}</pre>
								</div>
								<div>
									<div className="metric-label">Tool calls</div>
									<pre className="artifact-preview">{redactedJson(toolCalls)}</pre>
								</div>
								<div>
									<div className="metric-label">Permission checks and approvals</div>
									<pre className="artifact-preview">{redactedJson({ policyDecisions: selectedPackage.policyDecisions, approvals: selectedPackage.approvals }, '{}')}</pre>
								</div>
							</div>
						</Surface>
					</>
				) : null}
			</div>
			<Drawer label="Artifact preview" open={Boolean(previewArtifact)} onClose={() => {
				setPreviewArtifact(null);
				setPreviewPayload(null);
				setPreviewError('');
			}}>
				<div className="drawer-body">
					{previewArtifact ? (
						<>
							<div className="stack">
								<div className="inline">
									<Badge>{String(previewArtifact.kind ?? 'artifact')}</Badge>
									<Badge>{artifactMimeType(previewArtifact, previewPayload)}</Badge>
									<Badge>{artifactSizeLabel(previewArtifact)}</Badge>
								</div>
								<h3 className="artifact-title">{artifactDisplayName(previewArtifact)}</h3>
								<div className="mono">sha256 {String(previewPayload?.hash || previewArtifact.hash || 'not recorded')}</div>
							</div>
							{previewError ? <div className="form-error" role="alert">{previewError}</div> : null}
							{downloadLoadingId ? <div className="sr-only" role="status">Downloading artifact</div> : null}
							{previewPayload?.text ? (
								<pre className="artifact-preview">{redactVisibleText(previewPayload.text, '')}</pre>
							) : (
								<EmptyState title={previewLoadingId ? 'Loading artifact' : 'Binary or empty artifact'} body="Non-text artifacts remain downloadable, but are not rendered inline." />
							)}
							<button className="button primary" type="button" disabled={downloadLoadingId === String(previewArtifact.id ?? '')} aria-label={`Download preview artifact ${artifactDisplayName(previewArtifact)}`} onClick={() => void downloadArtifact(previewArtifact)}>
								{downloadLoadingId === String(previewArtifact.id ?? '') ? 'Downloading artifact' : 'Download artifact'}
							</button>
						</>
					) : null}
				</div>
			</Drawer>
		</>
	);
}

export function GovernancePage({ overview, selectedProject, mutate }: { overview: Overview; selectedProject: Project | null; mutate: Mutate }) {
	const project = selectedProject;
	const [riskRows, setRiskRows] = useState(overview.riskRegister);
	const [decisionRows, setDecisionRows] = useState(overview.architectureDecisions);
	const [nextStepRows, setNextStepRows] = useState(overview.nextSteps);
	const [riskTitle, setRiskTitle] = useState('');
	const [riskSeverity, setRiskSeverity] = useState<RiskSeverity>('medium');
	const [riskMitigation, setRiskMitigation] = useState('');
	const [decisionTitle, setDecisionTitle] = useState('');
	const [decisionStatus, setDecisionStatus] = useState<ArchitectureDecisionStatus>('proposed');
	const [decisionContext, setDecisionContext] = useState('');
	const [decisionText, setDecisionText] = useState('');
	const [nextStepTitle, setNextStepTitle] = useState('');
	const [nextStepPriority, setNextStepPriority] = useState<NextStepPriority>('medium');
	const [governanceFilter, setGovernanceFilter] = useState('');
	const [riskStatusFilter, setRiskStatusFilter] = useState<'all' | RiskStatus>('all');
	const [riskUpdateId, setRiskUpdateId] = useState('');
	const [riskUpdateStatus, setRiskUpdateStatus] = useState<RiskStatus>('monitoring');
	const [error, setError] = useState('');
	const [busy, setBusy] = useState(false);
	const query = governanceFilter.trim().toLowerCase();
	const matchesQuery = (...values: Array<string | null | undefined>) =>
		!query || values.some((value) => String(value ?? '').toLowerCase().includes(query));
	const filteredRisks = riskRows.filter(
		(risk) =>
			(riskStatusFilter === 'all' || risk.status === riskStatusFilter) &&
			matchesQuery(risk.title, risk.severity, risk.status, risk.owner, risk.mitigation),
	);
	const filteredDecisions = decisionRows.filter((decision) =>
		matchesQuery(decision.title, decision.status, decision.context, decision.decision),
	);
	const filteredNextSteps = nextStepRows.filter((step) =>
		matchesQuery(step.title, step.priority, step.status, step.owner),
	);
	const selectedRiskId = riskUpdateId || riskRows[0]?.id || '';
	useEffect(() => {
		setRiskRows((current) => mergeNewestById(current, overview.riskRegister));
		setDecisionRows((current) => mergeNewestById(current, overview.architectureDecisions));
		setNextStepRows((current) => mergeNewestById(current, overview.nextSteps));
	}, [overview.architectureDecisions, overview.nextSteps, overview.riskRegister]);
	const saveRisk = async () => {
		if (!riskTitle.trim()) {
			setError('Risk title is required.');
			return;
		}
		if (['high', 'critical'].includes(riskSeverity) && !riskMitigation.trim()) {
			setError('High and critical risks require mitigation.');
			return;
		}
		if (!project) {
			setError('A project is required before creating governance records.');
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate((token) =>
				createRisk(token, {
					projectId: project.id,
					title: riskTitle.trim(),
					severity: riskSeverity,
					status: 'open',
					mitigation: riskMitigation.trim(),
					owner: 'technical_lead',
				}),
				{ awaitRefresh: false },
			);
			setRiskRows((current) => upsertNewestById(current, result.risk));
		} catch (saveError) {
			setError(saveError instanceof Error ? saveError.message : 'Risk creation failed.');
		} finally {
			setBusy(false);
		}
	};
	const saveRiskUpdate = async () => {
		if (!selectedRiskId) {
			setError('A risk is required before updating status.');
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate((token) => updateRisk(token, selectedRiskId, { status: riskUpdateStatus }), { awaitRefresh: false });
			setRiskRows((current) => upsertNewestById(current, result.risk));
		} catch (saveError) {
			setError(saveError instanceof Error ? saveError.message : 'Risk update failed.');
		} finally {
			setBusy(false);
		}
	};
	const saveDecision = async () => {
		if (!decisionTitle.trim()) {
			setError('Decision title is required.');
			return;
		}
		if (decisionStatus === 'accepted' && (!decisionContext.trim() || !decisionText.trim())) {
			setError('Accepted decisions require context and decision text.');
			return;
		}
		if (!project) {
			setError('A project is required before creating governance records.');
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate((token) =>
				createArchitectureDecision(token, {
					projectId: project.id,
					title: decisionTitle.trim(),
					status: decisionStatus,
					context: decisionContext.trim(),
					decision: decisionText.trim(),
					consequences: [],
				}),
				{ awaitRefresh: false },
			);
			setDecisionRows((current) => upsertNewestById(current, result.architectureDecision));
		} catch (saveError) {
			setError(saveError instanceof Error ? saveError.message : 'Decision creation failed.');
		} finally {
			setBusy(false);
		}
	};
	const saveNextStep = async () => {
		if (!nextStepTitle.trim()) {
			setError('Next step title is required.');
			return;
		}
		if (!project) {
			setError('A project is required before creating governance records.');
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate((token) =>
				createNextStep(token, {
					projectId: project.id,
					title: nextStepTitle.trim(),
					priority: nextStepPriority,
					status: 'planned',
					owner: 'technical_lead',
				}),
				{ awaitRefresh: false },
			);
			setNextStepRows((current) => upsertNewestById(current, result.nextStep));
		} catch (saveError) {
			setError(saveError instanceof Error ? saveError.message : 'Next step creation failed.');
		} finally {
			setBusy(false);
		}
	};
	return (
		<>
			<PageHeader kicker="Engineering judgement" title="Governance" summary="Risks, decisions and next steps are operational records, not comments buried in chat." />
			<div className="grid three">
				<Surface title="Risks"><div className="metric-value">{riskRows.length}</div></Surface>
				<Surface title="Decisions"><div className="metric-value">{decisionRows.length}</div></Surface>
				<Surface title="Next steps"><div className="metric-value">{nextStepRows.length}</div></Surface>
			</div>
			<Surface title="Strict record forms">
				<div className="inline">
					<Badge tone={project ? 'ok' : 'warn'}>{project ? project.name : 'no operational project'}</Badge>
					{project ? null : <span className="field-help">Select an active operational project in Settings before creating governance records.</span>}
				</div>
				<div className="grid three">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="risk-title">Risk title</label>
							<input id="risk-title" className="input" value={riskTitle} onChange={(event) => setRiskTitle(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="risk-severity">Risk severity</label>
							<select id="risk-severity" className="select" value={riskSeverity} onChange={(event) => setRiskSeverity(event.target.value as RiskSeverity)}>
								<option value="low">low</option>
								<option value="medium">medium</option>
								<option value="high">high</option>
								<option value="critical">critical</option>
							</select>
						</div>
						<div className="field">
							<label htmlFor="risk-mitigation">Risk mitigation</label>
							<input id="risk-mitigation" className="input" value={riskMitigation} onChange={(event) => setRiskMitigation(event.target.value)} />
						</div>
						<button className="button primary" type="button" disabled={busy} onClick={() => { void saveRisk(); }}>Save risk</button>
					</div>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="decision-title">Decision title</label>
							<input id="decision-title" className="input" value={decisionTitle} onChange={(event) => setDecisionTitle(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="decision-status">Decision status</label>
							<select id="decision-status" className="select" value={decisionStatus} onChange={(event) => setDecisionStatus(event.target.value as ArchitectureDecisionStatus)}>
								<option value="proposed">proposed</option>
								<option value="accepted">accepted</option>
								<option value="rejected">rejected</option>
								<option value="superseded">superseded</option>
								<option value="deprecated">deprecated</option>
							</select>
						</div>
						<div className="field">
							<label htmlFor="decision-context">Decision context</label>
							<input id="decision-context" className="input" value={decisionContext} onChange={(event) => setDecisionContext(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="decision-text">Decision text</label>
							<input id="decision-text" className="input" value={decisionText} onChange={(event) => setDecisionText(event.target.value)} />
						</div>
						<button className="button primary" type="button" disabled={busy} onClick={() => { void saveDecision(); }}>Save decision</button>
					</div>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="next-step-title">Next step title</label>
							<input id="next-step-title" className="input" value={nextStepTitle} onChange={(event) => setNextStepTitle(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="next-step-priority">Next step priority</label>
							<select id="next-step-priority" className="select" value={nextStepPriority} onChange={(event) => setNextStepPriority(event.target.value as NextStepPriority)}>
								<option value="low">low</option>
								<option value="medium">medium</option>
								<option value="high">high</option>
								<option value="urgent">urgent</option>
							</select>
						</div>
						<button className="button primary" type="button" disabled={busy} onClick={() => { void saveNextStep(); }}>Save next step</button>
					</div>
				</div>
				{error ? <div className="form-error" role="alert">{error}</div> : null}
			</Surface>
			<div className="grid two">
				<Surface title="Governance filters">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="governance-filter">Governance filter</label>
							<input
								id="governance-filter"
								className="input"
								value={governanceFilter}
								onChange={(event) => setGovernanceFilter(event.target.value)}
								placeholder="Filter risks, decisions, and next steps"
							/>
						</div>
						<div className="field">
							<label htmlFor="risk-status-filter">Risk status filter</label>
							<select
								id="risk-status-filter"
								className="select"
								value={riskStatusFilter}
								onChange={(event) => setRiskStatusFilter(event.target.value as 'all' | RiskStatus)}
							>
								<option value="all">all</option>
								<option value="open">open</option>
								<option value="monitoring">monitoring</option>
								<option value="mitigating">mitigating</option>
								<option value="mitigated">mitigated</option>
								<option value="accepted">accepted</option>
								<option value="closed">closed</option>
							</select>
						</div>
					</div>
				</Surface>
				<Surface title="Risk update form">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="risk-update-id">Risk to update</label>
							<select
								id="risk-update-id"
								className="select"
								value={selectedRiskId}
								disabled={!riskRows.length}
								onChange={(event) => setRiskUpdateId(event.target.value)}
							>
								{riskRows.map((risk) => (
									<option key={risk.id} value={risk.id}>{risk.title}</option>
								))}
							</select>
						</div>
						<div className="field">
							<label htmlFor="risk-update-status">Risk update status</label>
							<select
								id="risk-update-status"
								className="select"
								value={riskUpdateStatus}
								onChange={(event) => setRiskUpdateStatus(event.target.value as RiskStatus)}
							>
								<option value="open">open</option>
								<option value="monitoring">monitoring</option>
								<option value="mitigating">mitigating</option>
								<option value="mitigated">mitigated</option>
								<option value="accepted">accepted</option>
								<option value="closed">closed</option>
							</select>
						</div>
						<button className="button primary" type="button" disabled={!selectedRiskId || busy} onClick={() => { void saveRiskUpdate(); }}>Update risk status</button>
					</div>
				</Surface>
			</div>
			<div className="grid three">
				<Surface title="Risk register">
					<DataTable rows={filteredRisks} empty={<EmptyState title="No risks" body="Open technical and product risks appear here." />} columns={[
						{ key: 'title', label: 'Risk', render: (row) => String(row.title ?? '') },
						{ key: 'severity', label: 'Severity', render: (row) => <Badge tone={toneForStatus(String(row.severity ?? ''))}>{String(row.severity ?? '')}</Badge> },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					]} />
				</Surface>
				<Surface title="Decision records">
					<DataTable rows={filteredDecisions} empty={<EmptyState title="No decisions" body="Architecture decisions should be explicit and linked to risks." />} columns={[
						{ key: 'title', label: 'Decision', render: (row) => String(row.title ?? '') },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					]} />
				</Surface>
				<Surface title="Next steps">
					<DataTable rows={filteredNextSteps} empty={<EmptyState title="No next steps" body="Mitigations and follow-up work appear here." />} columns={[
						{ key: 'title', label: 'Step', render: (row) => String(row.title ?? '') },
						{ key: 'priority', label: 'Priority', render: (row) => <Badge tone={toneForStatus(String(row.priority ?? ''))}>{String(row.priority ?? '')}</Badge> },
					]} />
				</Surface>
			</div>
		</>
	);
}

export function IntegrationsPage({ overview, mutate }: { overview: Overview; mutate: Mutate }) {
	const [mcpServers, setMcpServers] = useState(overview.mcpServers);
	const [serverId, setServerId] = useState('mcp_local');
	const [command, setCommand] = useState('');
	const [transport, setTransport] = useState<McpTransport>('stdio');
	const [error, setError] = useState('');
	const [busy, setBusy] = useState(false);
	useEffect(() => {
		setMcpServers((current) => mergeNewestById(current, overview.mcpServers));
	}, [overview.mcpServers]);
	const registerServer = async () => {
		if (!/^[a-z0-9][a-z0-9_-]{2,63}$/.test(serverId)) {
			setError('MCP server id must use lowercase letters, numbers, dashes or underscores.');
			return;
		}
		if (!command.trim()) {
			setError('MCP command is required.');
			return;
		}
		if (/[;&|<>`\r\n]/.test(command)) {
			setError('MCP command must be a single argv-style command without shell operators.');
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate(
				(token) => registerMcpServer(token, { id: serverId, command: command.trim(), transport, metadata: { source: 'integrations_form' } }),
				{ awaitRefresh: false },
			);
			setMcpServers((current) => upsertNewestById(current, result.mcpServer));
		} catch (registerError) {
			setError(registerError instanceof Error ? registerError.message : 'MCP registration failed.');
		} finally {
			setBusy(false);
		}
	};
	return (
		<>
			<PageHeader kicker="External tools" title="Integrations" summary="MCP, IDE, Git and automation integrations are optional adapters, never hidden core dependencies." />
			<div className="grid two">
				<Surface title="Strict MCP registration form">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="mcp-server-id">MCP server id</label>
							<input id="mcp-server-id" className="input" value={serverId} pattern="[a-z0-9][a-z0-9_-]{2,63}" onChange={(event) => setServerId(event.target.value)} />
						</div>
						<div className="field">
							<label htmlFor="mcp-command">MCP command</label>
							<input id="mcp-command" className="input" value={command} placeholder="Installed MCP server command" onChange={(event) => setCommand(event.target.value)} />
							<div className="field-help">Stored as argv-style config; execution still goes through broker, policy and sandbox.</div>
						</div>
						<div className="field">
							<label htmlFor="mcp-transport">MCP transport</label>
							<select id="mcp-transport" className="select" value={transport} onChange={(event) => setTransport(event.target.value as McpTransport)}>
								<option value="stdio">stdio</option>
							</select>
						</div>
						{error ? <div className="form-error" role="alert">{error}</div> : null}
						<button className="button primary" type="button" onClick={() => { void registerServer(); }} disabled={busy}>{busy ? 'Registering MCP server' : 'Register MCP server'}</button>
					</div>
				</Surface>
				<Surface title="Registered MCP servers">
					<DataTable rows={mcpServers} empty={<EmptyState title="No MCP servers" body="Register local stdio MCP servers before runtime adapters can call them." />} columns={[
						{ key: 'id', label: 'Server', render: (row) => <span className="mono">{String(row.id ?? '')}</span> },
						{ key: 'transport', label: 'Transport', render: (row) => String(row.transport ?? '') },
						{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					]} />
				</Surface>
				<Surface title="IDE connections">
					<DataTable rows={overview.auditEvents.filter((row) => String(row.action ?? '').includes('ide'))} empty={<EmptyState title="No integration events" body="Integration activity appears in audit records." />} columns={[
						{ key: 'action', label: 'Action', render: (row) => <span className="mono">{String(row.action ?? '')}</span> },
						{ key: 'target', label: 'Target', render: (row) => String(row.target ?? '') },
					]} />
				</Surface>
			</div>
		</>
	);
}

export function AuditPage({ overview }: { overview: Overview }) {
	return (
		<>
			<PageHeader kicker="Traceability" title="Audit Log" summary="Every change records who made it, what it targeted, the data sent and a linked event." />
			<Surface title="Audit records">
				<DataTable rows={overview.auditEvents} empty={<EmptyState title="No audit records" body="Every change to the system is recorded here." />} columns={[
					{ key: 'action', label: 'Action', render: (row) => <span className="mono">{String(row.action ?? '')}</span> },
					{ key: 'actor', label: 'Actor', render: (row) => String(row.actor ?? '') },
					{ key: 'target', label: 'Target', render: (row) => String(row.target ?? '') },
				]} />
			</Surface>
		</>
	);
}
