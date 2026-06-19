/**
 * Console pages for the operational surfaces that don't warrant their own folder yet.
 * Each export is a full route page (Workspaces, Policy & Security, Memory, Evidence & QA,
 * Governance, Integrations, Audit) reading from the shared Overview snapshot and writing
 * through the gated `mutate` handshake; the local helpers merge optimistic write results
 * back into the snapshot so the UI stays consistent before the next refresh.
 */
import { useEffect, useMemo, useState } from 'react';
import type { ArtifactPayload, EvidenceDetailResponse } from '../api/client';
import {
	createArchitectureDecision,
	createNextStep,
	createRisk,
	downloadEvidenceArtifact,
	fetchEvidenceArtifact,
	getEvidenceDetail,
	registerMcpServer,
	updateRisk,
	updateSandboxProfile,
} from '../api/client';
import type {
	ArchitectureDecisionStatus,
	Artifact,
	McpTransport,
	NextStepPriority,
	Overview,
	PolicyRevision,
	Project,
	RetrievalStatus,
	RiskSeverity,
	RiskStatus,
} from '../api/types';
import {
	Badge,
	DataTable,
	Drawer,
	EmptyState,
	PageHeader,
	Surface,
} from '../components/primitives';
import { useI18n } from '../i18n/I18nProvider';
import { artifactDisplayName, artifactMimeType, artifactSizeLabel } from '../lib/artifacts';
import { type AsyncError, resolveAsyncError, toAsyncError } from '../lib/asyncError';
import {
	evidenceDiffChangedFiles,
	findPatchArtifact,
	findSecurityArtifact,
	hasRealPatchChanges,
} from '../lib/diff';
import { toneForStatus } from '../lib/format';
import { redactVisibleText } from '../lib/redaction';

type Mutate = <T>(
	operation: (token: string) => Promise<T>,
	options?: { awaitRefresh?: boolean },
) => Promise<T>;

function money(value: unknown) {
	if (value === null || value === undefined || value === '') return 'unknown';
	const amount = Number(value);
	return Number.isFinite(amount) ? `$${amount.toFixed(4)}` : 'unknown';
}

function sumRecordedCost(rows: Overview['costUsage']) {
	const amounts = rows
		.map((row) => Number(row.amountUsd))
		.filter((amount) => Number.isFinite(amount));
	return amounts.length ? amounts.reduce((sum, amount) => sum + amount, 0) : null;
}

function recordTimestamp(record: { updatedAt?: string; createdAt?: string }) {
	const parsed = Date.parse(String(record.updatedAt ?? record.createdAt ?? ''));
	return Number.isNaN(parsed) ? 0 : parsed;
}

function upsertNewestById<T extends { id: string; updatedAt?: string; createdAt?: string }>(
	records: T[],
	incoming: T,
) {
	const existing = records.find((record) => record.id === incoming.id);
	if (existing && recordTimestamp(existing) > recordTimestamp(incoming)) return records;
	return existing
		? records.map((record) => (record.id === incoming.id ? incoming : record))
		: [incoming, ...records];
}

function mergeNewestById<T extends { id: string; updatedAt?: string; createdAt?: string }>(
	current: T[],
	incoming: T[],
) {
	return incoming.reduce((merged, record) => upsertNewestById(merged, record), current);
}

/**
 * Read-only inventory of task-owned runtime workspaces (the isolated working trees
 * agents run in). Surfaces ownership and isolation type so two agents are never seen
 * sharing one mutable tree.
 */
