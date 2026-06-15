/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import type { ModelGatewayProviderAccount } from '../../api/types';
import { Badge, DataTable, EmptyState } from '../../components/primitives';
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
	return (
		<PanelShell title="Provider Accounts">
			<DataTable rows={providers} empty={<EmptyState title="No provider accounts" body="Provider accounts seed during startup." />} columns={[
				{ key: 'provider', label: 'Provider', render: (row) => <span className="mono">{text(row.providerId)}</span> },
				{ key: 'type', label: 'Type', render: (row) => text(row.providerType) },
				{ key: 'format', label: 'API format', render: (row) => text(row.apiFormat) },
				{ key: 'base', label: 'Base URL', render: (row) => <SecretSafeValue value={row.baseUrl} /> },
				{ key: 'credential', label: 'Credential', render: (row) => <div className="stack"><Badge tone={row.credentialStatus === 'configured' ? 'ok' : 'warn'}>{text(row.credentialStatus)}</Badge><SecretSafeValue value={row.credentialRef} /></div> },
				{ key: 'enabled', label: 'Enabled', render: (row) => <Badge tone={row.enabled ? 'ok' : 'warn'}>{boolLabel(row.enabled)}</Badge> },
				{ key: 'quota', label: 'Quota mode', render: (row) => text(row.quotaMode) },
				{ key: 'health', label: 'Health', render: (row) => <Badge tone={toneForStatus(text(row.healthStatus))}>{text(row.healthStatus)}</Badge> },
				{ key: 'last', label: 'Last check', render: (row) => text(row.lastHealthCheckAt) },
				{ key: 'error', label: 'Last error', render: (row) => <SecretSafeValue value={row.lastError} /> },
				{ key: 'metadata', label: 'Metadata', render: (row) => <SecretSafeValue value={JSON.stringify(row.metadata ?? {})} /> },
				{ key: 'cost', label: 'Cost today', render: () => 'cost unavailable' },
				{ key: 'actions', label: 'Actions', render: (row) => {
					const providerId = text(row.providerId, '');
					return (
						<div className="inline">
							<button className="button" type="button" disabled={busyAction === `${providerId}:toggle`} onClick={() => onProviderAction(providerId, 'toggle')}>{row.enabled ? 'Disable' : 'Enable'}</button>
							<button className="button" type="button" disabled={busyAction === `${providerId}:health`} onClick={() => onProviderAction(providerId, 'health')}>Health check</button>
							<button className="button" type="button" disabled={busyAction === `${providerId}:discover`} onClick={() => onProviderAction(providerId, 'discover')}>Discover models</button>
						</div>
					);
				} },
			]} />
		</PanelShell>
	);
}
