/**
 * Panel de solo lectura que tabula el catálogo de modelos conocidos por el Model Gateway.
 * Por modelo expone proveedor, familia, ventana de contexto, capacidades (tools/JSON/visión/razonamiento),
 * precios por millón de tokens y si está habilitado, con su origen (seed o descubrimiento de proveedor).
 * @author Rodrigo Mason
 */
import type { ModelGatewayModel } from '../../api/types';
import { Badge, DataTable, EmptyState } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { PanelShell } from './PanelShell';
import { boolLabel, listLabel, text } from './utils';

export function ModelCatalogPanel({ models }: { models: ModelGatewayModel[] }) {
	const { t } = useI18n();
	return (
		<PanelShell title={t('ui.static.model.catalog.660f1b98', 'Model Catalog')}>
			<DataTable
				rows={models}
				empty={
					<EmptyState
						title={t('ui.static.no.models.7c30bd0a', 'No models')}
						body={t(
							'ui.static.model.catalog.entries.appear.after.seeds.or.provider.discove.babcd23e',
							'Model catalog entries appear after seeds or provider discovery.',
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
						key: 'model',
						label: t('ui.static.model.68c2cc7f', 'Model'),
						render: (row) => <span className="mono">{text(row.model)}</span>,
					},
					{
						key: 'family',
						label: t('ui.static.family.4efb6cb7', 'Family'),
						render: (row) => text(row.modelFamily),
					},
					{
						key: 'context',
						label: t('ui.static.context.cc11b3a2', 'Context'),
						render: (row) => text(row.contextWindow),
					},
					{
						key: 'tools',
						label: t('ui.static.tools.4fa8cc86', 'Tools'),
						render: (row) => boolLabel(row.supportsTools),
					},
					{ key: 'json', label: 'JSON', render: (row) => boolLabel(row.supportsJson) },
					{
						key: 'vision',
						label: t('ui.static.vision.40b0906c', 'Vision'),
						render: (row) => boolLabel(row.supportsVision),
					},
					{
						key: 'reasoning',
						label: t('ui.static.reasoning.e272c597', 'Reasoning'),
						render: (row) => boolLabel(row.supportsReasoning),
					},
					{
						key: 'effort',
						label: t('ui.static.effort.levels.00012f20', 'Effort levels'),
						render: (row) => listLabel(row.effortLevels, t('app.runtime.card.none', 'none')),
					},
					{
						key: 'input',
						label: t('ui.static.input.price.c4751daf', 'Input price'),
						render: (row) => text(row.inputPricePerMtok, t('app.runtime.card.unknown', 'unknown')),
					},
					{
						key: 'output',
						label: t('ui.static.output.price.a664728b', 'Output price'),
						render: (row) => text(row.outputPricePerMtok, t('app.runtime.card.unknown', 'unknown')),
					},
					{
						key: 'reasoningPrice',
						label: t('ui.static.reasoning.price.7a0fa004', 'Reasoning price'),
						render: (row) =>
							text(row.reasoningPricePerMtok, t('app.runtime.card.unknown', 'unknown')),
					},
					{
						key: 'free',
						label: t('ui.static.free.tier.5305bc7b', 'Free tier'),
						render: (row) => boolLabel(row.freeTier),
					},
					{
						key: 'enabled',
						label: t('ui.static.enabled.df174a3f', 'Enabled'),
						render: (row) => (
							<Badge tone={row.enabled ? 'ok' : 'warn'}>{boolLabel(row.enabled)}</Badge>
						),
					},
					{
						key: 'source',
						label: t('app.workspace.summary.source', 'Source'),
						render: (row) => text(row.source),
					},
				]}
			/>
		</PanelShell>
	);
}
