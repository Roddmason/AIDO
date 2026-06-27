/**
 * Read-only inventory of task-owned runtime workspaces (the isolated working trees agents run in).
 * Surfaces ownership and isolation type so two agents are never seen sharing one mutable tree.
 * @author Rodrigo Mason
 */
import type { Overview } from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';

export function WorkspacesPage({ overview }: { overview: Overview }) {
	const { t } = useI18n();
	return (
		<>
			<PageHeader
				kicker={t('ui.static.isolation.616318d9', 'Isolation')}
				title={t('app.nav.workspaces', 'Workspaces')}
				summary={t(
					'ui.static.task.owned.workspace.allocations.prevent.agents.from.sharing.42c454d7',
					'Task-owned workspace allocations prevent agents from sharing one mutable working tree.',
				)}
			/>
			<Surface title={t('ui.static.allocated.workspaces.b052cf67', 'Allocated workspaces')}>
				<DataTable
					rows={overview.runtimeWorkspaces}
					empty={
						<EmptyState
							title={t('ui.static.no.isolated.workspaces.1d0dc404', 'No isolated workspaces')}
							body={t(
								'ui.static.workflow.implementation.steps.will.allocate.workspaces.f402df35',
								'Workflow implementation steps will allocate workspaces.',
							)}
						/>
					}
					columns={[
						{
							key: 'task',
							label: t('ui.static.task.7bb0ddf9', 'Task'),
							render: (row) => <span className="mono">{String(row.taskId ?? '')}</span>,
						},
						{
							key: 'project',
							label: t('ui.static.project.f6f4da8d', 'Project'),
							render: (row) => <span className="mono">{String(row.projectId ?? '')}</span>,
						},
						{
							key: 'owner',
							label: t('ui.static.owner.89ff3122', 'Owner'),
							render: (row) => String(row.ownerAgentId ?? ''),
						},
						{
							key: 'isolation',
							label: t('ui.static.isolation.616318d9', 'Isolation'),
							render: (row) => <span className="mono">{String(row.isolationType ?? '')}</span>,
						},
						{
							key: 'status',
							label: t('ui.static.status.bae7d5be', 'Status'),
							render: (row) => (
								<Badge tone={toneForStatus(String(row.status ?? ''))}>
									{String(row.status ?? '')}
								</Badge>
							),
						},
					]}
				/>
			</Surface>
		</>
	);
}
