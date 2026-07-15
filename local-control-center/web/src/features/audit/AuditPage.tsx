/**
 * Audit log: read-only ledger of every recorded mutation — action, actor and target — so each
 * change is traceable to who made it and what it touched.
 * @author Rodrigo Mason
 */
import type { Overview } from '../../api/types';
import { DataTable, EmptyState, PageHeader, Surface } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';

export function AuditPage({ overview }: { overview: Overview }) {
	const { t } = useI18n();
	return (
		<>
			<PageHeader
				kicker={t('ui.static.traceability.61d2e70b', 'Traceability')}
				title={t('app.nav.audit', 'Audit Log')}
				summary={t(
					'ui.static.every.mutation.needs.actor.target.payload.and.event.correlat.6735cfc2',
					'Every change records who made it, what it targeted, the data sent and a linked event.',
				)}
			/>
			<Surface title={t('ui.static.audit.records.e11faea4', 'Audit records')}>
				<DataTable
					rows={overview.auditEvents}
					empty={
						<EmptyState
							title={t('ui.static.no.audit.records.3d57cdc9', 'No audit records')}
							body={t(
								'ui.static.mutating.api.calls.will.be.recorded.here.ddf49c4f',
								'Every change to the system is recorded here.',
							)}
						/>
					}
					columns={[
						{
							key: 'action',
							label: t('ui.static.action.97c89a4d', 'Action'),
							render: (row) => <span className="mono">{String(row.action ?? '')}</span>,
						},
						{
							key: 'actor',
							label: t('ui.static.actor.cbd19b5c', 'Actor'),
							render: (row) => String(row.actor ?? ''),
						},
						{
							key: 'target',
							label: t('ui.static.target.61ad50a9', 'Target'),
							render: (row) => String(row.target ?? ''),
						},
					]}
				/>
			</Surface>
		</>
	);
}
