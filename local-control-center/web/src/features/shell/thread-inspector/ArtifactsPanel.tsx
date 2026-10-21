/**
 * Artifacts tab of the Thread Inspector: the thread's artifacts, newest first, capped to a visible limit with an honest count of the older ones.
 * @author Rodrigo Mason
 */
import { m } from 'motion/react';
import type { ThreadDetail } from '../../../api/types';
import { EmptyState } from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';
import { cardTransition, listStagger } from '../../../motion/variants';
import type { InspectorResource } from '../useThreadInspectorData';
import { formatWhen, humanize } from './inspectorValues';
import { NoThreadState, ResourceGate } from './panelStates';

const ARTIFACTS_LIMIT = 30;

export function ArtifactsPanel({
	detail,
	threadId,
}: {
	detail: InspectorResource<ThreadDetail>;
	threadId: string | null;
}) {
	const { t, language } = useI18n();
	if (!threadId) return <NoThreadState />;
	return (
		<ResourceGate resource={detail}>
			{(data) => {
				if (!data.artifacts.length) {
					return (
						<EmptyState
							title={t('app.threads.inspector.artifacts.emptyTitle', 'No artifacts yet')}
							body={t(
								'app.threads.inspector.artifacts.empty',
								'Evidence produced by the loop appears here.',
							)}
						/>
					);
				}
				const sorted = [...data.artifacts].sort((left, right) =>
					left.createdAt < right.createdAt ? 1 : -1,
				);
				const visible = sorted.slice(0, ARTIFACTS_LIMIT);
				const hidden = sorted.length - visible.length;
				return (
					<div className="thread-inspector-stack">
						<m.ul
							className="thread-inspector-list"
							variants={listStagger}
							initial="initial"
							animate="animate"
						>
							{visible.map((artifact) => (
								<m.li className="thread-inspector-row" key={artifact.id} variants={cardTransition}>
									<div className="thread-inspector-row-main">
										<strong>{artifact.title}</strong>
										<span className="thread-inspector-subline">
											{formatWhen(artifact.createdAt, language)}
										</span>
									</div>
									<div className="thread-inspector-row-side">
										<span className="thread-inspector-subline mono">{humanize(artifact.kind)}</span>
									</div>
								</m.li>
							))}
						</m.ul>
						{hidden > 0 ? (
							<p className="thread-inspector-subline">
								{t(
									'app.threads.inspector.artifacts.more',
									'{count} older artifacts not shown',
								).replace('{count}', String(hidden))}
							</p>
						) : null}
					</div>
				);
			}}
		</ResourceGate>
	);
}
