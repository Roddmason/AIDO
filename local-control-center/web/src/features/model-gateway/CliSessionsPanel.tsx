/**
 * Panel de runtimes CLI y sus sesiones de ejecución en el Model Gateway, con la actividad en vivo.
 * Muestra el estado de cada runtime CLI y el detalle de cada sesión (workspace, agente, comando,
 * artefactos), redactando ejecutables, comandos y errores que puedan contener secretos; al elegir una
 * sesión despliega su bitácora de eventos en streaming (actividad real, no spinner indefinido).
 * @author Rodrigo Mason
 */
import { useState } from 'react';

import type { ModelGatewayCliRuntime, ModelGatewayCliSession } from '../../api/types';
import { StatusChip as Badge, DataTable, EmptyState } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { CliSessionActivity } from './CliSessionActivity';
import { PanelShell } from './PanelShell';
import { listLabel, SecretSafeValue, text } from './utils';

export function CliSessionsPanel({
	cliRuntimes,
	cliSessions,
	token,
}: {
	cliRuntimes: ModelGatewayCliRuntime[];
	cliSessions: ModelGatewayCliSession[];
	token?: string;
}) {
	const { t } = useI18n();
	const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
	return (
		<PanelShell title={t('ui.static.cli.sessions.e179e262', 'CLI Sessions')}>
			<div className="grid two">
				<DataTable
					rows={cliRuntimes}
					empty={
						<EmptyState
							title={t('ui.static.no.cli.runtimes.867bdd5c', 'No CLI runtimes')}
							body={t(
								'ui.static.codex.claude.code.openhands.and.swe.agent.adapters.are.optio.6ae60fea',
								'Codex, Claude Code, OpenHands and SWE-agent adapters are optional.',
							)}
						/>
					}
					columns={[
						{
							key: 'runtime',
							label: t('ui.static.runtime.c4740e4c', 'Runtime'),
							render: (row) => text(row.runtime),
						},
						{
							key: 'status',
							label: t('ui.static.status.bae7d5be', 'Status'),
							render: (row) => (
								<Badge tone={toneForStatus(text(row.status))}>{text(row.status)}</Badge>
							),
						},
						{
							key: 'executable',
							label: t('ui.static.executable.6f703eda', 'Executable'),
							render: (row) => <SecretSafeValue value={row.executable} />,
						},
						{
							key: 'message',
							label: t('ui.static.message.68f4145f', 'Message'),
							render: (row) => text(row.message),
						},
					]}
				/>
				<DataTable
					rows={cliSessions}
					empty={
						<EmptyState
							title={t('ui.static.no.cli.sessions.4ff7b528', 'No CLI sessions')}
							body={t(
								'ui.static.cli.session.records.appear.only.after.policy.approved.runtim.61626bfe',
								'CLI session records appear only after policy-approved runtime execution.',
							)}
						/>
					}
					columns={[
						{
							key: 'runtime',
							label: t('ui.static.runtime.c4740e4c', 'Runtime'),
							render: (row) => text(row.runtime),
						},
						{
							key: 'workspace',
							label: t('ui.static.workspace.4ca0a75c', 'Workspace'),
							render: (row) => text(row.workspaceId),
						},
						{
							key: 'agent',
							label: t('ui.static.agent.5ce2e6f4', 'Agent'),
							render: (row) => text(row.agentId),
						},
						{
							key: 'workflow',
							label: t('ui.static.workflow.d7a48414', 'Workflow'),
							render: (row) => text(row.workflowRunId),
						},
						{
							key: 'status',
							label: t('ui.static.status.bae7d5be', 'Status'),
							render: (row) => text(row.status),
						},
						{
							key: 'command',
							label: t('ui.static.command.summary.22289d0c', 'Command summary'),
							render: (row) => (
								<SecretSafeValue
									value={Array.isArray(row.command) ? row.command.join(' ') : row.command}
								/>
							),
						},
						{
							key: 'started',
							label: t('ui.static.started.faa9e7e7', 'Started'),
							render: (row) => text(row.startedAt),
						},
						{
							key: 'finished',
							label: t('app.nav.finished', 'Finished'),
							render: (row) => text(row.finishedAt),
						},
						{
							key: 'usage',
							label: t('ui.static.usage.0bb18642', 'Usage'),
							render: (row) => text(row.usageLedgerId),
						},
						{
							key: 'artifacts',
							label: t('ui.static.artifacts.a5b79f59', 'Artifacts'),
							render: (row) =>
								listLabel(
									[row.stdoutArtifactId, row.stderrArtifactId, row.logsArtifactId].filter(Boolean),
								),
						},
						{
							key: 'error',
							label: t('ui.static.error.7f2f6a15', 'Error'),
							render: (row) => <SecretSafeValue value={row.error} />,
						},
						{
							key: 'activity',
							label: t('app.cliSession.activity', 'Activity'),
							render: (row) => (
								<button
									className="button"
									type="button"
									onClick={() => setSelectedSessionId(row.id)}
								>
									{t('app.cliSession.view', 'View activity')}
								</button>
							),
						},
					]}
				/>
			</div>
			{selectedSessionId ? (
				<CliSessionActivity sessionId={selectedSessionId} token={token} />
			) : null}
		</PanelShell>
	);
}
