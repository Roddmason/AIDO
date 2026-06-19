/**
 * Tabla de límites de proveedor del Model Gateway: RPM/TPM, cupos diarios, presupuesto mensual y cooldowns.
 * Sub-dominio de rate-limiting/cuota por proveedor-modelo; registra el último 429 y la estrategia ante límites
 * desconocidos. Solo lectura: las filas aparecen tras seeds o eventos de rate-limit.
 */
import type { ModelGatewayProviderLimit } from '../../api/types';
import { DataTable, EmptyState } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { PanelShell } from './PanelShell';
import { money, text } from './utils';

export function ProviderLimitsPanel({
	providerLimits,
}: {
	providerLimits: ModelGatewayProviderLimit[];
}) {
	const { t } = useI18n();
	return (
		<PanelShell title={t('ui.static.provider.limits.7533783c', 'Provider Limits')}>
			<DataTable
				rows={providerLimits}
				empty={
					<EmptyState
						title={t('ui.static.no.provider.limits.b14ad2ba', 'No provider limits')}
						body={t(
							'ui.static.provider.limit.records.appear.after.seeds.or.rate.limit.even.0c737940',
							'Provider limit records appear after seeds or rate-limit events.',
						)}
					/>
				}
				columns={[
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
					{ key: 'rpm', label: 'RPM', render: (row) => text(row.rpm) },
					{ key: 'tpm', label: 'TPM', render: (row) => text(row.tpm) },
					{
						key: 'dailyRequests',
						label: t('ui.static.daily.requests.aa9a0d5f', 'Daily requests'),
						render: (row) => text(row.dailyRequests),
					},
					{
						key: 'dailyTokens',
						label: t('ui.static.daily.tokens.4a4f692d', 'Daily tokens'),
						render: (row) => text(row.dailyTokens),
					},
					{
						key: 'monthlyBudget',
						label: t('ui.static.monthly.budget.f260ddaf', 'Monthly budget'),
						render: (row) => money(row.monthlyBudgetUsd, t('app.runtime.card.unknown', 'unknown')),
					},
					{
						key: 'cooldown',
						label: t('ui.static.cooldown.98fd67d9', 'Cooldown'),
						render: (row) => text(row.cooldownUntil),
					},
					{
						key: 'last429',
						label: t('ui.static.last.429.465b5393', 'Last 429'),
						render: (row) => text(row.last429At),
					},
					{
						key: 'strategy',
						label: t('ui.static.unknown.limit.strategy.c9e71d1c', 'Unknown limit strategy'),
						render: (row) => text(row.unknownLimitStrategy),
					},
				]}
			/>
		</PanelShell>
	);
}
