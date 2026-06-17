/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import type { ModelGatewayRoutingDecision } from '../../api/types';
import { DataTable, EmptyState } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
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
	const { t } = useI18n();
	return (
		<PanelShell title={t('ui.static.routing.decisions.de5835f8', 'Routing Decisions')}>
			<div className="field">
				<label htmlFor="routing-decision-filter">{t('ui.static.routing.decision.filter.21e88e2e', 'Routing decision filter')}</label>
				<input id="routing-decision-filter" className="input" value={filter} onChange={(event) => onFilterChange(event.target.value)} placeholder={t('ui.static.filter.role.task.mode.provider.model.or.reason.3e68ef6b', 'Filter role, task, mode, provider, model or reason')} />
			</div>
			<DataTable rows={rows} empty={<EmptyState title={t('ui.static.no.routing.decisions.e50fdc8d', 'No routing decisions')} body={t('ui.static.route.preview.and.execution.decisions.are.audited.here.aba81602', 'Route preview and execution decisions are audited here.')} />} columns={[
				{ key: 'time', label: t('ui.static.time.6c82e6dd', 'Time'), render: (row) => text(row.createdAt) },
				{ key: 'role', label: t('ui.static.role.c3f104d1', 'Role'), render: (row) => text(row.role) },
				{ key: 'task', label: t('ui.static.task.type.9ca3a8e9', 'Task type'), render: (row) => text(row.taskType) },
				{ key: 'mode', label: t('ui.static.mode.a7b93d21', 'Mode'), render: (row) => text(row.mode) },
				{ key: 'provider', label: t('ui.static.selected.provider.9af0120e', 'Selected provider'), render: (row) => text(row.selectedProvider) },
				{ key: 'model', label: t('ui.static.selected.model.5d227e1f', 'Selected model'), render: (row) => text(row.selectedModel) },
				{ key: 'runtime', label: t('ui.static.selected.runtime.0d70da8f', 'Selected runtime'), render: (row) => text(row.selectedRuntime) },
				{ key: 'effort', label: t('ui.static.effort.8c974bc6', 'Effort'), render: (row) => text(row.selectedEffort) },
				{ key: 'cost', label: t('ui.static.estimated.cost.516cbee2', 'Estimated cost'), render: (row) => money(row.estimatedCostUsd, t('app.runtime.card.unknown', 'unknown')) },
				{ key: 'reason', label: t('ui.static.reason.f219cc06', 'Reason'), render: (row) => text(row.decisionReason) },
			]} />
		</PanelShell>
	);
}
