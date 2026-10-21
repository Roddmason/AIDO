/**
 * Memory tab of the Thread Inspector: what AIDO recalls about work like this (similar threads, previous decisions, evidence, lessons, performance passes and implemented functionality).
 * @author Rodrigo Mason
 */
import type { ReactNode } from 'react';
import type { ThreadMemoryRecallResponse } from '../../../api/client';
import { StatusChip } from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';
import { threadStatusTone } from '../../../lib/format';
import type { InspectorResource } from '../useThreadInspectorData';
import { formatWhen, humanize } from './inspectorValues';
import { NoThreadState, PanelError, PanelSkeleton, SectionTitle } from './panelStates';

/** One recall category: a titled list when there is data, an honest empty line when not. */
function MemorySection<T>({
	title,
	emptyText,
	items,
	renderItem,
}: {
	title: string;
	emptyText: string;
	items: ReadonlyArray<T>;
	renderItem: (item: T) => ReactNode;
}) {
	return (
		<>
			<SectionTitle>{title}</SectionTitle>
			{items.length ? (
				<ul className="thread-inspector-list">{items.map(renderItem)}</ul>
			) : (
				<p className="thread-inspector-subline">{emptyText}</p>
			)}
		</>
	);
}

export function MemoryPanel({
	threadId,
	memory,
}: {
	threadId: string | null;
	memory: InspectorResource<ThreadMemoryRecallResponse>;
}) {
	const { t, language } = useI18n();
	if (!threadId) return <NoThreadState />;
	if (memory.error) return <PanelError onRetry={memory.reload} />;
	if (!memory.data) return <PanelSkeleton />;
	const recall = memory.data;

	return (
		<div className="thread-inspector-stack">
			<p className="thread-inspector-subline">
				{t('app.threads.inspector.memory.summary', 'What AIDO remembers about work like this')} ·{' '}
				{formatWhen(recall.generatedAt, language)}
			</p>

			<MemorySection
				title={t('app.threads.inspector.memory.similar', 'Similar threads')}
				emptyText={t('app.threads.inspector.memory.similarEmpty', 'No similar threads found.')}
				items={recall.similarThreads}
				renderItem={(candidate) => (
					<li className="thread-inspector-row" key={candidate.threadId}>
						<div className="thread-inspector-row-main">
							<strong>{candidate.title}</strong>
							<span className="thread-inspector-subline">{candidate.reason}</span>
						</div>
						<div className="thread-inspector-row-side">
							{candidate.status === 'archived' ? (
								<StatusChip tone={threadStatusTone(candidate.status)}>
									{t('app.threads.similar.archived', 'Archived')}
								</StatusChip>
							) : null}
							<StatusChip tone="info">{Math.round(candidate.score * 100)}%</StatusChip>
						</div>
					</li>
				)}
			/>

			<MemorySection
				title={t('app.threads.inspector.memory.decisions', 'Previous decisions')}
				emptyText={t(
					'app.threads.inspector.memory.decisionsEmpty',
					'No decisions from similar work yet.',
				)}
				items={recall.previousDecisions}
				renderItem={(decision) => (
					<li className="thread-inspector-row" key={decision.id}>
						<div className="thread-inspector-row-main">
							<strong>{decision.title || decision.prompt}</strong>
							{decision.resolution ? (
								<span className="thread-inspector-subline">
									{t('app.threads.inspector.memory.resolution', 'Resolution')}:{' '}
									{decision.resolution}
								</span>
							) : null}
							<span className="thread-inspector-subline">
								{decision.threadTitle}
								{decision.threadTitle ? ' · ' : ''}
								{formatWhen(decision.decidedAt ?? decision.createdAt, language)}
							</span>
						</div>
					</li>
				)}
			/>

			<MemorySection
				title={t('app.threads.inspector.memory.evidence', 'Related evidence')}
				emptyText={t(
					'app.threads.inspector.memory.evidenceEmpty',
					'No evidence linked from similar work yet.',
				)}
				items={recall.relatedEvidence}
				renderItem={(evidence) => (
					<li className="thread-inspector-row" key={evidence.id}>
						<div className="thread-inspector-row-main">
							<strong>{evidence.title || humanize(evidence.kind)}</strong>
							<span className="thread-inspector-subline">
								{evidence.threadTitle}
								{evidence.threadTitle ? ' · ' : ''}
								{formatWhen(evidence.createdAt, language)}
							</span>
						</div>
						<div className="thread-inspector-row-side">
							<StatusChip tone="info">{humanize(evidence.kind)}</StatusChip>
						</div>
					</li>
				)}
			/>

			<MemorySection
				title={t('app.threads.inspector.memory.lessons', 'Lessons learned')}
				emptyText={t(
					'app.threads.inspector.memory.lessonsEmpty',
					'No lessons recorded for this project yet.',
				)}
				items={recall.lessonsLearned}
				renderItem={(lesson) => (
					<li className="thread-inspector-row" key={lesson.id}>
						<div className="thread-inspector-row-main">
							<span className="thread-inspector-memory-content">{lesson.content}</span>
							{lesson.matchedKeywords.length ? (
								<span className="thread-inspector-subline mono">
									{lesson.matchedKeywords.join(' · ')}
								</span>
							) : null}
							<span className="thread-inspector-subline">
								{formatWhen(lesson.createdAt, language)}
							</span>
						</div>
					</li>
				)}
			/>

			<MemorySection
				title={t('app.threads.inspector.memory.performance', 'Prior performance issues')}
				emptyText={t(
					'app.threads.inspector.memory.performanceEmpty',
					'No performance passes recorded for this work.',
				)}
				items={recall.performanceIssues}
				renderItem={(issue) => (
					<li className="thread-inspector-row" key={issue.id}>
						<div className="thread-inspector-row-main">
							<span className="thread-inspector-memory-content">{issue.note}</span>
							<span className="thread-inspector-subline">
								{issue.threadTitle}
								{issue.threadTitle ? ' · ' : ''}
								{formatWhen(issue.createdAt, language)}
							</span>
						</div>
					</li>
				)}
			/>

			<MemorySection
				title={t('app.threads.inspector.memory.implemented', 'Functionality already implemented')}
				emptyText={t(
					'app.threads.inspector.memory.implementedEmpty',
					'No resolved similar threads yet.',
				)}
				items={recall.implementedFunctionality}
				renderItem={(done) => (
					<li className="thread-inspector-row" key={done.threadId}>
						<div className="thread-inspector-row-main">
							<strong>{done.title}</strong>
							{done.summary ? (
								<span className="thread-inspector-subline">{done.summary}</span>
							) : null}
							<span className="thread-inspector-subline">
								{formatWhen(done.updatedAt, language)}
							</span>
						</div>
						<div className="thread-inspector-row-side">
							<StatusChip tone="ok">{Math.round(done.score * 100)}%</StatusChip>
						</div>
					</li>
				)}
			/>
		</div>
	);
}
