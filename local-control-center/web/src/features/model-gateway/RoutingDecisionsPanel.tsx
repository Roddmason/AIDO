/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import type { ModelGatewayRoutingDecision } from '../../api/types';
import { DataTable, EmptyState } from '../../components/primitives';
import { PanelShell } from './PanelShell';
import { money, text } from './utils';

export function RoutingDecisionsPanel({
	rows,
	filter,
	onFilterChange,
}: {
	rows: ModelGatewayRoutingDecision[];
	filter: string;
	onFilterChange: (value: string) => void;
}) {
	return (
		<PanelShell title="Routing Decisions">
			<div className="field">
				<label htmlFor="routing-decision-filter">Routing decision filter</label>
				<input id="routing-decision-filter" className="input" value={filter} onChange={(event) => onFilterChange(event.target.value)} placeholder="Filter role, task, mode, provider, model or reason" />
			</div>
			<DataTable rows={rows} empty={<EmptyState title="No routing decisions" body="Route preview and execution decisions are audited here." />} columns={[
				{ key: 'time', label: 'Time', render: (row) => text(row.createdAt) },
				{ key: 'role', label: 'Role', render: (row) => text(row.role) },
				{ key: 'task', label: 'Task type', render: (row) => text(row.taskType) },
				{ key: 'mode', label: 'Mode', render: (row) => text(row.mode) },
				{ key: 'provider', label: 'Selected provider', render: (row) => text(row.selectedProvider) },
				{ key: 'model', label: 'Selected model', render: (row) => text(row.selectedModel) },
				{ key: 'runtime', label: 'Selected runtime', render: (row) => text(row.selectedRuntime) },
				{ key: 'effort', label: 'Effort', render: (row) => text(row.selectedEffort) },
				{ key: 'cost', label: 'Estimated cost', render: (row) => money(row.estimatedCostUsd) },
				{ key: 'reason', label: 'Reason', render: (row) => text(row.decisionReason) },
			]} />
		</PanelShell>
	);
}
