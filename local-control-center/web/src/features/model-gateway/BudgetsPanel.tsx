import type { ModelGatewayBudgetRule } from '../../api/types';
import { DataTable, EmptyState } from '../../components/primitives';
import { PanelShell } from './PanelShell';
import { boolLabel, money, text } from './utils';

export function BudgetsPanel({ budgetRules }: { budgetRules: ModelGatewayBudgetRule[] }) {
	return (
		<PanelShell title="Budgets">
			<DataTable rows={budgetRules} empty={<EmptyState title="No budget rules" body="Budget rules can be added for global, role, provider, project and workflow scopes." />} columns={[
				{ key: 'scope', label: 'Scope', render: (row) => `${text(row.scopeType)}:${text(row.scopeId, '*')}` },
				{ key: 'cost', label: 'Max cost', render: (row) => money(row.maxCostUsd) },
				{ key: 'tokens', label: 'Max tokens', render: (row) => text(row.maxTokens, 'none') },
				{ key: 'period', label: 'Period', render: (row) => text(row.period) },
				{ key: 'action', label: 'Action on exceed', render: (row) => text(row.actionOnExceed) },
				{ key: 'enabled', label: 'Enabled', render: (row) => boolLabel(row.enabled) },
			]} />
		</PanelShell>
	);
}