export function WorkspacesPage({ overview }: { overview: Overview }) {
	const { t } = useI18n();
	return (
		<>
			<PageHeader
				kicker={t('ui.static.isolation.616318d9', 'Isolation')}
				title={t('app.nav.workspaces', 'Workspaces')}
				summary={t(
					'ui.static.task.owned.workspace.allocations.prevent.agents.from.sharing.42c454d7',
					'Task-owned workspace allocations prevent agents from sharing one mutable working tree.',
				)}
			/>
			<Surface title={t('ui.static.allocated.workspaces.b052cf67', 'Allocated workspaces')}>
				<DataTable
					rows={overview.runtimeWorkspaces}
					empty={
						<EmptyState
							title={t('ui.static.no.isolated.workspaces.1d0dc404', 'No isolated workspaces')}
							body={t(
								'ui.static.workflow.implementation.steps.will.allocate.workspaces.f402df35',
								'Workflow implementation steps will allocate workspaces.',
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
							key: 'project',
							label: t('ui.static.project.f6f4da8d', 'Project'),
							render: (row) => <span className="mono">{String(row.projectId ?? '')}</span>,
						},
						{
							key: 'owner',
							label: t('ui.static.owner.89ff3122', 'Owner'),
							render: (row) => String(row.ownerAgentId ?? ''),
						},
						{
							key: 'isolation',
							label: t('ui.static.isolation.616318d9', 'Isolation'),
							render: (row) => <span className="mono">{String(row.isolationType ?? '')}</span>,
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
					]}
				/>
			</Surface>
		</>
	);
}

/**
 * Permission-engine console: edits the strict sandbox profile and audits the
 * permission decisions, grants, revisions and tool-call executions that gate every
 * sensitive action. Sandbox edits are validated client-side (image, timeout bounds)
 * before the gated write and create an explicit policy revision.
 */
export function PolicySecurityPage({ overview, mutate }: { overview: Overview; mutate: Mutate }) {
	const { t } = useI18n();
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
				if (
					!existing ||
					Number.isNaN(existingTime) ||
					(!Number.isNaN(incomingTime) && incomingTime >= existingTime)
				) {
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
			setError(
				t(
					'ui.static.sandbox.update.reason.is.required.f6d8e3c7',
					'Sandbox update reason is required.',
				),
			);
			return;
		}
		if (!sandboxImage.trim() || /\s/.test(sandboxImage)) {
			setError(
				t(
					'ui.static.sandbox.allowed.image.must.be.a.catalog.image.without.spaces.30b08cad',
					'Sandbox allowed image must be a catalog image without spaces.',
				),
			);
			return;
		}
		const timeoutSeconds = Number(sandboxTimeout);
		if (!Number.isInteger(timeoutSeconds) || timeoutSeconds < 1 || timeoutSeconds > 900) {
			setError(
				t(
					'ui.static.sandbox.timeout.seconds.must.be.between.1.and.900.dabbd1b1',
					'Sandbox timeout seconds must be between 1 and 900.',
				),
			);
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate(
				(token) =>
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
				setPolicyRevisions((current) => [
					revision,
					...current.filter((item) => item.id !== revision.id),
				]);
			}
		} catch (saveError) {
			setError(
				saveError instanceof Error
					? saveError.message
					: t('app.pages.errSandboxProfileUpdate', 'Sandbox profile update failed.'),
			);
		} finally {
			setBusy(false);
		}
	};
	return (
		<>
			<PageHeader
				kicker={t('ui.static.permission.engine.7d290d1a', 'Permission engine')}
				title={t('app.nav.policy', 'Policy & Security')}
				summary={t(
					'app.copy.standalone.6',
					'Command classification, path boundaries, human gates and sandbox settings for every sensitive action.',
				)}
			/>
			<div className="grid two">
				<Surface
					title={t('ui.static.strict.sandbox.profile.form.0d2d3c79', 'Strict sandbox profile form')}
				>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="sandbox-profile">
								{t('ui.static.sandbox.profile.9a2f87ba', 'Sandbox profile')}
							</label>
							<select
								id="sandbox-profile"
								className="select"
								value={profileId}
								onChange={(event) => setProfileId(event.target.value)}
							>
								{sandboxProfiles.map((profile) => (
									<option key={String(profile.id)} value={String(profile.id)}>
										{String(profile.id)}
									</option>
								))}
							</select>
						</div>
						<div className="field">
							<label htmlFor="sandbox-reason">
								{t('ui.static.sandbox.update.reason.ddf64780', 'Sandbox update reason')}
							</label>
							<input
								id="sandbox-reason"
								className="input"
								value={sandboxReason}
								onChange={(event) => setSandboxReason(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="sandbox-image">
								{t('ui.static.sandbox.allowed.image.d77f37e2', 'Sandbox allowed image')}
							</label>
							<input
								id="sandbox-image"
								className="input"
								value={sandboxImage}
								onChange={(event) => setSandboxImage(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="sandbox-memory">
								{t('ui.static.sandbox.memory.limit.a8b6c3b6', 'Sandbox memory limit')}
							</label>
							<input
								id="sandbox-memory"
								className="input"
								value={sandboxMemory}
								onChange={(event) => setSandboxMemory(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="sandbox-cpus">
								{t('ui.static.sandbox.cpu.limit.b909ae47', 'Sandbox CPU limit')}
							</label>
							<input
								id="sandbox-cpus"
								className="input"
								value={sandboxCpus}
								onChange={(event) => setSandboxCpus(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="sandbox-timeout">
								{t('ui.static.sandbox.timeout.seconds.6e201a33', 'Sandbox timeout seconds')}
							</label>
							<input
								id="sandbox-timeout"
								className="input"
								type="number"
								min="1"
								max="900"
								value={sandboxTimeout}
								onChange={(event) => setSandboxTimeout(event.target.value)}
							/>
						</div>
						{error ? (
							<div className="form-error" role="alert">
								{error}
							</div>
						) : null}
						<button
							className="button primary"
							type="button"
							disabled={busy}
							onClick={() => {
								void saveSandboxProfile();
							}}
						>
							{t('ui.static.save.sandbox.profile.8f586d15', 'Save sandbox profile')}
						</button>
					</div>
				</Surface>
				<Surface title={t('ui.static.policy.decisions.f9eecf70', 'Permission checks')}>
					<DataTable
						rows={overview.permissionDecisions}
						empty={
							<EmptyState
								title={t('ui.static.no.policy.decisions.6c679309', 'No permission checks')}
								body={t(
									'ui.static.tool.calls.and.command.evaluations.are.recorded.here.8806375f',
									'Tool calls and command evaluations are recorded here.',
								)}
							/>
						}
						columns={[
							{
								key: 'decision',
								label: t('ui.static.decision.7f59a1f1', 'Decision'),
								render: (row) => (
									<Badge tone={toneForStatus(String(row.decision ?? ''))}>
										{String(row.decision ?? '')}
									</Badge>
								),
							},
							{
								key: 'risk',
								label: t('ui.static.risk.5a8f23f5', 'Risk'),
								render: (row) => String(row.riskLevel ?? ''),
							},
							{
								key: 'command',
								label: t('app.workbench.evidence.colCommand', 'Command'),
								render: (row) => <span className="mono">{String(row.command ?? '')}</span>,
							},
						]}
					/>
				</Surface>
				<Surface title={t('ui.static.policy.revisions.88c9d177', 'Policy revisions')}>
					<DataTable
						rows={policyRevisions}
						empty={
							<EmptyState
								title={t('ui.static.no.revisions.93220a0a', 'No revisions')}
								body={t(
									'ui.static.policy.and.sandbox.changes.will.create.explicit.revision.rec.041be812',
									'Policy and sandbox changes will create explicit revision records.',
								)}
							/>
						}
						columns={[
							{
								key: 'subject',
								label: t('ui.static.subject.8d183dbd', 'Subject'),
								render: (row) => <span className="mono">{String(row.subjectId ?? '')}</span>,
							},
							{
								key: 'version',
								label: t('ui.static.version.2da600bf', 'Version'),
								render: (row) => <Badge>v{String(row.version ?? '')}</Badge>,
							},
							{
								key: 'fields',
								label: t('ui.static.changed.cb5424f6', 'Changed'),
								render: (row) =>
									Array.isArray(row.changedFields) ? row.changedFields.join(', ') : '',
							},
							{
								key: 'diff',
								label: t('app.workbench.tab.diff', 'Diff'),
								render: (row) => (
									<button
										className="button"
										type="button"
										aria-label={`${t('app.pages.viewPolicyRevisionDiff', 'View policy revision diff for')} ${String(row.subjectId ?? '')}`}
										onClick={() => setSelectedRevision(row)}
									>
										{t('app.pages.viewDiff', 'View diff')}
									</button>
								),
							},
						]}
					/>
				</Surface>
				<Surface title={t('ui.static.permission.grants.aee6b0a5', 'Permission grants')}>
					<DataTable
						rows={overview.permissionGrants}
						empty={
							<EmptyState
								title={t('ui.static.no.grants.34485dd1', 'No grants')}
								body={t(
									'ui.static.approved.sensitive.actions.create.one.use.execution.grants.4162db27',
									'Approved sensitive actions create one-use execution grants.',
								)}
							/>
						}
						columns={[
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
								key: 'request',
								label: t('ui.static.request.15c2d85f', 'Request'),
								render: (row) => <span className="mono">{String(row.actionRequestId ?? '')}</span>,
							},
							{
								key: 'scope',
								label: t('ui.static.scope.4651a34e', 'Scope'),
								render: (row) => (
									<span className="mono">
										{String(row.projectId ?? '')} / {String(row.jobId ?? '')} /{' '}
										{String(row.agentId ?? '')}
									</span>
								),
							},
							{
								key: 'tool',
								label: t('ui.static.tool.9a830c71', 'Tool'),
								render: (row) => (
									<span className="mono">
										{String(row.tool ?? '')} {String(row.runtimeId ?? '')}
									</span>
								),
							},
							{
								key: 'command',
								label: t('app.workbench.evidence.colCommand', 'Command'),
								render: (row) => (
									<span className="mono">
										{String(row.command ?? '')} {(row.commandArgv ?? []).join(' ')}
									</span>
								),
							},
							{
								key: 'path',
								label: t('app.workspace.summary.path', 'Path'),
								render: (row) => (
									<span className="mono">
										{String(row.workspaceId ?? '')} {String(row.path ?? '')}
									</span>
								),
							},
							{
								key: 'lifecycle',
								label: t('ui.static.lifecycle.e4db4b56', 'Lifecycle'),
								render: (row) => (
									<span>
										{String(row.grantedBy ?? '')} {String(row.grantedAt ?? '')} / expires{' '}
										{String(row.expiresAt ?? '')} / consumed{' '}
										{String(row.consumedAt ?? t('app.pages.grantNotConsumed', 'not consumed'))}
									</span>
								),
							},
							{
								key: 'reason',
								label: t('ui.static.reason.f219cc06', 'Reason'),
								render: (row) => String(row.revokeReason ?? row.reason ?? ''),
							},
						]}
					/>
				</Surface>
				<Surface title={t('ui.static.sandbox.profiles.36dcd5c1', 'Sandbox profiles')}>
					<DataTable
						rows={sandboxProfiles}
						empty={
							<EmptyState
								title={t('ui.static.no.profiles.a21ca86c', 'No profiles')}
								body={t(
									'ui.static.docker.sandbox.catalog.and.resource.limits.appear.here.32a3464c',
									'Docker sandbox catalog and resource limits appear here.',
								)}
							/>
						}
						columns={[
							{
								key: 'id',
								label: t('ui.static.profile.ff4fc027', 'Profile'),
								render: (row) => <span className="mono">{String(row.id ?? '')}</span>,
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
								key: 'images',
								label: t('ui.static.images.09e871c9', 'Images'),
								render: (row) => (Array.isArray(row.allowedImages) ? row.allowedImages.length : 0),
							},
							{
								key: 'limits',
								label: t('ui.static.limits.61a0ae3b', 'Limits'),
								render: (row) => (
									<span className="mono">
										{String(row.memory ?? '')} / {String(row.cpus ?? '')} cpu
									</span>
								),
							},
						]}
					/>
				</Surface>
				<Surface title={t('ui.static.tool.call.execution.c408e307', 'Tool-call execution')}>
					<DataTable
						rows={overview.agentToolCalls}
						empty={
							<EmptyState
								title={t('ui.static.no.tool.calls.7ab6a86e', 'No tool calls')}
								body={t(
									'ui.static.agent.runtime.tool.calls.appear.after.policy.evaluation.27727399',
									'Agent runtime tool calls appear after policy evaluation.',
								)}
							/>
						}
						columns={[
							{
								key: 'tool',
								label: t('ui.static.tool.9a830c71', 'Tool'),
								render: (row) => <span className="mono">{String(row.toolName ?? '')}</span>,
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
								key: 'execution',
								label: t('ui.static.execution.6d525b71', 'Execution'),
								render: (row) => {
									const payload = row.payload as Record<string, unknown> | undefined;
									return (
										<span className="mono">{String(payload?.execution ?? 'not_recorded')}</span>
									);
								},
							},
							{
								key: 'command',
								label: t('app.workbench.evidence.colCommand', 'Command'),
								render: (row) => {
									const payload = row.payload as Record<string, unknown> | undefined;
									return <span className="mono">{String(payload?.command ?? '')}</span>;
								},
							},
						]}
					/>
				</Surface>
			</div>
			<Drawer
				label={t('ui.static.policy.revision.diff.3d1f2ed2', 'Policy revision diff')}
				open={Boolean(selectedRevision)}
				onClose={() => setSelectedRevision(null)}
			>
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
							<div
								className="diff-grid"
								role="table"
								aria-label={t(
									'ui.static.policy.revision.changed.fields.1d6ec331',
									'Policy revision changed fields',
								)}
							>
								<div className="diff-row diff-head" role="row">
									<div role="columnheader">{t('ui.static.field.c326a466', 'Field')}</div>
									<div role="columnheader">{t('ui.static.before.74f39697', 'Before')}</div>
									<div role="columnheader">{t('ui.static.after.79ba5e1b', 'After')}</div>
								</div>
								{(Array.isArray(selectedRevision.changedFields)
									? selectedRevision.changedFields
									: []
								).map((field) => {
									const previous = selectedRevision.previous as Record<string, unknown> | undefined;
									const updated = selectedRevision.updated as Record<string, unknown> | undefined;
									return (
										<div className="diff-row" role="row" key={String(field)}>
											<div role="cell" className="mono">
												{String(field)}
											</div>
											<div role="cell" className="diff-before">
												{JSON.stringify(previous?.[String(field)] ?? null)}
											</div>
											<div role="cell" className="diff-after">
												{JSON.stringify(updated?.[String(field)] ?? null)}
											</div>
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

/**
 * Memory & Retrieval readout: shows the retrieval backend posture and stored item
 * count. Reinforces the invariant that SQLite is canonical while vector indexes
 * (FAISS/NumPy) are rebuildable, not source of truth.
 */
export function MemoryPage({
	overview,
	retrievalStatus,
}: {
	overview: Overview;
	retrievalStatus: RetrievalStatus | null;
}) {
	const { t } = useI18n();
	const retrievalPosture = retrievalStatus
		? retrievalStatus.available
			? retrievalStatus.degraded
				? t('app.pages.memoryIndexDegraded', 'index degraded')
				: t('app.modelGateway.runtime.available', 'available')
			: retrievalStatus.status
		: t('app.runtime.card.unknown', 'unknown');

	return (
		<>
			<PageHeader
				kicker={t('ui.static.semantic.context.8744e1fb', 'Semantic context')}
				title={t('app.nav.memory', 'Memory & Retrieval')}
				summary={t(
					'ui.static.sqlite.is.canonical.faiss.numpy.and.future.vector.stores.are.f2fb19e8',
					'SQLite is canonical. FAISS, NumPy and future vector stores are rebuildable indexes, not source of truth.',
				)}
			/>
			<div className="grid two">
				<Surface title={t('ui.static.retrieval.backend.fa3c92fd', 'Retrieval backend')}>
					<div className="metric-value">
						{String(retrievalStatus?.backend ?? t('app.runtime.card.unknown', 'unknown'))}
					</div>
					<div className="metric-label">{retrievalPosture}</div>
				</Surface>
				<Surface title={t('ui.static.memory.items.d8a2b209', 'Memory items')}>
					<div className="metric-value">{overview.memoryItems.length}</div>
					<div className="metric-label">
						{t('app.pages.memoryRecordsInSqlite', 'records in SQLite')}
					</div>
				</Surface>
			</div>
		</>
	);
}

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
	}, [selectedEvidenceId]);
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
	}, [patchArtifact, selectedEvidenceId, token]);
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
	}, [securityArtifact, selectedEvidenceId, token]);
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
				{detailErrorText ? (
					<div className="form-error" role="alert">
						{detailErrorText}
					</div>
				) : null}
				{selectedEvidenceId && !selectedPackage ? (
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
								{diffErrorText ? (
									<div className="form-error" role="alert">
										{diffErrorText}
									</div>
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
								{securityErrorText ? (
									<div className="form-error" role="alert">
										{securityErrorText}
									</div>
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

/**
 * Governance console: captures risks, architecture decisions and next steps as
 * first-class operational records (not chat notes), with create/update forms,
 * client-side filters and a risk-status workflow. Writes require an explicit
 * operational project; high/critical risks and accepted decisions enforce extra fields.
 */
export function GovernancePage({
	overview,
	selectedProject,
	mutate,
}: {
	overview: Overview;
	selectedProject: Project | null;
	mutate: Mutate;
}) {
	const { t } = useI18n();
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
		!query ||
		values.some((value) =>
			String(value ?? '')
				.toLowerCase()
				.includes(query),
		);
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
			setError(t('ui.static.risk.title.is.required.469006de', 'Risk title is required.'));
			return;
		}
		if (['high', 'critical'].includes(riskSeverity) && !riskMitigation.trim()) {
			setError(
				t(
					'ui.static.high.and.critical.risks.require.mitigation.d2100035',
					'High and critical risks require mitigation.',
				),
			);
			return;
		}
		if (!project) {
			setError(
				t(
					'ui.static.a.project.is.required.before.creating.governance.records.0a36e1b5',
					'A project is required before creating governance records.',
				),
			);
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate(
				(token) =>
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
			setError(
				saveError instanceof Error
					? saveError.message
					: t('app.pages.errRiskCreation', 'Risk creation failed.'),
			);
		} finally {
			setBusy(false);
		}
	};
	const saveRiskUpdate = async () => {
		if (!selectedRiskId) {
			setError(
				t(
					'ui.static.a.risk.is.required.before.updating.status.d945e7fa',
					'A risk is required before updating status.',
				),
			);
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate(
				(token) => updateRisk(token, selectedRiskId, { status: riskUpdateStatus }),
				{ awaitRefresh: false },
			);
			setRiskRows((current) => upsertNewestById(current, result.risk));
		} catch (saveError) {
			setError(
				saveError instanceof Error
					? saveError.message
					: t('app.pages.errRiskUpdate', 'Risk update failed.'),
			);
		} finally {
			setBusy(false);
		}
	};
	const saveDecision = async () => {
		if (!decisionTitle.trim()) {
			setError(t('ui.static.decision.title.is.required.2f387ea8', 'Decision title is required.'));
			return;
		}
		if (decisionStatus === 'accepted' && (!decisionContext.trim() || !decisionText.trim())) {
			setError(
				t(
					'ui.static.accepted.decisions.require.context.and.decision.text.b5edc6fa',
					'Accepted decisions require context and decision text.',
				),
			);
			return;
		}
		if (!project) {
			setError(
				t(
					'ui.static.a.project.is.required.before.creating.governance.records.0a36e1b5',
					'A project is required before creating governance records.',
				),
			);
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate(
				(token) =>
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
			setError(
				saveError instanceof Error
					? saveError.message
					: t('app.pages.errDecisionCreation', 'Decision creation failed.'),
			);
		} finally {
			setBusy(false);
		}
	};
	const saveNextStep = async () => {
		if (!nextStepTitle.trim()) {
			setError(t('ui.static.next.step.title.is.required.92b152bd', 'Next step title is required.'));
			return;
		}
		if (!project) {
			setError(
				t(
					'ui.static.a.project.is.required.before.creating.governance.records.0a36e1b5',
					'A project is required before creating governance records.',
				),
			);
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate(
				(token) =>
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
			setError(
				saveError instanceof Error
					? saveError.message
					: t('app.pages.errNextStepCreation', 'Next step creation failed.'),
			);
		} finally {
			setBusy(false);
		}
	};
	return (
		<>
			<PageHeader
				kicker={t('ui.static.engineering.judgement.94b6be3b', 'Engineering judgement')}
				title={t('app.nav.governance', 'Governance')}
				summary={t(
					'ui.static.risks.decisions.and.next.steps.are.operational.records.not.c.121069e8',
					'Risks, decisions and next steps are operational records, not comments buried in chat.',
				)}
			/>
			<div className="grid three">
				<Surface title={t('ui.static.risks.92ddd0d2', 'Risks')}>
					<div className="metric-value">{riskRows.length}</div>
				</Surface>
				<Surface title={t('ui.static.decisions.af2f32cc', 'Decisions')}>
					<div className="metric-value">{decisionRows.length}</div>
				</Surface>
				<Surface title={t('ui.static.next.steps.11fc1420', 'Next steps')}>
					<div className="metric-value">{nextStepRows.length}</div>
				</Surface>
			</div>
			<Surface title={t('ui.static.strict.record.forms.09648e18', 'Strict record forms')}>
				<div className="inline">
					<Badge tone={project ? 'ok' : 'warn'}>
						{project
							? project.name
							: t('app.settings.project.noOperationalBadge', 'no operational project')}
					</Badge>
					{project ? null : (
						<span className="field-help">
							{t(
								'ui.static.select.an.active.operational.project.in.settings.before.crea.cdedb6c4',
								'Select an active operational project in Settings before creating governance records.',
							)}
						</span>
					)}
				</div>
				<div className="grid three">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="risk-title">{t('ui.static.risk.title.d2581c4f', 'Risk title')}</label>
							<input
								id="risk-title"
								className="input"
								value={riskTitle}
								onChange={(event) => setRiskTitle(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="risk-severity">
								{t('ui.static.risk.severity.755bc9ed', 'Risk severity')}
							</label>
							<select
								id="risk-severity"
								className="select"
								value={riskSeverity}
								onChange={(event) => setRiskSeverity(event.target.value as RiskSeverity)}
							>
								<option value="low">low</option>
								<option value="medium">medium</option>
								<option value="high">high</option>
								<option value="critical">critical</option>
							</select>
						</div>
						<div className="field">
							<label htmlFor="risk-mitigation">
								{t('ui.static.risk.mitigation.515b5711', 'Risk mitigation')}
							</label>
							<input
								id="risk-mitigation"
								className="input"
								value={riskMitigation}
								onChange={(event) => setRiskMitigation(event.target.value)}
							/>
						</div>
						<button
							className="button primary"
							type="button"
							disabled={busy}
							onClick={() => {
								void saveRisk();
							}}
						>
							{t('ui.static.save.risk.33c025b0', 'Save risk')}
						</button>
					</div>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="decision-title">
								{t('ui.static.decision.title.ed22b7fe', 'Decision title')}
							</label>
							<input
								id="decision-title"
								className="input"
								value={decisionTitle}
								onChange={(event) => setDecisionTitle(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="decision-status">
								{t('ui.static.decision.status.9f1c54f2', 'Decision status')}
							</label>
							<select
								id="decision-status"
								className="select"
								value={decisionStatus}
								onChange={(event) =>
									setDecisionStatus(event.target.value as ArchitectureDecisionStatus)
								}
							>
								<option value="proposed">proposed</option>
								<option value="accepted">accepted</option>
								<option value="rejected">rejected</option>
								<option value="superseded">superseded</option>
								<option value="deprecated">deprecated</option>
							</select>
						</div>
						<div className="field">
							<label htmlFor="decision-context">
								{t('ui.static.decision.context.b59edd63', 'Decision context')}
							</label>
							<input
								id="decision-context"
								className="input"
								value={decisionContext}
								onChange={(event) => setDecisionContext(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="decision-text">
								{t('ui.static.decision.text.a276ce0a', 'Decision text')}
							</label>
							<input
								id="decision-text"
								className="input"
								value={decisionText}
								onChange={(event) => setDecisionText(event.target.value)}
							/>
						</div>
						<button
							className="button primary"
							type="button"
							disabled={busy}
							onClick={() => {
								void saveDecision();
							}}
						>
							{t('ui.static.save.decision.406fc627', 'Save decision')}
						</button>
					</div>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="next-step-title">
								{t('ui.static.next.step.title.2984700e', 'Next step title')}
							</label>
							<input
								id="next-step-title"
								className="input"
								value={nextStepTitle}
								onChange={(event) => setNextStepTitle(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="next-step-priority">
								{t('ui.static.next.step.priority.1652a14b', 'Next step priority')}
							</label>
							<select
								id="next-step-priority"
								className="select"
								value={nextStepPriority}
								onChange={(event) => setNextStepPriority(event.target.value as NextStepPriority)}
							>
								<option value="low">low</option>
								<option value="medium">medium</option>
								<option value="high">high</option>
								<option value="urgent">urgent</option>
							</select>
						</div>
						<button
							className="button primary"
							type="button"
							disabled={busy}
							onClick={() => {
								void saveNextStep();
							}}
						>
							{t('ui.static.save.next.step.7e6ec275', 'Save next step')}
						</button>
					</div>
				</div>
				{error ? (
					<div className="form-error" role="alert">
						{error}
					</div>
				) : null}
			</Surface>
			<div className="grid two">
				<Surface title={t('ui.static.governance.filters.7ded1ef3', 'Governance filters')}>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="governance-filter">
								{t('ui.static.governance.filter.092bf483', 'Governance filter')}
							</label>
							<input
								id="governance-filter"
								className="input"
								value={governanceFilter}
								onChange={(event) => setGovernanceFilter(event.target.value)}
								placeholder={t(
									'ui.static.filter.risks.decisions.and.next.steps.17f70033',
									'Filter risks, decisions, and next steps',
								)}
							/>
						</div>
						<div className="field">
							<label htmlFor="risk-status-filter">
								{t('ui.static.risk.status.filter.5a81dbb9', 'Risk status filter')}
							</label>
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
				<Surface title={t('ui.static.risk.update.form.63b4d923', 'Risk update form')}>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="risk-update-id">
								{t('ui.static.risk.to.update.4f30deec', 'Risk to update')}
							</label>
							<select
								id="risk-update-id"
								className="select"
								value={selectedRiskId}
								disabled={!riskRows.length}
								onChange={(event) => setRiskUpdateId(event.target.value)}
							>
								{riskRows.map((risk) => (
									<option key={risk.id} value={risk.id}>
										{risk.title}
									</option>
								))}
							</select>
						</div>
						<div className="field">
							<label htmlFor="risk-update-status">
								{t('ui.static.risk.update.status.b82f73d5', 'Risk update status')}
							</label>
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
						<button
							className="button primary"
							type="button"
							disabled={!selectedRiskId || busy}
							onClick={() => {
								void saveRiskUpdate();
							}}
						>
							{t('ui.static.update.risk.status.4ed521f0', 'Update risk status')}
						</button>
					</div>
				</Surface>
			</div>
			<div className="grid three">
				<Surface title={t('ui.static.risk.register.e2cb59b0', 'Risk register')}>
					<DataTable
						rows={filteredRisks}
						empty={
							<EmptyState
								title={t('ui.static.no.risks.df25a300', 'No risks')}
								body={t(
									'ui.static.open.technical.and.product.risks.appear.here.68f75ad6',
									'Open technical and product risks appear here.',
								)}
							/>
						}
						columns={[
							{
								key: 'title',
								label: t('ui.static.risk.5a8f23f5', 'Risk'),
								render: (row) => String(row.title ?? ''),
							},
							{
								key: 'severity',
								label: t('app.workbench.logs.colSeverity', 'Severity'),
								render: (row) => (
									<Badge tone={toneForStatus(String(row.severity ?? ''))}>
										{String(row.severity ?? '')}
									</Badge>
								),
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
						]}
					/>
				</Surface>
				<Surface title={t('ui.static.decision.records.606d98ca', 'Decision records')}>
					<DataTable
						rows={filteredDecisions}
						empty={
							<EmptyState
								title={t('ui.static.no.decisions.15ab90ea', 'No decisions')}
								body={t(
									'ui.static.architecture.decisions.should.be.explicit.and.linked.to.risk.0bd4f789',
									'Architecture decisions should be explicit and linked to risks.',
								)}
							/>
						}
						columns={[
							{
								key: 'title',
								label: t('ui.static.decision.7f59a1f1', 'Decision'),
								render: (row) => String(row.title ?? ''),
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
						]}
					/>
				</Surface>
				<Surface title={t('ui.static.next.steps.11fc1420', 'Next steps')}>
					<DataTable
						rows={filteredNextSteps}
						empty={
							<EmptyState
								title={t('ui.static.no.next.steps.dcb989e0', 'No next steps')}
								body={t(
									'ui.static.mitigations.and.follow.up.work.appear.here.3535c646',
									'Mitigations and follow-up work appear here.',
								)}
							/>
						}
						columns={[
							{
								key: 'title',
								label: t('ui.static.step.dc416e10', 'Step'),
								render: (row) => String(row.title ?? ''),
							},
							{
								key: 'priority',
								label: t('ui.static.priority.886cbff9', 'Priority'),
								render: (row) => (
									<Badge tone={toneForStatus(String(row.priority ?? ''))}>
										{String(row.priority ?? '')}
									</Badge>
								),
							},
						]}
					/>
				</Surface>
			</div>
		</>
	);
}

/**
 * Integrations console: registers local stdio MCP servers and lists registered
 * servers plus IDE connection events. Validates the server id and rejects shell
 * operators in the command; registration only stores argv-style config — execution
 * still passes through broker, policy and sandbox.
 */
export function IntegrationsPage({ overview, mutate }: { overview: Overview; mutate: Mutate }) {
	const { t } = useI18n();
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
			setError(
				t(
					'ui.static.mcp.server.id.must.use.lowercase.letters.numbers.dashes.or.u.727ded15',
					'MCP server id must use lowercase letters, numbers, dashes or underscores.',
				),
			);
			return;
		}
		if (!command.trim()) {
			setError(t('ui.static.mcp.command.is.required.5ea1eac7', 'MCP command is required.'));
			return;
		}
		if (/[;&|<>`\r\n]/.test(command)) {
			setError(
				t(
					'ui.static.mcp.command.must.be.a.single.argv.style.command.without.shel.66279188',
					'MCP command must be a single argv-style command without shell operators.',
				),
			);
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate(
				(token) =>
					registerMcpServer(token, {
						id: serverId,
						command: command.trim(),
						transport,
						metadata: { source: 'integrations_form' },
					}),
				{ awaitRefresh: false },
			);
			setMcpServers((current) => upsertNewestById(current, result.mcpServer));
		} catch (registerError) {
			setError(
				registerError instanceof Error
					? registerError.message
					: t('app.pages.errMcpRegistration', 'MCP registration failed.'),
			);
		} finally {
			setBusy(false);
		}
	};
	return (
		<>
			<PageHeader
				kicker={t('ui.static.external.tools.ab4115a7', 'External tools')}
				title={t('app.nav.integrations', 'Integrations')}
				summary={t(
					'ui.static.mcp.ide.git.and.automation.integrations.are.optional.adapter.fd985428',
					'MCP, IDE, Git and automation integrations are optional adapters, never hidden core dependencies.',
				)}
			/>
			<div className="grid two">
				<Surface
					title={t(
						'ui.static.strict.mcp.registration.form.718f1920',
						'Strict MCP registration form',
					)}
				>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="mcp-server-id">
								{t('ui.static.mcp.server.id.35074cd3', 'MCP server id')}
							</label>
							<input
								id="mcp-server-id"
								className="input"
								value={serverId}
								pattern="[a-z0-9][a-z0-9_-]{2,63}"
								onChange={(event) => setServerId(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="mcp-command">
								{t('ui.static.mcp.command.d01a7d2d', 'MCP command')}
							</label>
							<input
								id="mcp-command"
								className="input"
								value={command}
								placeholder={t(
									'ui.static.installed.mcp.server.command.a52d6621',
									'Installed MCP server command',
								)}
								onChange={(event) => setCommand(event.target.value)}
							/>
							<div className="field-help">
								{t(
									'ui.static.stored.as.argv.style.config.execution.still.goes.through.bro.481beb62',
									'Stored as argv-style config; execution still goes through broker, policy and sandbox.',
								)}
							</div>
						</div>
						<div className="field">
							<label htmlFor="mcp-transport">
								{t('ui.static.mcp.transport.70f1719d', 'MCP transport')}
							</label>
							<select
								id="mcp-transport"
								className="select"
								value={transport}
								onChange={(event) => setTransport(event.target.value as McpTransport)}
							>
								<option value="stdio">stdio</option>
							</select>
						</div>
						{error ? (
							<div className="form-error" role="alert">
								{error}
							</div>
						) : null}
						<button
							className="button primary"
							type="button"
							onClick={() => {
								void registerServer();
							}}
							disabled={busy}
						>
							{busy
								? t('app.pages.registeringMcpServer', 'Registering MCP server')
								: t('ui.static.register.mcp.server.b3f30e86', 'Register MCP server')}
						</button>
					</div>
				</Surface>
				<Surface title={t('ui.static.registered.mcp.servers.d5439a1b', 'Registered MCP servers')}>
					<DataTable
						rows={mcpServers}
						empty={
							<EmptyState
								title={t('ui.static.no.mcp.servers.3604d548', 'No MCP servers')}
								body={t(
									'ui.static.register.local.stdio.mcp.servers.before.runtime.adapters.can.fe48c69d',
									'Register local stdio MCP servers before runtime adapters can call them.',
								)}
							/>
						}
						columns={[
							{
								key: 'id',
								label: t('ui.static.server.cb0cb170', 'Server'),
								render: (row) => <span className="mono">{String(row.id ?? '')}</span>,
							},
							{
								key: 'transport',
								label: t('ui.static.transport.c10d76c9', 'Transport'),
								render: (row) => String(row.transport ?? ''),
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
						]}
					/>
				</Surface>
				<Surface title={t('ui.static.ide.connections.4be76e0e', 'IDE connections')}>
					<DataTable
						rows={overview.auditEvents.filter((row) => String(row.action ?? '').includes('ide'))}
						empty={
							<EmptyState
								title={t('ui.static.no.integration.events.1eaf9391', 'No integration events')}
								body={t(
									'ui.static.integration.activity.appears.in.audit.records.f846c944',
									'Integration activity appears in audit records.',
								)}
							/>
						}
						columns={[
							{
								key: 'action',
								label: t('ui.static.action.97c89a4d', 'Action'),
								render: (row) => <span className="mono">{String(row.action ?? '')}</span>,
							},
							{
								key: 'target',
								label: t('ui.static.target.61ad50a9', 'Target'),
								render: (row) => String(row.target ?? ''),
							},
						]}
					/>
				</Surface>
			</div>
		</>
	);
}

/**
 * Append-only audit trail: lists every recorded mutation with its actor, target and
 * action so changes are traceable to who made them and what they touched.
 */
export function AuditPage({ overview }: { overview: Overview }) {
	const { t } = useI18n();
	return (
		<>
			<PageHeader
				kicker={t('ui.static.traceability.61d2e70b', 'Traceability')}
				title={t('app.nav.audit', 'Audit Log')}
				summary={t(
					'ui.static.every.mutation.needs.actor.target.payload.and.event.correlat.6735cfc2',
					'Every change records who made it, what it targeted, the data sent and a linked event.',
				)}
			/>
			<Surface title={t('ui.static.audit.records.e11faea4', 'Audit records')}>
				<DataTable
					rows={overview.auditEvents}
					empty={
						<EmptyState
							title={t('ui.static.no.audit.records.3d57cdc9', 'No audit records')}
							body={t(
								'ui.static.mutating.api.calls.will.be.recorded.here.ddf49c4f',
								'Every change to the system is recorded here.',
							)}
						/>
					}
					columns={[
						{
							key: 'action',
							label: t('ui.static.action.97c89a4d', 'Action'),
							render: (row) => <span className="mono">{String(row.action ?? '')}</span>,
						},
						{
							key: 'actor',
							label: t('ui.static.actor.cbd19b5c', 'Actor'),
							render: (row) => String(row.actor ?? ''),
						},
						{
							key: 'target',
							label: t('ui.static.target.61ad50a9', 'Target'),
							render: (row) => String(row.target ?? ''),
						},
					]}
				/>
			</Surface>
		</>
	);
}
