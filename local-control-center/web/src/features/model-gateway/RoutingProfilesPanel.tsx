/**
 * Tabla de perfiles de ruteo del Model Gateway: los modos que definen el objetivo de selección (costo, valor, rendimiento).
 * Sub-dominio de estrategia de ruteo: por perfil muestra modo, objetivo, reglas y si está habilitado.
 * Solo lectura: los modos se siembran al arranque.
 * @author Rodrigo Mason
 */
import type { ModelGatewayRoutingProfile } from '../../api/types';
import { DataTable, EmptyState } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { PanelShell } from './PanelShell';
import { boolLabel, text } from './utils';

export function RoutingProfilesPanel({
	routingProfiles,
}: {
	routingProfiles: ModelGatewayRoutingProfile[];
}) {
	const { t } = useI18n();
	return (
		<PanelShell title={t('ui.static.routing.profiles.03ac2a79', 'Routing Profiles')}>
			<DataTable
				rows={routingProfiles}
				empty={
					<EmptyState
						title={t('ui.static.no.routing.profiles.065ee125', 'No routing profiles')}
						body={t(
							'ui.static.routing.modes.seed.during.startup.3759adcf',
							'Routing modes seed during startup.',
						)}
					/>
				}
				columns={[
					{
						key: 'name',
						label: t('ui.static.profile.ff4fc027', 'Profile'),
						render: (row) => <span className="mono">{text(row.name)}</span>,
					},
					{
						key: 'mode',
						label: t('ui.static.mode.a7b93d21', 'Mode'),
						render: (row) => text(row.mode),
					},
					{
						key: 'objective',
						label: t('ui.static.objective.50c8920b', 'Objective'),
						render: (row) => text(row.objective),
					},
					{
						key: 'rules',
						label: t('ui.static.rules.bb11a8e3', 'Rules'),
						render: (row) => <span className="mono">{JSON.stringify(row.rules ?? {})}</span>,
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
