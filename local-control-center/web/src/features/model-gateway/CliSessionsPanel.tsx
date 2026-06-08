import type { ModelGatewayCliRuntime, ModelGatewayCliSession } from '../../api/types';
import { Badge, DataTable, EmptyState } from '../../components/primitives';
import { toneForStatus } from '../../lib/format';
import { PanelShell } from './PanelShell';
import { listLabel, SecretSafeValue, text } from './utils';

export function CliSessionsPanel({
	cliRuntimes,
	cliSessions,
}: {
	cliRuntimes: ModelGatewayCliRuntime[];
	cliSessions: ModelGatewayCliSession[];
}) {
	return (
		<PanelShell title="CLI Sessions">
			<div className="grid two">
				<DataTable rows={cliRuntimes} empty={<EmptyState title="No CLI runtimes" body="Codex, Claude Code, OpenHands and SWE-agent adapters are optional." />} columns={[
					{ key: 'runtime', label: 'Runtime', render: (row) => text(row.runtime) },
					{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(text(row.status))}>{text(row.status)}</Badge> },
					{ key: 'executable', label: 'Executable', render: (row) => <SecretSafeValue value={row.executable} /> },
					{ key: 'message', label: 'Message', render: (row) => text(row.message) },
				]} />
				<DataTable rows={cliSessions} empty={<EmptyState title="No CLI sessions" body="CLI session records appear only after policy-approved runtime execution." />} columns={[
					{ key: 'runtime', label: 'Runtime', render: (row) => text(row.runtime) },
					{ key: 'workspace', label: 'Workspace', render: (row) => text(row.workspaceId) },
					{ key: 'agent', label: 'Agent', render: (row) => text(row.agentId) },
					{ key: 'workflow', label: 'Workflow', render: (row) => text(row.workflowRunId) },
					{ key: 'status', label: 'Status', render: (row) => text(row.status) },
					{ key: 'command', label: 'Command summary', render: (row) => <SecretSafeValue value={Array.isArray(row.command) ? row.command.join(' ') : row.command} /> },
					{ key: 'started', label: 'Started', render: (row) => text(row.startedAt) },
					{ key: 'finished', label: 'Finished', render: (row) => text(row.finishedAt) },
					{ key: 'usage', label: 'Usage', render: (row) => text(row.usageLedgerId) },
					{ key: 'artifacts', label: 'Artifacts', render: (row) => listLabel([row.stdoutArtifactId, row.stderrArtifactId, row.logsArtifactId].filter(Boolean)) },
					{ key: 'error', label: 'Error', render: (row) => <SecretSafeValue value={row.error} /> },
				]} />
			</div>
		</PanelShell>
	);
}
