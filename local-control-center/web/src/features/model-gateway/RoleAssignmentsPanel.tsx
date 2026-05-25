import type { Dictionary } from '../../api/types';
import { DataTable, EmptyState } from '../../components/primitives';
import { PanelShell } from './PanelShell';
import { boolLabel, listLabel, money, text } from './utils';

export function RoleAssignmentsPanel({ rolePolicies }: { rolePolicies: Dictionary[] }) {
	return (
		<PanelShell title="Role Assignments">
			<DataTable rows={rolePolicies} empty={<EmptyState title="No role policies" body="Role routing policies seed during startup." />} columns={[
				{ key: 'role', label: 'Role', render: (row) => <span className="mono">{text(row.role)}</span> },
				{ key: 'mode', label: 'Mode', render: (row) => text(row.routingProfileId) },
				{ key: 'primary', label: 'Primary provider/model/runtime', render: (row) => listLabel(row.preferred) },
				{ key: 'fallbacks', label: 'Fallbacks', render: (row) => listLabel(row.fallback) },
				{ key: 'maxCost', label: 'Max cost/task', render: (row) => money(row.maxCostPerTaskUsd) },
				{ key: 'maxTokens', label: 'Max tokens/run', render: (row) => text(row.maxTokensPerRun) },
				{ key: 'remote', label: 'Remote allowed', render: (row) => boolLabel(row.allowRemote) },
				{ key: 'cli', label: 'CLI allowed', render: (row) => boolLabel(row.allowCli) },
				{ key: 'api', label: 'API allowed', render: (row) => boolLabel(row.allowApi) },
				{ key: 'thinking', label: 'Thinking/effort', render: (row) => row.requiresApprovalForReasoningMax ? 'max requires approval' : 'profile default' },
				{ key: 'approval', label: 'Approval threshold', render: (row) => money(row.requiresApprovalOverUsd ?? row.maxCostPerTaskUsd) },
			]} />
		</PanelShell>
	);
}
