/**
 * Memory & Retrieval console: shows the canonical SQLite-backed memory count, the retrieval
 * backend posture (rebuildable vector indexes are not the source of truth) with an honest
 * "unknown"/degraded state, and a searchable, project-scoped table of the underlying memory
 * items. Read-only; every value is sourced from `overview`/`retrievalStatus`/`selectedProject`
 * props, never invented.
 * @author Rodrigo Mason
 */
import { useMemo, useState } from 'react';

import type { MemoryItemRecord } from '../../api/generated/openapi';
import type { Overview, Project, RetrievalStatus } from '../../api/types';
import {
	DataTable,
	EmptyState,
	PageHeader,
	SegmentedControl,
	StatusChip,
	Surface,
	TextField,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { formatTime, shortId } from '../../lib/format';

const CONTENT_PREVIEW_MAX_CHARS = 120;

type ProjectScope = 'all' | 'project';

function truncateContent(content: string): string {
	return content.length > CONTENT_PREVIEW_MAX_CHARS
		? `${content.slice(0, CONTENT_PREVIEW_MAX_CHARS)}…`
		: content;
}

/** Most informative provenance signal for a memory item: `sourceRef` when recorded, else its
 * supersede revision; appended with an expiry note when the item is not permanent. */
function memoryContextLabel(
	row: MemoryItemRecord,
	t: (key: string, fallback: string) => string,
): string {
	const primary = row.sourceRef
		? shortId(row.sourceRef)
		: t('app.pages.memoryVersionLabel', 'v{version}').replace('{version}', String(row.version));
	if (!row.expiresAt) return primary;
	const expiry = t('app.pages.memoryExpiresLabel', 'expires {date}').replace(
		'{date}',
		formatTime(row.expiresAt),
	);
	return `${primary} · ${expiry}`;
}

export function MemoryPage({
	overview,
	retrievalStatus,
	selectedProject,
}: {
	overview: Overview;
	retrievalStatus: RetrievalStatus | null;
	selectedProject: Project | null;
}) {
	const { t } = useI18n();
	const [search, setSearch] = useState('');
	const [scope, setScope] = useState<ProjectScope>('all');

	const sortedItems = useMemo(() => {
		return [...overview.memoryItems].sort(
			(a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt),
		);
	}, [overview.memoryItems]);

	const scopedItems = useMemo(() => {
		if (scope !== 'project' || !selectedProject) return sortedItems;
		return sortedItems.filter((item) => item.projectId === selectedProject.id);
	}, [sortedItems, scope, selectedProject]);

	const filteredItems = useMemo(() => {
		const query = search.trim().toLowerCase();
		if (!query) return scopedItems;
		return scopedItems.filter((item) =>
			[item.content, item.kind, item.scope].some((field) => field.toLowerCase().includes(query)),
		);
	}, [scopedItems, search]);

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
					{retrievalStatus?.degraded ? (
						<div className="inline" style={{ marginTop: 'var(--space-3)' }}>
							<StatusChip tone="warn">
								{t('app.pages.memoryIndexDegradedChip', 'Index degraded')}
							</StatusChip>
							<span className="metric-label">
								{t(
									'app.pages.memoryIndexDegradedExplain',
									'The vector index can be rebuilt without losing the source of truth in SQLite.',
								)}
								{retrievalStatus.reason ? ` (${retrievalStatus.reason})` : ''}
							</span>
						</div>
					) : null}
				</Surface>
				<Surface title={t('ui.static.memory.items.d8a2b209', 'Memory items')}>
					<div className="metric-value">{overview.memoryItems.length}</div>
					<div className="metric-label">
						{t('app.pages.memoryRecordsInSqlite', 'records in SQLite')}
					</div>
				</Surface>
			</div>
			<Surface title={t('app.pages.memoryRecordsTitle', 'Memory records')}>
				<div className="inline">
					<TextField
						label={t('app.pages.memorySearchLabel', 'Search memory items')}
						help={t('app.pages.memorySearchHelp', 'Matches kind, scope or content.')}
						placeholder={t('app.pages.memorySearchPlaceholder', 'Search content, kind or scope')}
						value={search}
						onChange={(event) => setSearch(event.target.value)}
					/>
					<SegmentedControl<ProjectScope>
						label={t('app.pages.memoryScopeLabel', 'Project scope')}
						value={scope}
						onChange={setScope}
						disabled={!selectedProject}
						options={[
							{ value: 'all', label: t('app.pages.memoryScopeAll', 'All projects') },
							{ value: 'project', label: t('app.pages.memoryScopeProject', 'This project') },
						]}
					/>
				</div>
				<div className="stack compact">
					<p className="metric-label">
						{t('app.pages.memoryFilteredCount', '{filtered} of {total} items')
							.replace('{filtered}', String(filteredItems.length))
							.replace('{total}', String(overview.memoryItems.length))}
					</p>
					{!selectedProject ? (
						<p className="metric-label">
							{t(
								'app.pages.memoryScopeNoProjectHelp',
								'Select a project to filter memory items by it.',
							)}
						</p>
					) : null}
				</div>
				<DataTable
					caption={t('app.pages.memoryRecordsCaption', 'Memory items table')}
					rows={filteredItems}
					empty={
						<EmptyState
							title={t('app.pages.memoryEmptyTitle', 'No memory items match')}
							body={
								search || scope === 'project'
									? t(
											'app.pages.memoryEmptyFilteredBody',
											'No memory items match the current search and scope filters.',
										)
									: t(
											'app.pages.memoryEmptyBody',
											'Memory items accumulate as agents record decisions, lessons and evidence.',
										)
							}
						/>
					}
					columns={[
						{
							key: 'kind',
							label: t('app.pages.memoryColKind', 'Kind'),
							render: (row: MemoryItemRecord) => <span className="mono">{row.kind}</span>,
						},
						{
							key: 'scope',
							label: t('app.pages.memoryColScope', 'Scope'),
							render: (row: MemoryItemRecord) => (
								<span className="mono">
									{row.scope}
									{row.scopeId ? ` · ${shortId(row.scopeId)}` : ''}
								</span>
							),
						},
						{
							key: 'content',
							label: t('app.pages.memoryColContent', 'Content'),
							render: (row: MemoryItemRecord) => truncateContent(row.content),
						},
						{
							key: 'context',
							label: t('app.pages.memoryColContext', 'Context'),
							render: (row: MemoryItemRecord) => (
								<span className="mono">{memoryContextLabel(row, t)}</span>
							),
						},
						{
							key: 'createdAt',
							label: t('app.pages.memoryColCreatedAt', 'Created'),
							render: (row: MemoryItemRecord) => formatTime(row.createdAt),
						},
					]}
				/>
			</Surface>
		</>
	);
}
