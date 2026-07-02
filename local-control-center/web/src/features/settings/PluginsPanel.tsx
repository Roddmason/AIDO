/**
 * Settings → Plugins panel: operator console for the fail-closed local plugin registry.
 *
 * Eight views over one dataset (installed, available-from-folder, disabled, blocked,
 * permissions, skills, agents, tools) plus the install/validate/enable/disable actions.
 * Every mutation goes through the typed plugins API; scan results are read-only until the
 * operator explicitly installs a validated candidate, and blocked installs surface the exact
 * validator reason so third-party manifests stay auditable.
 * @author Rodrigo Mason
 */

import {
	FileJson,
	FolderSearch,
	KeyRound,
	PackagePlus,
	Power,
	PowerOff,
	Puzzle,
	RefreshCw,
	ShieldCheck,
} from 'lucide-react';
import { type FormEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';

import {
	disablePlugin,
	enablePlugin,
	installLocalPlugin,
	listPluginInstallEvents,
	listPlugins,
	type PluginInstallEvent,
	type PluginRecord,
	type PluginScanCandidate,
	scanLocalPlugins,
	validatePlugin,
} from '../../api/client';
import { Badge, DataTable, EmptyState } from '../../components/primitives';
import {
	Button,
	Dialog,
	ErrorState,
	Skeleton,
	Tabs,
	TextField,
	useToast,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';

type PluginVersion = NonNullable<PluginRecord['activeVersion']>;

type PluginView =
	| 'installed'
	| 'available'
	| 'disabled'
	| 'blocked'
	| 'permissions'
	| 'skills'
	| 'agents'
	| 'tools';

/** Tone for a plugin/entrypoint lifecycle status; fail-closed states read as danger. */
function pluginTone(status: string): 'ok' | 'warn' | 'danger' | 'info' | 'pending' {
	if (status === 'enabled' || status === 'valid') return 'ok';
	if (status === 'blocked' || status === 'invalid') return 'danger';
	if (status === 'disabled') return 'pending';
	return 'info';
}

/** Tone for a manifest trust level: only first-party tiers read as settled. */
function trustTone(trustLevel: string): 'ok' | 'info' | 'pending' {
	if (trustLevel === 'core' || trustLevel === 'first_party') return 'ok';
	if (trustLevel === 'verified') return 'info';
	return 'pending';
}

/** Tone for a declared permission risk level; unknown levels stay visibly unresolved. */
function riskTone(riskLevel: string): 'ok' | 'pending' | 'danger' {
	if (riskLevel === 'low') return 'ok';
	if (riskLevel === 'medium') return 'pending';
	return 'danger';
}

export function PluginsPanel({ token }: { token: string }) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [view, setView] = useState<PluginView>('installed');
	const [plugins, setPlugins] = useState<PluginRecord[]>([]);
	const [blockedEvents, setBlockedEvents] = useState<PluginInstallEvent[]>([]);
	const [scanPath, setScanPath] = useState('');
	const [scanRoot, setScanRoot] = useState('');
	const [candidates, setCandidates] = useState<PluginScanCandidate[] | null>(null);
	const [pending, setPending] = useState<string | null>(null);
	const [loaded, setLoaded] = useState(false);
	const [error, setError] = useState('');
	const [manifestPlugin, setManifestPlugin] = useState<PluginRecord | null>(null);
	const [permissionsPlugin, setPermissionsPlugin] = useState<PluginRecord | null>(null);

	const refresh = useCallback(async () => {
		setPending('load');
		setError('');
		try {
			const [pluginsPayload, eventsPayload] = await Promise.all([
				listPlugins(),
				listPluginInstallEvents({ status: 'blocked', limit: 100 }),
			]);
			setPlugins(pluginsPayload.plugins);
			setBlockedEvents(eventsPayload.events);
			setLoaded(true);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setPending(null);
		}
	}, []);

	useEffect(() => {
		void refresh();
	}, [refresh]);

	const notifyError = (err: unknown) =>
		notify({
			title: t('settings.plugins.operationFailed', 'Plugin operation failed'),
			body: err instanceof Error ? err.message : String(err),
			tone: 'danger',
		});

	const runScan = useCallback(
		async (path: string) => {
			const payload = await scanLocalPlugins(token, { path });
			setScanRoot(payload.root);
			setCandidates(payload.candidates);
			return payload;
		},
		[token],
	);

	const handleScan = async (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		if (!token || pending || !scanPath.trim()) return;
		setPending('scan');
		try {
			const payload = await runScan(scanPath.trim());
			notify({
				title: t('settings.plugins.scanned', 'Scan complete'),
				body: String(payload.candidates.length),
				tone: 'ok',
			});
		} catch (err) {
			notifyError(err);
		} finally {
			setPending(null);
		}
	};

	const handleInstall = async (candidate: PluginScanCandidate) => {
		if (!token || pending) return;
		setPending(`install:${candidate.path}`);
		try {
			await installLocalPlugin(token, { path: candidate.path });
			await refresh();
			if (scanRoot) await runScan(scanRoot);
			notify({ title: t('settings.plugins.installedToast', 'Plugin installed'), tone: 'ok' });
		} catch (err) {
			notifyError(err);
			await refresh();
		} finally {
			setPending(null);
		}
	};

	const handleValidate = async (plugin: PluginRecord) => {
		if (!token || pending) return;
		setPending(`validate:${plugin.id}`);
		try {
			const result = await validatePlugin(token, plugin.id);
			await refresh();
			notify({
				title: result.valid
					? t('settings.plugins.manifestValid', 'Manifest valid')
					: t('settings.plugins.manifestInvalid', 'Manifest invalid'),
				body: result.issues.join(' ') || undefined,
				tone: result.valid ? 'ok' : 'danger',
			});
		} catch (err) {
			notifyError(err);
		} finally {
			setPending(null);
		}
	};

	const handleToggle = async (plugin: PluginRecord) => {
		if (!token || pending) return;
		const enabling = plugin.status !== 'enabled';
		setPending(`toggle:${plugin.id}`);
		try {
			await (enabling ? enablePlugin(token, plugin.id) : disablePlugin(token, plugin.id));
			await refresh();
			notify({
				title: enabling
					? t('settings.plugins.enabledToast', 'Plugin enabled')
					: t('settings.plugins.disabledToast', 'Plugin disabled'),
				tone: 'ok',
			});
		} catch (err) {
			notifyError(err);
			await refresh();
		} finally {
			setPending(null);
		}
	};

	const disabled = !token || pending !== null;
	const disabledPlugins = useMemo(
		() => plugins.filter((plugin) => plugin.status === 'disabled'),
		[plugins],
	);
	const enabledCount = plugins.length - disabledPlugins.length;

	const permissionRows = useMemo(
		() =>
			plugins.flatMap((plugin) =>
				(plugin.activeVersion?.permissions ?? []).map((record) => ({
					...record,
					pluginName: plugin.name,
				})),
			),
		[plugins],
	);
	const skillRows = useMemo(
		() =>
			plugins.flatMap((plugin) =>
				(plugin.activeVersion?.skills ?? []).map((record) => ({
					...record,
					pluginName: plugin.name,
				})),
			),
		[plugins],
	);
	const agentRows = useMemo(
		() =>
			plugins.flatMap((plugin) =>
				(plugin.activeVersion?.agents ?? []).map((record) => ({
					...record,
					pluginName: plugin.name,
				})),
			),
		[plugins],
	);
	const toolRows = useMemo(
		() =>
			plugins.flatMap((plugin) =>
				(plugin.activeVersion?.tools ?? []).map((record) => ({
					...record,
					pluginName: plugin.name,
				})),
			),
		[plugins],
	);

	const pluginColumn = {
		key: 'plugin',
		label: t('settings.plugins.col.plugin', 'Plugin'),
		render: (row: { pluginName: string }) => row.pluginName,
	};
	const statusLabel = t('ui.static.status.bae7d5be', 'Status');
	const entrypointEmptyBody = t(
		'settings.plugins.emptyEntrypointsBody',
		'Install a plugin to inspect its declared contract.',
	);

	const installedTable = (rows: PluginRecord[], empty: ReactNode) => (
		<DataTable
			rows={rows}
			caption={t('settings.plugins.tab.installed', 'Installed')}
			empty={empty}
			columns={[
				{
					key: 'name',
					label: t('settings.plugins.col.plugin', 'Plugin'),
					render: (row) => (
						<>
							{row.name}
							<div className="field-help mono">{row.id}</div>
						</>
					),
				},
				{
					key: 'version',
					label: t('settings.plugins.col.version', 'Version'),
					render: (row) => <span className="mono">{row.activeVersion?.version ?? '—'}</span>,
				},
				{
					key: 'trust',
					label: t('settings.plugins.col.trust', 'Trust'),
					render: (row) => <Badge tone={trustTone(row.trustLevel)}>{row.trustLevel}</Badge>,
				},
				{
					key: 'status',
					label: statusLabel,
					render: (row) => <Badge tone={pluginTone(row.status)}>{row.status}</Badge>,
				},
				{
					key: 'actions',
					label: t('settings.plugins.col.actions', 'Actions'),
					render: (row) => (
						<div className="inline">
							<Button
								onClick={() => void handleValidate(row)}
								disabled={disabled}
								loading={pending === `validate:${row.id}`}
								icon={<ShieldCheck size={14} />}
							>
								{t('settings.plugins.validate', 'Validate')}
							</Button>
							<Button
								variant={row.status === 'enabled' ? 'secondary' : 'primary'}
								onClick={() => void handleToggle(row)}
								disabled={disabled}
								loading={pending === `toggle:${row.id}`}
								icon={row.status === 'enabled' ? <PowerOff size={14} /> : <Power size={14} />}
							>
								{row.status === 'enabled'
									? t('settings.plugins.disable', 'Disable')
									: t('settings.plugins.enable', 'Enable')}
							</Button>
							<Button
								onClick={() => setPermissionsPlugin(row)}
								disabled={!row.activeVersion}
								icon={<KeyRound size={14} />}
							>
								{t('settings.plugins.viewPermissions', 'View permissions')}
							</Button>
							<Button
								onClick={() => setManifestPlugin(row)}
								disabled={!row.activeVersion}
								icon={<FileJson size={14} />}
							>
								{t('settings.plugins.viewManifest', 'View manifest')}
							</Button>
						</div>
					),
				},
			]}
		/>
	);

	const renderView = () => {
		if (view === 'installed') {
			return installedTable(
				plugins,
				<EmptyState
					title={t('settings.plugins.emptyInstalledTitle', 'No plugins installed')}
					body={t(
						'settings.plugins.emptyInstalledBody',
						'Scan a local folder and install a validated plugin to extend AIDO.',
					)}
					action={
						<Button
							variant="primary"
							onClick={() => setView('available')}
							icon={<PackagePlus size={14} />}
						>
							{t('settings.plugins.installFromFolder', 'Install from folder')}
						</Button>
					}
				/>,
			);
		}
		if (view === 'available') {
			return (
				<div className="stack">
					<form className="form-grid" onSubmit={(event) => void handleScan(event)}>
						<TextField
							label={t('settings.plugins.scanPath', 'Local plugin folder')}
							help={t(
								'settings.plugins.scanPathHelp',
								'The folder is scanned read-only: nothing is executed, copied, or installed during a scan.',
							)}
							value={scanPath}
							onChange={(event) => setScanPath(event.target.value)}
							required
						/>
						<Button
							className="settings-action"
							type="submit"
							variant="primary"
							disabled={disabled || !scanPath.trim()}
							loading={pending === 'scan'}
							icon={<FolderSearch size={15} />}
						>
							{t('settings.plugins.scan', 'Scan folder')}
						</Button>
					</form>
					{candidates === null ? (
						<EmptyState
							title={t('settings.plugins.emptyScanTitle', 'No scan yet')}
							body={t(
								'settings.plugins.emptyScanBody',
								'Point to a folder holding aido.plugin.json manifests to list installable candidates.',
							)}
						/>
					) : (
						<DataTable
							rows={candidates}
							caption={t('settings.plugins.tab.available', 'Available (local folder)')}
							empty={
								<EmptyState
									title={t('settings.plugins.emptyCandidatesTitle', 'No candidates found')}
									body={t(
										'settings.plugins.emptyCandidatesBody',
										'Neither this folder nor its direct children contain an aido.plugin.json manifest.',
									)}
								/>
							}
							columns={[
								{
									key: 'candidate',
									label: t('settings.plugins.col.candidate', 'Candidate'),
									render: (row) => (
										<>
											{row.name ?? row.id ?? '—'}
											<div className="field-help mono">{row.path}</div>
										</>
									),
								},
								{
									key: 'version',
									label: t('settings.plugins.col.version', 'Version'),
									render: (row) => <span className="mono">{row.version ?? '—'}</span>,
								},
								{
									key: 'trust',
									label: t('settings.plugins.col.trust', 'Trust'),
									render: (row) =>
										row.trustLevel ? (
											<Badge tone={trustTone(row.trustLevel)}>{row.trustLevel}</Badge>
										) : (
											<span className="muted">—</span>
										),
								},
								{
									key: 'entrypoints',
									label: t('settings.plugins.col.entrypoints', 'Entrypoints'),
									render: (row) => (
										<span className="mono">
											{row.skillsCount}/{row.agentsCount}/{row.toolsCount}
										</span>
									),
								},
								{
									key: 'validity',
									label: t('settings.plugins.col.validity', 'Validity'),
									render: (row) => (
										<>
											<Badge tone={row.valid ? 'ok' : 'danger'}>
												{row.valid
													? t('settings.plugins.candidateValid', 'valid')
													: t('settings.plugins.candidateBlocked', 'blocked')}
											</Badge>
											{row.issues[0] ? <div className="field-error">{row.issues[0]}</div> : null}
										</>
									),
								},
								{
									key: 'action',
									label: t('settings.plugins.col.action', 'Action'),
									render: (row) =>
										row.installed ? (
											<Badge tone="info">
												{t('settings.plugins.alreadyInstalled', 'installed')}
											</Badge>
										) : (
											<Button
												variant="primary"
												onClick={() => void handleInstall(row)}
												disabled={disabled || !row.valid}
												loading={pending === `install:${row.path}`}
												icon={<PackagePlus size={14} />}
											>
												{t('settings.plugins.install', 'Install')}
											</Button>
										),
								},
							]}
						/>
					)}
				</div>
			);
		}
		if (view === 'disabled') {
			return installedTable(
				disabledPlugins,
				<EmptyState
					title={t('settings.plugins.emptyDisabledTitle', 'No disabled plugins')}
					body={t(
						'settings.plugins.emptyDisabledBody',
						'Disabled plugins stay installed but none of their entrypoints can run.',
					)}
				/>,
			);
		}
		if (view === 'blocked') {
			return (
				<DataTable
					rows={blockedEvents}
					caption={t('settings.plugins.tab.blocked', 'Blocked')}
					empty={
						<EmptyState
							title={t('settings.plugins.emptyBlockedTitle', 'No blocked installs')}
							body={t(
								'settings.plugins.emptyBlockedBody',
								'Rejected manifests and failed validations appear here with the exact validator reason.',
							)}
						/>
					}
					columns={[
						{
							key: 'created',
							label: t('settings.plugins.col.recorded', 'Recorded'),
							render: (row) => <span className="mono">{row.createdAt}</span>,
						},
						{
							key: 'action',
							label: t('settings.plugins.col.action', 'Action'),
							render: (row) => <span className="mono">{row.action}</span>,
						},
						{
							key: 'plugin',
							label: t('settings.plugins.col.plugin', 'Plugin'),
							render: (row) => (
								<span className="mono">{row.pluginId ?? String(row.payload.path ?? '—')}</span>
							),
						},
						{
							key: 'reason',
							label: t('settings.plugins.col.reason', 'Reason'),
							render: (row) => row.reason,
						},
					]}
				/>
			);
		}
		if (view === 'permissions') {
			return (
				<DataTable
					rows={permissionRows}
					caption={t('settings.plugins.tab.permissions', 'Permissions')}
					empty={
						<EmptyState
							title={t('settings.plugins.emptyPermissionsTitle', 'No declared permissions')}
							body={entrypointEmptyBody}
						/>
					}
					columns={[
						pluginColumn,
						{
							key: 'permission',
							label: t('settings.plugins.col.permission', 'Permission'),
							render: (row) => <span className="mono">{row.permission}</span>,
						},
						{
							key: 'risk',
							label: t('settings.plugins.col.risk', 'Risk'),
							render: (row) => <Badge tone={riskTone(row.riskLevel)}>{row.riskLevel}</Badge>,
						},
						{
							key: 'status',
							label: statusLabel,
							render: (row) => <Badge tone={pluginTone(row.status)}>{row.status}</Badge>,
						},
						{
							key: 'reason',
							label: t('settings.plugins.col.reason', 'Reason'),
							render: (row) => row.reason,
						},
					]}
				/>
			);
		}
		if (view === 'skills') {
			return (
				<DataTable
					rows={skillRows}
					caption={t('settings.plugins.tab.skills', 'Skills')}
					empty={
						<EmptyState
							title={t('settings.plugins.emptySkillsTitle', 'No plugin skills')}
							body={entrypointEmptyBody}
						/>
					}
					columns={[
						pluginColumn,
						{
							key: 'path',
							label: t('app.workspace.summary.path', 'Path'),
							render: (row) => <span className="mono">{row.path}</span>,
						},
						{
							key: 'contract',
							label: t('settings.plugins.col.contract', 'Contract hash'),
							render: (row) => <span className="mono">{row.contractHash.slice(0, 19)}…</span>,
						},
						{
							key: 'status',
							label: statusLabel,
							render: (row) => <Badge tone={pluginTone(row.status)}>{row.status}</Badge>,
						},
					]}
				/>
			);
		}
		if (view === 'agents') {
			return (
				<DataTable
					rows={agentRows}
					caption={t('settings.plugins.tab.agents', 'Agents')}
					empty={
						<EmptyState
							title={t('settings.plugins.emptyAgentsTitle', 'No plugin agents')}
							body={entrypointEmptyBody}
						/>
					}
					columns={[
						pluginColumn,
						{
							key: 'role',
							label: t('ui.static.role.c3f104d1', 'Role'),
							render: (row) => row.role,
						},
						{
							key: 'capabilities',
							label: t('ui.static.capabilities.ca09c54b', 'Capabilities'),
							render: (row) => <span className="mono">{row.capabilities.join(', ')}</span>,
						},
						{
							key: 'status',
							label: statusLabel,
							render: (row) => <Badge tone={pluginTone(row.status)}>{row.status}</Badge>,
						},
					]}
				/>
			);
		}
		return (
			<DataTable
				rows={toolRows}
				caption={t('settings.plugins.tab.tools', 'Tools')}
				empty={
					<EmptyState
						title={t('settings.plugins.emptyToolsTitle', 'No plugin tools')}
						body={entrypointEmptyBody}
					/>
				}
				columns={[
					pluginColumn,
					{
						key: 'tool',
						label: t('settings.plugins.col.tool', 'Tool'),
						render: (row) => row.name,
					},
					{
						key: 'broker',
						label: t('settings.plugins.col.broker', 'Broker tool'),
						render: (row) => <span className="mono">{row.brokerTool}</span>,
					},
					{
						key: 'policy',
						label: t('settings.plugins.col.policy', 'Policy'),
						render: (row) => (
							<Badge tone={row.policyRequired ? 'ok' : 'danger'}>
								{row.policyRequired
									? t('settings.plugins.policyRequired', 'policy required')
									: t('settings.plugins.policyMissing', 'policy missing')}
							</Badge>
						),
					},
					{
						key: 'status',
						label: statusLabel,
						render: (row) => <Badge tone={pluginTone(row.status)}>{row.status}</Badge>,
					},
				]}
			/>
		);
	};

	const manifestVersion: PluginVersion | null = manifestPlugin?.activeVersion ?? null;
	const permissionsVersion: PluginVersion | null = permissionsPlugin?.activeVersion ?? null;

	return (
		<section className="stack" aria-labelledby="plugins-panel-title">
			<div className="surface-toolbar">
				<div className="inline">
					<Puzzle aria-hidden="true" size={17} />
					<h3 id="plugins-panel-title" className="surface-title">
						{t('settings.plugins.title', 'Plugin registry')}
					</h3>
				</div>
				<Button
					onClick={() => void refresh()}
					disabled={disabled}
					loading={pending === 'load'}
					icon={<RefreshCw size={15} />}
				>
					{t('settings.plugins.refresh', 'Refresh plugins')}
				</Button>
			</div>

			<p className="muted">
				{t(
					'settings.plugins.intro',
					'Install first-party and third-party plugins from local folders. Manifests are validated fail-closed before anything is persisted, and every install decision is audited.',
				)}
			</p>

			{error ? (
				<ErrorState
					title={t('settings.plugins.loadFailed', 'Plugin registry unavailable')}
					body={error}
					action={
						<Button onClick={() => void refresh()} disabled={pending !== null}>
							{t('settings.plugins.retry', 'Retry')}
						</Button>
					}
				/>
			) : null}

			<div className="inline">
				<Badge tone={plugins.length ? 'ok' : 'pending'}>
					{plugins.length} {t('settings.plugins.summaryInstalled', 'installed')}
				</Badge>
				<Badge tone={enabledCount ? 'ok' : 'pending'}>
					{enabledCount} {t('settings.plugins.summaryEnabled', 'enabled')}
				</Badge>
				<Badge tone={blockedEvents.length ? 'danger' : 'ok'}>
					{blockedEvents.length} {t('settings.plugins.summaryBlocked', 'blocked events')}
				</Badge>
			</div>

			<Tabs
				tabs={[
					{ id: 'installed', label: t('settings.plugins.tab.installed', 'Installed') },
					{
						id: 'available',
						label: t('settings.plugins.tab.available', 'Available (local folder)'),
					},
					{ id: 'disabled', label: t('settings.plugins.tab.disabled', 'Disabled') },
					{ id: 'blocked', label: t('settings.plugins.tab.blocked', 'Blocked') },
					{ id: 'permissions', label: t('settings.plugins.tab.permissions', 'Permissions') },
					{ id: 'skills', label: t('settings.plugins.tab.skills', 'Skills') },
					{ id: 'agents', label: t('settings.plugins.tab.agents', 'Agents') },
					{ id: 'tools', label: t('settings.plugins.tab.tools', 'Tools') },
				]}
				activeTab={view}
				onChange={(id) => setView(id as PluginView)}
				label={t('settings.plugins.tablist', 'Plugin views')}
				idBase="settings-plugins"
			>
				{loaded || error ? (
					renderView()
				) : (
					<div className="stack" aria-hidden="true">
						<Skeleton />
						<Skeleton />
						<Skeleton />
					</div>
				)}
			</Tabs>

			<Dialog
				open={manifestPlugin !== null}
				onClose={() => setManifestPlugin(null)}
				label={t('settings.plugins.manifestDialog', 'Plugin manifest')}
			>
				{manifestVersion ? (
					<div className="stack">
						<div className="settings-readouts">
							<div>
								<strong>{t('settings.plugins.col.plugin', 'Plugin')}</strong>
								<span className="mono">{manifestVersion.pluginId}</span>
							</div>
							<div>
								<strong>{t('settings.plugins.col.version', 'Version')}</strong>
								<span className="mono">{manifestVersion.version}</span>
							</div>
							<div>
								<strong>{t('settings.plugins.minAido', 'Min AIDO version')}</strong>
								<span className="mono">{manifestVersion.minAidoVersion}</span>
							</div>
							<div>
								<strong>{t('settings.plugins.manifestHash', 'Manifest hash')}</strong>
								<span className="mono">{manifestVersion.manifestHash}</span>
							</div>
							<div>
								<strong>{t('settings.plugins.packageHash', 'Package hash')}</strong>
								<span className="mono">{manifestVersion.packageHash}</span>
							</div>
							<div>
								<strong>{t('settings.plugins.manifestPath', 'Manifest path')}</strong>
								<span className="mono">{manifestVersion.manifestPath}</span>
							</div>
							<div>
								<strong>{t('settings.plugins.validatedAt', 'Validated')}</strong>
								<span className="mono">{manifestVersion.validatedAt}</span>
							</div>
						</div>
						<pre className="artifact-preview">
							{JSON.stringify(manifestVersion.manifest, null, 2)}
						</pre>
					</div>
				) : null}
			</Dialog>

			<Dialog
				open={permissionsPlugin !== null}
				onClose={() => setPermissionsPlugin(null)}
				label={t('settings.plugins.permissionsDialog', 'Plugin permissions')}
			>
				{permissionsVersion ? (
					<DataTable
						rows={permissionsVersion.permissions}
						caption={t('settings.plugins.permissionsDialog', 'Plugin permissions')}
						empty={
							<EmptyState
								title={t('settings.plugins.emptyPermissionsTitle', 'No declared permissions')}
								body={t(
									'settings.plugins.emptyPluginPermissionsBody',
									'This plugin manifest declares no permissions, so it gets none.',
								)}
							/>
						}
						columns={[
							{
								key: 'permission',
								label: t('settings.plugins.col.permission', 'Permission'),
								render: (row) => <span className="mono">{row.permission}</span>,
							},
							{
								key: 'risk',
								label: t('settings.plugins.col.risk', 'Risk'),
								render: (row) => <Badge tone={riskTone(row.riskLevel)}>{row.riskLevel}</Badge>,
							},
							{
								key: 'status',
								label: statusLabel,
								render: (row) => <Badge tone={pluginTone(row.status)}>{row.status}</Badge>,
							},
							{
								key: 'reason',
								label: t('settings.plugins.col.reason', 'Reason'),
								render: (row) => row.reason,
							},
						]}
					/>
				) : null}
			</Dialog>
		</section>
	);
}
