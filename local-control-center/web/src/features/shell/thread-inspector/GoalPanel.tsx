/**
 * Goal tab of the Thread Inspector: thread title, status, captured objective, owner, timestamps and pending decision count.
 * @author Rodrigo Mason
 */
import type { ThreadDetail } from '../../../api/types';
import { Disclosure, StatusChip } from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';
import { threadStatusTone } from '../../../lib/format';
import type { InspectorResource } from '../useThreadInspectorData';
import { formatWhen, humanize } from './inspectorValues';
import { NoThreadState, ResourceGate } from './panelStates';

export function GoalPanel({
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
				const thread = data.thread;
				const goalMessage = data.messages.find((message) => message.kind === 'user');
				const pendingDecisions = data.decisions.filter(
					(decision) => decision.status === 'pending',
				).length;
				return (
					<div className="thread-inspector-stack">
						<header className="thread-inspector-goal-head">
							<h3>{thread.title}</h3>
							<StatusChip tone={threadStatusTone(thread.status)}>
								{humanize(thread.status)}
							</StatusChip>
						</header>
						<p className="thread-inspector-goal-text">
							{goalMessage?.content.trim() ||
								thread.summary?.trim() ||
								t(
									'app.threads.inspector.goal.empty',
									'No goal captured yet — the first message sets the objective.',
								)}
						</p>
						<dl className="thread-inspector-meta">
							<div>
								<dt>{t('app.threads.inspector.goal.owner', 'Owner')}</dt>
								<dd className="mono">{humanize(thread.ownerType)}</dd>
							</div>
							<div>
								<dt>{t('app.threads.inspector.goal.created', 'Created')}</dt>
								<dd>{formatWhen(thread.createdAt, language)}</dd>
							</div>
							<div>
								<dt>{t('app.threads.inspector.goal.updated', 'Updated')}</dt>
								<dd>{formatWhen(thread.updatedAt, language)}</dd>
							</div>
							<div>
								<dt>{t('app.threads.inspector.goal.decisions', 'Pending decisions')}</dt>
								<dd>
									{pendingDecisions > 0 ? (
										<StatusChip tone="pending">{pendingDecisions}</StatusChip>
									) : (
										<span className="mono">0</span>
									)}
								</dd>
							</div>
						</dl>
						{thread.summary?.trim() && goalMessage ? (
							<Disclosure
								headingLevel={4}
								title={t('app.threads.inspector.goal.summary', 'Summary')}
							>
								<p className="thread-inspector-goal-text">{thread.summary}</p>
							</Disclosure>
						) : null}
					</div>
				);
			}}
		</ResourceGate>
	);
}
