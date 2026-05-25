import type { Dictionary } from '../../api/types';
import { Badge, DataTable, EmptyState } from '../../components/primitives';
import { PanelShell } from './PanelShell';
import { boolLabel, listLabel, text } from './utils';

export function ModelCatalogPanel({ models }: { models: Dictionary[] }) {
	return (
		<PanelShell title="Model Catalog">
			<DataTable rows={models} empty={<EmptyState title="No models" body="Model catalog entries appear after seeds or provider discovery." />} columns={[
				{ key: 'provider', label: 'Provider', render: (row) => <span className="mono">{text(row.providerId)}</span> },
				{ key: 'model', label: 'Model', render: (row) => <span className="mono">{text(row.model)}</span> },
				{ key: 'family', label: 'Family', render: (row) => text(row.modelFamily) },
				{ key: 'context', label: 'Context', render: (row) => text(row.contextWindow) },
				{ key: 'tools', label: 'Tools', render: (row) => boolLabel(row.supportsTools) },
				{ key: 'json', label: 'JSON', render: (row) => boolLabel(row.supportsJson) },
				{ key: 'vision', label: 'Vision', render: (row) => boolLabel(row.supportsVision) },
				{ key: 'reasoning', label: 'Reasoning', render: (row) => boolLabel(row.supportsReasoning) },
				{ key: 'effort', label: 'Effort levels', render: (row) => listLabel(row.effortLevels) },
				{ key: 'input', label: 'Input price', render: (row) => text(row.inputPricePerMtok, 'unknown') },
				{ key: 'output', label: 'Output price', render: (row) => text(row.outputPricePerMtok, 'unknown') },
				{ key: 'reasoningPrice', label: 'Reasoning price', render: (row) => text(row.reasoningPricePerMtok, 'unknown') },
				{ key: 'free', label: 'Free tier', render: (row) => boolLabel(row.freeTier) },
				{ key: 'enabled', label: 'Enabled', render: (row) => <Badge tone={row.enabled ? 'ok' : 'warn'}>{boolLabel(row.enabled)}</Badge> },
				{ key: 'source', label: 'Source', render: (row) => text(row.source) },
			]} />
		</PanelShell>
	);
}
