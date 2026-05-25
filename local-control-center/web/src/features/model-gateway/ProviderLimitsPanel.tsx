import type { Dictionary } from '../../api/types';
import { DataTable, EmptyState } from '../../components/primitives';
import { PanelShell } from './PanelShell';
import { money, text } from './utils';

export function ProviderLimitsPanel({ providerLimits }: { providerLimits: Dictionary[] }) {
	return (
		<PanelShell title="Provider Limits">
			<DataTable rows={providerLimits} empty={<EmptyState title="No provider limits" body="Provider limit records appear after seeds or rate-limit events." />} columns={[
				{ key: 'provider', label: 'Provider', render: (row) => text(row.providerId) },
				{ key: 'model', label: 'Model', render: (row) => text(row.model) },
				{ key: 'rpm', label: 'RPM', render: (row) => text(row.rpm) },
				{ key: 'tpm', label: 'TPM', render: (row) => text(row.tpm) },
				{ key: 'dailyRequests', label: 'Daily requests', render: (row) => text(row.dailyRequests) },
				{ key: 'dailyTokens', label: 'Daily tokens', render: (row) => text(row.dailyTokens) },
				{ key: 'monthlyBudget', label: 'Monthly budget', render: (row) => money(row.monthlyBudgetUsd) },
				{ key: 'cooldown', label: 'Cooldown', render: (row) => text(row.cooldownUntil) },
				{ key: 'last429', label: 'Last 429', render: (row) => text(row.last429At) },
				{ key: 'strategy', label: 'Unknown limit strategy', render: (row) => text(row.unknownLimitStrategy) },
			]} />
		</PanelShell>
	);
}
