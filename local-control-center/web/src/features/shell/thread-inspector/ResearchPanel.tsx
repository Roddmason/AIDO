/**
 * Research tab of the Thread Inspector: research report artifacts with recommendation, recorded result, sources with trust levels and discrepancies.
 * @author Rodrigo Mason
 */
import { AlertTriangle, ExternalLink } from 'lucide-react';
import type { ThreadArtifact, ThreadDetail } from '../../../api/types';
import { Disclosure, EmptyState, StatusChip } from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';
import { toneForStatus } from '../../../lib/format';
import type { InspectorResource } from '../useThreadInspectorData';
import { arrayValue, asRecord, humanize, textValue, trustTone } from './inspectorValues';
import { NoThreadState, ResourceGate } from './panelStates';

type ResearchSourcePayload = {
	id?: string;
	url?: string;
	publisher?: string;
	trustLevel?: string;
	fetchedAt?: string;
};

function researchSources(metadata: unknown): ResearchSourcePayload[] {
	return arrayValue(asRecord(metadata).sources).map((entry) => {
		const record = asRecord(entry);
		return {
			id: textValue(record.id ?? record.sourceId),
			url: textValue(record.url),
			publisher: textValue(record.publisher),
			trustLevel: textValue(record.trustLevel),
			fetchedAt: textValue(record.fetchedAt),
		};
	});
}

export function ResearchPanel({
	detail,
	threadId,
}: {
	detail: InspectorResource<ThreadDetail>;
	threadId: string | null;
}) {
	const { t } = useI18n();
	if (!threadId) return <NoThreadState />;
	return (
		<ResourceGate resource={detail}>
			{(data) => {
				const reports = data.artifacts.filter((artifact) => artifact.kind === 'research_report');
				if (!reports.length) {
					return (
						<EmptyState
							title={t('app.threads.inspector.research.emptyTitle', 'No research yet')}
							body={t(
								'app.threads.inspector.research.empty',
								'No research requested for this thread.',
							)}
						/>
					);
				}
				return (
					<div className="thread-inspector-stack">
						{reports.map((artifact) => (
							<ResearchReportCard key={artifact.id} artifact={artifact} />
						))}
					</div>
				);
			}}
		</ResourceGate>
	);
}

function ResearchReportCard({ artifact }: { artifact: ThreadArtifact }) {
	const { t } = useI18n();
	const metadata = asRecord(artifact.metadata);
	const status = textValue(metadata.status) ?? 'research_required';
	const recommendation = asRecord(metadata.recommendation);
	const remediation = asRecord(metadata.remediation);
	const sources = researchSources(artifact.metadata);
	const discrepancies = arrayValue(metadata.discrepancies);
	const showRecoveryState =
		status !== 'research_ready' &&
		Boolean(textValue(metadata.reason) || textValue(remediation.summary));
	const showSourceList = sources.length > 0 || status === 'research_ready';

	return (
		<section className="thread-inspector-research">
			<div className="thread-inspector-row-main">
				<strong>{t('app.threads.research.historyTitle', 'Research history')}</strong>
				<span className="thread-inspector-subline">
					{t(
						'app.threads.research.historyHelp',
						'This report is a historical result. Current status and repair actions are shown in Execution.',
					)}
				</span>
			</div>
			<header className="thread-inspector-goal-head">
				<h3>{artifact.title}</h3>
				<StatusChip tone={toneForStatus(status.replace(/^research_/, ''))}>
					{humanize(status)}
				</StatusChip>
			</header>
			{textValue(recommendation.title) || textValue(recommendation.decision) ? (
				<div className="thread-inspector-row-main">
					<span className="thread-inspector-subline">
						{t('app.threads.research.recommendation', 'Recommendation')}
					</span>
					<strong>{textValue(recommendation.title) ?? ''}</strong>
					<span className="thread-inspector-subline">
						{textValue(recommendation.decision) ?? ''}
					</span>
				</div>
			) : null}
			{showRecoveryState ? (
				<div className="thread-research-state" data-status={status}>
					<div className="thread-research-state-head">
						<AlertTriangle aria-hidden="true" size={14} />
						<span>{t('app.threads.research.recordedResult', 'Recorded result')}</span>
					</div>
					{textValue(metadata.reason) ? <strong>{textValue(metadata.reason)}</strong> : null}
					{textValue(remediation.summary) ? <p>{textValue(remediation.summary)}</p> : null}
				</div>
			) : null}
			{showSourceList ? (
				<Disclosure
					headingLevel={4}
					title={t('app.threads.research.sources', 'Sources')}
					summary={String(sources.length)}
				>
					{sources.length ? (
						<ul className="thread-inspector-list">
							{sources.map((source, index) => (
								<li className="thread-inspector-row" key={source.id ?? source.url ?? `s-${index}`}>
									<div className="thread-inspector-row-main">
										{source.url ? (
											<a href={source.url} target="_blank" rel="noreferrer">
												{source.publisher ?? source.url}
												<ExternalLink
													aria-hidden="true"
													className="thread-inspector-external-icon"
													size={12}
												/>
												<span className="sr-only">
													{t('app.threads.inspector.research.newTab', 'opens in a new tab')}
												</span>
											</a>
										) : (
											<strong>{source.publisher ?? '—'}</strong>
										)}
										<span className="thread-inspector-subline">
											{source.fetchedAt ?? t('app.threads.research.noDate', 'No date')}
										</span>
									</div>
									<div className="thread-inspector-row-side">
										<StatusChip tone={trustTone(source.trustLevel ?? 'untrusted')}>
											{humanize(source.trustLevel ?? 'untrusted')}
										</StatusChip>
									</div>
								</li>
							))}
						</ul>
					) : (
						<p className="thread-inspector-subline">
							{t('app.threads.research.noSources', 'No sources persisted yet.')}
						</p>
					)}
				</Disclosure>
			) : null}
			{discrepancies.length ? (
				<Disclosure
					headingLevel={4}
					title={t('app.threads.research.discrepancies', 'Discrepancies')}
					summary={String(discrepancies.length)}
				>
					<ul className="thread-inspector-list">
						{discrepancies.map((item, index) => {
							const record = asRecord(item);
							return (
								<li className="thread-inspector-row" key={textValue(record.topic) ?? `d-${index}`}>
									<div className="thread-inspector-row-main">
										<strong>{textValue(record.topic) ?? '—'}</strong>
										<span className="thread-inspector-subline">
											{textValue(record.conflictingValues) ?? ''}
										</span>
									</div>
								</li>
							);
						})}
					</ul>
				</Disclosure>
			) : null}
		</section>
	);
}
