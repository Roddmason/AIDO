/**
 * Integrations console: registers local stdio MCP servers and lists registered
 * servers plus IDE connection events. Validates the server id and rejects shell
 * operators in the command; registration only stores argv-style config — execution
 * still passes through broker, policy and sandbox.
 * @author Rodrigo Mason
 */
import { useEffect, useState } from 'react';
import { registerMcpServer } from '../../api/client';
import type { McpTransport, Overview } from '../../api/types';
import {
	StatusChip as Badge,
	DataTable,
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
