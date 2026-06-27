/**
 * Tabla de políticas de ruteo por rol del Model Gateway: qué proveedor/modelo y límites aplican a cada rol de agente.
 * Sub-dominio de asignación rol→política: preferidos/fallbacks, topes de costo y tokens, transportes permitidos
 * (remote/CLI/API) y el trato del costo remoto desconocido. Solo lectura: las políticas se siembran al arranque.
 * @author Rodrigo Mason
 */
import type { ModelGatewayRolePolicy } from '../../api/types';
import { Badge, DataTable, EmptyState } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { PanelShell } from './PanelShell';
import { boolLabel, listLabel, money, text } from './utils';

type Translate = (key: string, fallback?: string) => string;

/** Traduce los tres estados de la política de costo remoto desconocido (rechazar/requiere aprobación/permitido) a un badge con tono. */
function unknownCostPolicyLabel(row: ModelGatewayRolePolicy, t: Translate) {
	if (!row.allowUnknownCost)
		return (
			<Badge tone="danger">
				{t('app.modelGateway.role.rejectUnknownCost', 'reject unknown remote cost')}
			</Badge>
		);
	if (row.requireApprovalForUnknownCost)
		return (
			<Badge tone="warn">{t('app.modelGateway.role.approvalRequired', 'approval required')}</Badge>
		);
	return <Badge tone="ok">{t('app.modelGateway.role.allowedByPolicy', 'allowed by policy')}</Badge>;
}

export function RoleAssignmentsPanel({ rolePolicies }: { rolePolicies: ModelGatewayRolePolicy[] }) {
	const { t } = useI18n();
	return (
		<PanelShell title={t('ui.static.role.assignments.1baf0a07', 'Role Assignments')}>
			<DataTable
				rows={rolePolicies}
				empty={
					<EmptyState
						title={t('ui.static.no.role.policies.a12a5674', 'No role policies')}
						body={t(
							'ui.static.role.routing.policies.seed.during.startup.088f425d',
							'Role routing policies seed during startup.',
						)}
					/>
				}
				columns={[
					{
						key: 'role',
						label: t('ui.static.role.c3f104d1', 'Role'),
						render: (row) => <span className="mono">{text(row.role)}</span>,
					},
					{
						key: 'mode',
						label: t('ui.static.mode.a7b93d21', 'Mode'),
						render: (row) => text(row.routingProfileId),
					},
					{
						key: 'primary',
						label: t(
							'ui.static.primary.provider.model.runtime.f0cce3eb',
							'Primary provider/model/runtime',
						),
						render: (row) => listLabel(row.preferred, t('app.runtime.card.none', 'none')),
					},
					{
						key: 'fallbacks',
						label: t('ui.static.fallbacks.7ad29425', 'Fallbacks'),
						render: (row) => listLabel(row.fallback, t('app.runtime.card.none', 'none')),
					},
					{
						key: 'maxCost',
						label: t('ui.static.max.cost.task.b474ecd8', 'Max cost/task'),
						render: (row) => money(row.maxCostPerTaskUsd, t('app.runtime.card.unknown', 'unknown')),
					},
					{
						key: 'maxTokens',
						label: t('ui.static.max.tokens.run.e7d1a6f7', 'Max tokens/run'),
						render: (row) => text(row.maxTokensPerRun),
					},
					{
						key: 'remote',
						label: t('ui.static.remote.allowed.2bd3880c', 'Remote allowed'),
						render: (row) => boolLabel(row.allowRemote),
					},
					{
						key: 'cli',
						label: t('ui.static.cli.allowed.31f8f238', 'CLI allowed'),
						render: (row) => boolLabel(row.allowCli),
					},
					{
						key: 'api',
						label: t('ui.static.api.allowed.4352398a', 'API allowed'),
						render: (row) => boolLabel(row.allowApi),
					},
					{
						key: 'thinking',
						label: t('ui.static.thinking.effort.3e220762', 'Thinking/effort'),
						render: (row) =>
							row.requiresApprovalForReasoningMax
								? t('app.modelGateway.role.maxRequiresApproval', 'max requires approval')
								: t('app.modelGateway.role.profileDefault', 'profile default'),
					},
					{
						key: 'approval',
						label: t('ui.static.approval.threshold.c49acc2d', 'Approval threshold'),
						render: (row) =>
							money(
								row.requiresApprovalOverUsd ?? row.maxCostPerTaskUsd,
								t('app.runtime.card.unknown', 'unknown'),
							),
					},
					{
						key: 'unknownCost',
						label: t('ui.static.unknown.remote.cost.ed33ec72', 'Unknown remote cost'),
						render: (row) => unknownCostPolicyLabel(row, t),
					},
				]}
			/>
		</PanelShell>
	);
}
