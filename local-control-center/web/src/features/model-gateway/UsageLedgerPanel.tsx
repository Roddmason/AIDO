/**
 * Libro de uso del Model Gateway: historial de consumo real reportado por cada llamada a proveedor/runtime.
 * Sub-dominio de contabilidad de uso: por entrada muestra desglose de tokens, costo estimado vs real, latencia
 * y la procedencia/estado del dato (reportado o estimado). El filtrado de texto es controlado por el padre.
 * @author Rodrigo Mason
 */
import type { ModelGatewayUsage } from '../../api/types';
import { DataTable, EmptyState } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { PanelShell } from './PanelShell';
import { money, text } from './utils';

export function UsageLedgerPanel({
	rows,
	filter,
	onFilterChange,
}: {
	rows: ModelGatewayUsage[];
	filter: string;
	onFilterChange: (value: string) => void;
}) {
	const { t } = useI18n();
	return (
		<PanelShell title={t('ui.static.usage.ledger.bc792665', 'Usage history')}>
			<div className="field">
				<label htmlFor="usage-ledger-filter">
					{t('ui.static.usage.ledger.filter.c94a6388', 'Usage history filter')}
				</label>
				<input
					id="usage-ledger-filter"
					className="input"
					value={filter}
					onChange={(event) => onFilterChange(event.target.value)}
					placeholder={t(
						'ui.static.filter.provider.model.role.runtime.workflow.or.agent.b9ddad3a',
						'Filter provider, model, role, runtime, workflow or agent',
					)}
				/>
			</div>
			<DataTable
				rows={rows}
				empty={
					<EmptyState
						title={t('ui.static.no.usage.ledger.entries.0b082a29', 'No usage history entries')}
						body={t(
							'ui.static.real.provider.and.runtime.calls.record.reported.usage.here.cec31f01',
							'Real provider and runtime calls record reported usage here.',
						)}
					/>
				}
				columns={[
					{
						key: 'time',
						label: t('ui.static.timestamp.19eabc96', 'Timestamp'),
						render: (row) => text(row.createdAt),
					},
					{
						key: 'provider',
						label: t('ui.static.provider.7ceee3f3', 'Provider'),
						render: (row) => text(row.providerId),
					},
					{
						key: 'model',
						label: t('ui.static.model.68c2cc7f', 'Model'),
						render: (row) => text(row.model),
					},
					{
						key: 'runtime',
						label: t('ui.static.runtime.c4740e4c', 'Runtime'),
						render: (row) => text(row.runtimeType),
					},
					{
						key: 'role',
						label: t('ui.static.role.c3f104d1', 'Role'),
						render: (row) => text(row.role),
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
						key: 'task',
						label: t('ui.static.task.7bb0ddf9', 'Task'),
						render: (row) => text(row.taskId),
					},
					{
						key: 'input',
						label: t('ui.static.input.tokens.92f7d222', 'Input tokens'),
						render: (row) => text(row.inputTokens, '0'),
					},
					{
						key: 'cached',
						label: t('ui.static.cached.input.36e191c9', 'Cached input'),
						render: (row) => text(row.cachedInputTokens, '0'),
					},
					{
						key: 'output',
						label: t('ui.static.output.tokens.b879f52d', 'Output tokens'),
						render: (row) => text(row.outputTokens, '0'),
					},
					{
						key: 'reasoning',
						label: t('ui.static.reasoning.tokens.6284e599', 'Reasoning tokens'),
						render: (row) => text(row.reasoningTokens, '0'),
					},
					{
						key: 'tool',
						label: t('ui.static.tool.tokens.472fefe0', 'Tool tokens'),
						render: (row) => text(row.toolTokens, '0'),
					},
					{
						key: 'total',
						label: t('ui.static.total.tokens.e6dad16e', 'Total tokens'),
						render: (row) => text(row.totalTokens, '0'),
					},
					{
						key: 'est',
						label: t('ui.static.estimated.cost.516cbee2', 'Estimated cost'),
						render: (row) => money(row.estimatedCostUsd, t('app.runtime.card.unknown', 'unknown')),
					},
					{
						key: 'actual',
						label: t('ui.static.actual.cost.edf3964d', 'Actual cost'),
						render: (row) => money(row.actualCostUsd, t('app.runtime.card.unknown', 'unknown')),
					},
					{
						key: 'latency',
						label: t('ui.static.latency.3e399725', 'Latency'),
						render: (row) => text(row.latencyMs),
					},
					{
						key: 'source',
						label: t('ui.static.usage.source.16f36c88', 'Usage source'),
						render: (row) => text(row.usageSource, t('app.runtime.card.unknown', 'unknown')),
					},
					{
						key: 'tokenStatus',
						label: t('ui.static.token.status.9716f498', 'Token status'),
						render: (row) => text(row.tokenStatus, t('app.runtime.card.unknown', 'unknown')),
					},
					{
						key: 'costStatus',
						label: t('ui.static.cost.status.0f8a4f0a', 'Cost status'),
						render: (row) => text(row.costStatus, t('app.runtime.card.unknown', 'unknown')),
					},
				]}
			/>
		</PanelShell>
	);
}
