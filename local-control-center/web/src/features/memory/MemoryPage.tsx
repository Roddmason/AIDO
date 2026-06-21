/**
 * Memory & Retrieval console: shows the canonical SQLite-backed memory count and the retrieval
 * backend posture (rebuildable vector indexes are not the source of truth). Read-only.
 */
import type { Overview, RetrievalStatus } from '../../api/types';
import { PageHeader, Surface } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';

export function MemoryPage({
	overview,
	retrievalStatus,
}: {
	overview: Overview;
	retrievalStatus: RetrievalStatus | null;
}) {
	const { t } = useI18n();
	const retrievalPosture = retrievalStatus
		? retrievalStatus.available
			? retrievalStatus.degraded
				? t('app.pages.memoryIndexDegraded', 'index degraded')
				: t('app.modelGateway.runtime.available', 'available')
			: retrievalStatus.status
		: t('app.runtime.card.unknown', 'unknown');

	return (
		<>
			<PageHeader
				kicker={t('ui.static.semantic.context.8744e1fb', 'Semantic context')}
				title={t('app.nav.memory', 'Memory & Retrieval')}
				summary={t(
					'ui.static.sqlite.is.canonical.faiss.numpy.and.future.vector.stores.are.f2fb19e8',
					'SQLite is canonical. FAISS, NumPy and future vector stores are rebuildable indexes, not source of truth.',
				)}
			/>
			<div className="grid two">
				<Surface title={t('ui.static.retrieval.backend.fa3c92fd', 'Retrieval backend')}>
					<div className="metric-value">
						{String(retrievalStatus?.backend ?? t('app.runtime.card.unknown', 'unknown'))}
					</div>
					<div className="metric-label">{retrievalPosture}</div>
				</Surface>
				<Surface title={t('ui.static.memory.items.d8a2b209', 'Memory items')}>
					<div className="metric-value">{overview.memoryItems.length}</div>
					<div className="metric-label">
						{t('app.pages.memoryRecordsInSqlite', 'records in SQLite')}
					</div>
				</Surface>
			</div>
		</>
	);
}
