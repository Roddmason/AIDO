/**
 * Backlog tab of the Thread Inspector: epics, stories and agent tasks of the product loop as a three-level list.
 * @author Rodrigo Mason
 */
import { EmptyState, StatusChip } from '../../../components/ui';
import { useI18n } from '../../../i18n/I18nProvider';
import { toneForStatus } from '../../../lib/format';
import type { InspectorResource } from '../useThreadInspectorData';
import { humanize, type LoopData } from './inspectorValues';
import { ResourceGate } from './panelStates';

export function BacklogPanel({ loop }: { loop: InspectorResource<LoopData> }) {
	const { t } = useI18n();
	return (
		<ResourceGate resource={loop}>
			{(data) => {
				if (!data.epics.length && !data.stories.length && !data.tasks.length) {
					return (
						<EmptyState
							title={t('app.threads.inspector.backlog.emptyTitle', 'Backlog is empty')}
							body={t(
								'app.threads.inspector.backlog.empty',
								'Stories appear here once planning breaks the goal down.',
							)}
						/>
					);
				}
				const epicIds = new Set(data.epics.map((epic) => epic.id));
				const storiesByEpic = new Map<string, LoopData['stories']>();
				for (const story of data.stories) {
					const key = story.epicId && epicIds.has(story.epicId) ? story.epicId : '';
					const bucket = storiesByEpic.get(key) ?? [];
					bucket.push(story);
					storiesByEpic.set(key, bucket);
				}
				const tasksByStory = new Map<string, LoopData['tasks']>();
				for (const task of data.tasks) {
					const storyKey = task.storyId ?? '';
					const bucket = tasksByStory.get(storyKey) ?? [];
					bucket.push(task);
					tasksByStory.set(storyKey, bucket);
				}
				const orphanStories = storiesByEpic.get('') ?? [];

				return (
					<div className="thread-inspector-stack">
						<p className="thread-inspector-subline mono">
							{t('app.threads.inspector.backlog.epics', 'Epics')} {data.epics.length} ·{' '}
							{t('app.threads.inspector.backlog.stories', 'Stories')} {data.stories.length} ·{' '}
							{t('app.threads.inspector.backlog.tasks', 'Agent tasks')} {data.tasks.length}
						</p>
						<ul className="thread-inspector-list">
							{data.epics.map((epic) => (
								<BacklogEpicRows
									key={epic.id}
									epic={epic}
									stories={storiesByEpic.get(epic.id) ?? []}
									tasksByStory={tasksByStory}
								/>
							))}
							{orphanStories.length ? (
								<BacklogEpicRows epic={null} stories={orphanStories} tasksByStory={tasksByStory} />
							) : null}
						</ul>
					</div>
				);
			}}
		</ResourceGate>
	);
}

function BacklogEpicRows({
	epic,
	stories,
	tasksByStory,
}: {
	epic: LoopData['epics'][number] | null;
	stories: LoopData['stories'];
	tasksByStory: Map<string, LoopData['tasks']>;
}) {
	const { t } = useI18n();
	return (
		<>
			<li className="thread-inspector-row" data-depth="0">
				<div className="thread-inspector-row-main">
					<strong>
						{epic ? epic.title : t('app.threads.inspector.backlog.unassigned', 'Without epic')}
					</strong>
					{epic?.priority ? (
						<span className="thread-inspector-subline mono">{humanize(epic.priority)}</span>
					) : null}
				</div>
				{epic ? (
					<div className="thread-inspector-row-side">
						<StatusChip tone={toneForStatus(epic.status)}>{humanize(epic.status)}</StatusChip>
					</div>
				) : null}
			</li>
			{stories.map((story) => (
				<BacklogStoryRows key={story.id} story={story} tasks={tasksByStory.get(story.id) ?? []} />
			))}
		</>
	);
}

function BacklogStoryRows({
	story,
	tasks,
}: {
	story: LoopData['stories'][number];
	tasks: LoopData['tasks'];
}) {
	return (
		<>
			<li className="thread-inspector-row" data-depth="1">
				<div className="thread-inspector-row-main">
					<strong>{story.title}</strong>
					{story.businessValue ? (
						<span className="thread-inspector-subline">{story.businessValue}</span>
					) : null}
				</div>
				<div className="thread-inspector-row-side">
					<StatusChip tone={toneForStatus(story.status)}>{humanize(story.status)}</StatusChip>
				</div>
			</li>
			{tasks.map((task) => (
				<li className="thread-inspector-row" data-depth="2" key={task.id}>
					<div className="thread-inspector-row-main">
						<span className="thread-inspector-subline">
							<span className="mono">{humanize(task.role)}</span> · {task.title}
						</span>
					</div>
					<div className="thread-inspector-row-side">
						<StatusChip tone={toneForStatus(task.status)}>{humanize(task.status)}</StatusChip>
					</div>
				</li>
			))}
		</>
	);
}
