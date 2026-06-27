/**
 * Panel de solo lectura que lista las reglas de presupuesto del Model Gateway.
 * Cada fila resume el alcance (tipo:id), los topes de costo/tokens, el periodo y la acción al excederse.
 * @author Rodrigo Mason
 */
import type { ModelGatewayBudgetRule } from '../../api/types';
import { DataTable, EmptyState } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { PanelShell } from './PanelShell';
import { boolLabel, money, text } from './utils';

export function BudgetsPanel({ budgetRules }: { budgetRules: ModelGatewayBudgetRule[] }) {
	const { t } = useI18n();
	return (
		<PanelShell title={t('ui.static.budgets.c13c8242', 'Budgets')}>
			<DataTable
				rows={budgetRules}
				empty={
					<EmptyState
						title={t('ui.static.no.budget.rules.b121a748', 'No budget rules')}
						body={t(
							'ui.static.budget.rules.can.be.added.for.global.role.provider.project.a.065c4ad7',
							'Budget rules can be added for global, role, provider, project and workflow scopes.',
						)}
					/>
				}
				columns={[
					{
						key: 'scope',
						label: t('ui.static.scope.4651a34e', 'Scope'),
						render: (row) => `${text(row.scopeType)}:${text(row.scopeId, '*')}`,
					},
					{
						key: 'cost',
						label: t('ui.static.max.cost.cd7a5d86', 'Max cost'),
						render: (row) => money(row.maxCostUsd, t('app.runtime.card.unknown', 'unknown')),
					},
					{
						key: 'tokens',
						label: t('ui.static.max.tokens.4bd246e3', 'Max tokens'),
						render: (row) => text(row.maxTokens, t('app.runtime.card.none', 'none')),
					},
					{
						key: 'period',
						label: t('ui.static.period.170a28a9', 'Period'),
						render: (row) => text(row.period),
					},
					{
						key: 'action',
						label: t('ui.static.action.on.exceed.dab9c5d8', 'Action on exceed'),
						render: (row) => text(row.actionOnExceed),
					},
					{
						key: 'enabled',
						label: t('ui.static.enabled.df174a3f', 'Enabled'),
						render: (row) => boolLabel(row.enabled),
					},
				]}
			/>
		</PanelShell>
	);
}
