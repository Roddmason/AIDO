/**
 * Tabla de cuentas de proveedor del Model Gateway: muestra config, credencial, salud y cuota por proveedor.
 * Sub-dominio de inventario de proveedores; expone acciones por fila (habilitar, health check, descubrir modelos)
 * que delega al contenedor vía onProviderAction. Enmascara secretos con SecretSafeValue.
 * @author Rodrigo Mason
 */
import type { ModelGatewayProviderAccount } from '../../api/types';
import { StatusChip as Badge, DataTable, EmptyState } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { PanelShell } from './PanelShell';
import { boolLabel, SecretSafeValue, text } from './utils';

export function ProviderAccountsPanel({
	providers,
	busyAction,
	onProviderAction,
}: {
	providers: ModelGatewayProviderAccount[];
	busyAction: string;
	onProviderAction: (providerId: string, action: 'toggle' | 'health' | 'discover') => void;
}) {
	const { t } = useI18n();
	return (
		<PanelShell title={t('ui.static.provider.accounts.a0bdb58a', 'Provider Accounts')}>
			<DataTable
				rows={providers}
				empty={
					<EmptyState
						title={t('ui.static.no.provider.accounts.2aee0ea0', 'No provider accounts')}
						body={t(
							'ui.static.provider.accounts.seed.during.startup.ec276e37',
							'Provider accounts seed during startup.',
						)}
					/>
				}
				columns={[
					{
						key: 'provider',
						label: t('ui.static.provider.7ceee3f3', 'Provider'),
						render: (row) => <span className="mono">{text(row.providerId)}</span>,
					},
					{
						key: 'type',
						label: t('ui.static.type.3deb7456', 'Type'),
						render: (row) => text(row.providerType),
					},
					{
						key: 'format',
						label: t('ui.static.api.format.ba7636e4', 'API format'),
						render: (row) => text(row.apiFormat),
					},
					{
						key: 'base',
						label: t('ui.static.base.url.1dbd61f5', 'Base URL'),
						render: (row) => <SecretSafeValue value={row.baseUrl} />,
					},
					{
						key: 'credential',
						label: t('ui.static.credential.8bede3ea', 'Credential'),
						render: (row) => (
							<div className="stack">
								<Badge tone={row.credentialStatus === 'configured' ? 'ok' : 'warn'}>
									{text(row.credentialStatus)}
								</Badge>
								<SecretSafeValue value={row.credentialRef} />
							</div>
						),
					},
					{
						key: 'enabled',
						label: t('ui.static.enabled.df174a3f', 'Enabled'),
						render: (row) => (
							<Badge tone={row.enabled ? 'ok' : 'warn'}>{boolLabel(row.enabled)}</Badge>
						),
					},
					{
						key: 'quota',
						label: t('ui.static.quota.mode.84d5c054', 'Quota mode'),
						render: (row) => text(row.quotaMode),
					},
					{
						key: 'health',
						label: t('ui.static.health.3703cd21', 'Health'),
						render: (row) => (
							<Badge tone={toneForStatus(text(row.healthStatus))}>{text(row.healthStatus)}</Badge>
						),
					},
					{
						key: 'last',
						label: t('ui.static.last.check.f4b50527', 'Last check'),
						render: (row) => text(row.lastHealthCheckAt),
					},
					{
						key: 'error',
						label: t('ui.static.last.error.5e4df866', 'Last error'),
						render: (row) => <SecretSafeValue value={row.lastError} />,
					},
					{
						key: 'metadata',
						label: t('ui.static.metadata.251edc0e', 'Metadata'),
						render: (row) => <SecretSafeValue value={JSON.stringify(row.metadata ?? {})} />,
					},
					{
						key: 'cost',
						label: t('ui.static.cost.today.ef1eaeca', 'Cost today'),
						render: () => t('app.global.costUnavailable', 'cost unavailable'),
					},
					{
						key: 'actions',
						label: t('ui.static.actions.c3cd636a', 'Actions'),
						render: (row) => {
							const providerId = text(row.providerId, '');
							return (
								<div className="inline">
									<button
										className="button"
										type="button"
										disabled={busyAction === `${providerId}:toggle`}
										onClick={() => onProviderAction(providerId, 'toggle')}
									>
										{row.enabled
											? t('app.modelGateway.action.disable', 'Disable')
											: t('app.modelGateway.action.enable', 'Enable')}
									</button>
									<button
										className="button"
										type="button"
										disabled={busyAction === `${providerId}:health`}
										onClick={() => onProviderAction(providerId, 'health')}
									>
										{t('ui.static.health.check.6afd56ff', 'Health check')}
									</button>
									<button
										className="button"
										type="button"
										disabled={busyAction === `${providerId}:discover`}
										onClick={() => onProviderAction(providerId, 'discover')}
									>
										{t('ui.static.discover.models.6a62bedb', 'Discover models')}
									</button>
								</div>
							);
						},
					},
				]}
			/>
		</PanelShell>
	);
}
