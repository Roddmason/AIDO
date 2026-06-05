import type { Dictionary } from '../../api/types';
import { DataTable, EmptyState } from '../../components/primitives';
import { PanelShell } from './PanelShell';
import { money, text } from './utils';

export function UsageLedgerPanel({
	rows,
	filter,
	onFilterChange,
}: {
	rows: Dictionary[];
	filter: string;
	onFilterChange: (value: string) => void;
}) {
	return (
		<PanelShell title="Usage Ledger">
			<div className="field">
				<label htmlFor="usage-ledger-filter">Usage ledger filter</label>
				<input id="usage-ledger-filter" className="input" value={filter} onChange={(event) => onFilterChange(event.target.value)} placeholder="Filter provider, model, role, runtime, workflow or agent" />
			</div>
			<DataTable rows={rows} empty={<EmptyState title="No usage ledger entries" body="Real provider and runtime calls record reported usage here." />} columns={[
				{ key: 'time', label: 'Timestamp', render: (row) => text(row.createdAt) },
				{ key: 'provider', label: 'Provider', render: (row) => text(row.providerId) },
				{ key: 'model', label: 'Model', render: (row) => text(row.model) },
				{ key: 'runtime', label: 'Runtime', render: (row) => text(row.runtimeType) },
				{ key: 'role', label: 'Role', render: (row) => text(row.role) },
				{ key: 'agent', label: 'Agent', render: (row) => text(row.agentId) },
				{ key: 'workflow', label: 'Workflow', render: (row) => text(row.workflowRunId) },
				{ key: 'task', label: 'Task', render: (row) => text(row.taskId) },
				{ key: 'input', label: 'Input tokens', render: (row) => text(row.inputTokens, '0') },
				{ key: 'cached', label: 'Cached input', render: (row) => text(row.cachedInputTokens, '0') },
				{ key: 'output', label: 'Output tokens', render: (row) => text(row.outputTokens, '0') },
				{ key: 'reasoning', label: 'Reasoning tokens', render: (row) => text(row.reasoningTokens, '0') },
				{ key: 'tool', label: 'Tool tokens', render: (row) => text(row.toolTokens, '0') },
				{ key: 'total', label: 'Total tokens', render: (row) => text(row.totalTokens, '0') },
				{ key: 'est', label: 'Estimated cost', render: (row) => money(row.estimatedCostUsd) },
				{ key: 'actual', label: 'Actual cost', render: (row) => row.actualCostUsd === null || row.actualCostUsd === undefined ? 'unknown' : money(row.actualCostUsd) },
				{ key: 'latency', label: 'Latency', render: (row) => text(row.latencyMs) },
				{ key: 'source', label: 'Usage source', render: (row) => text(row.usageSource, 'unknown') },
				{ key: 'tokenStatus', label: 'Token status', render: (row) => text(row.tokenStatus, 'unknown') },
				{ key: 'costStatus', label: 'Cost status', render: (row) => text(row.costStatus, 'unknown') },
			]} />
		</PanelShell>
	);
}
