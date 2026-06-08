import type { ModelGatewayRoutingProfile } from '../../api/types';
import { DataTable, EmptyState } from '../../components/primitives';
import { PanelShell } from './PanelShell';
import { boolLabel, text } from './utils';

export function RoutingProfilesPanel({ routingProfiles }: { routingProfiles: ModelGatewayRoutingProfile[] }) {
	return (
		<PanelShell title="Routing Profiles">
			<DataTable rows={routingProfiles} empty={<EmptyState title="No routing profiles" body="Routing modes seed during startup." />} columns={[
				{ key: 'name', label: 'Profile', render: (row) => <span className="mono">{text(row.name)}</span> },
				{ key: 'mode', label: 'Mode', render: (row) => text(row.mode) },
				{ key: 'objective', label: 'Objective', render: (row) => text(row.objective) },
				{ key: 'rules', label: 'Rules', render: (row) => <span className="mono">{JSON.stringify(row.rules ?? {})}</span> },
				{ key: 'enabled', label: 'Enabled', render: (row) => boolLabel(row.enabled) },
			]} />
		</PanelShell>
	);
}
