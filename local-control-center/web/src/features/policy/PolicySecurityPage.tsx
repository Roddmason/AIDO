/**
 * Policy & Security route page: edits the strict sandbox profile and audits the
 * permission decisions, grants, policy revisions and tool-call executions that gate
 * every sensitive action, reading from the shared Overview snapshot and writing
 * through the gated `mutate` handshake.
 * @author Rodrigo Mason
 */
import { useEffect, useState } from 'react';
import { updateSandboxProfile } from '../../api/client';
import type { Overview, PolicyRevision } from '../../api/types';
import {
	StatusChip as Badge,
	DataTable,
	Drawer,
	EmptyState,
	PageHeader,
	Surface,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';

type Mutate = <T>(
	operation: (token: string) => Promise<T>,
	options?: { awaitRefresh?: boolean },
) => Promise<T>;

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
							{/* biome-ignore lint/a11y/useSemanticElements: role="table" preserves the tabular semantics the CSS grid layout (.diff-grid display:grid) depends on; a native <table> resets display:table and breaks the grid columns. */}
							<div
								className="diff-grid"
								role="table"
								aria-label={t(
									'ui.static.policy.revision.changed.fields.1d6ec331',
									'Policy revision changed fields',
								)}
							>
								{/* biome-ignore lint/a11y/useSemanticElements: paired with the role="table" grid container above; a native <tr> resets display:table-row and breaks the grid-template-columns layout. */}
								{/* biome-ignore lint/a11y/useFocusableInteractive: header row is presentational within the ARIA table; adding tabIndex would create a spurious keyboard tab stop with no interactive behavior. */}
								<div className="diff-row diff-head" role="row">
									{/* biome-ignore lint/a11y/useSemanticElements: role="columnheader" preserves table-header semantics; a native <th> carries table-cell display that breaks the grid layout. */}
									{/* biome-ignore lint/a11y/useFocusableInteractive: static column header within the ARIA table is not interactive; adding tabIndex would create a spurious keyboard tab stop. */}
									<div role="columnheader">{t('ui.static.field.c326a466', 'Field')}</div>
									{/* biome-ignore lint/a11y/useSemanticElements: role="columnheader" preserves table-header semantics; a native <th> carries table-cell display that breaks the grid layout. */}
									{/* biome-ignore lint/a11y/useFocusableInteractive: static column header within the ARIA table is not interactive; adding tabIndex would create a spurious keyboard tab stop. */}
									<div role="columnheader">{t('ui.static.before.74f39697', 'Before')}</div>
									{/* biome-ignore lint/a11y/useSemanticElements: role="columnheader" preserves table-header semantics; a native <th> carries table-cell display that breaks the grid layout. */}
									{/* biome-ignore lint/a11y/useFocusableInteractive: static column header within the ARIA table is not interactive; adding tabIndex would create a spurious keyboard tab stop. */}
									<div role="columnheader">{t('ui.static.after.79ba5e1b', 'After')}</div>
								</div>
								{(Array.isArray(selectedRevision.changedFields)
									? selectedRevision.changedFields
									: []
								).map((field) => {
									const previous = selectedRevision.previous as Record<string, unknown> | undefined;
									const updated = selectedRevision.updated as Record<string, unknown> | undefined;
									return (
										// biome-ignore lint/a11y/useSemanticElements: role="row" preserves the table-row semantics the CSS grid layout depends on; a native <tr> resets display:table-row and breaks grid-template-columns.
										// biome-ignore lint/a11y/useFocusableInteractive: data row within the ARIA table is not interactive; adding tabIndex would create a spurious keyboard tab stop.
										<div className="diff-row" role="row" key={String(field)}>
											{/* biome-ignore lint/a11y/useSemanticElements: role="cell" preserves table-cell semantics; a native <td> carries table-cell display that breaks the grid layout and the .diff-row > div CSS selectors. */}
											<div role="cell" className="mono">
												{String(field)}
											</div>
											{/* biome-ignore lint/a11y/useSemanticElements: role="cell" preserves table-cell semantics; a native <td> carries table-cell display that breaks the grid layout and the .diff-row > div CSS selectors. */}
											<div role="cell" className="diff-before">
												{JSON.stringify(previous?.[String(field)] ?? null)}
											</div>
											{/* biome-ignore lint/a11y/useSemanticElements: role="cell" preserves table-cell semantics; a native <td> carries table-cell display that breaks the grid layout and the .diff-row > div CSS selectors. */}
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
