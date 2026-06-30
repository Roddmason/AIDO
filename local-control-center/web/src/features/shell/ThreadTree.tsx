/**
 * Threads tab of the shell sidebar: a workspace → thread tree backed by real `project_threads`.
 *
 * Top level is the workspace (project); its children are that project's real threads. Selecting a
 * workspace makes it the operational project; selecting a thread sets the active thread that drives the
 * center conversation. Real overview data (`overview.threads`) — no sessions/chats stand-ins and no
 * fabricated sub-threads. Collapsible per workspace with honest empty states; accessible via a
 * disclosure pattern (button headers with aria-expanded).
 * @author Rodrigo Mason
 */
import { ChevronRight, FolderKanban, MessageSquare, Plus } from 'lucide-react';
import { useState } from 'react';
import type { Overview, Project } from '../../api/types';
import { EmptyState } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { NEW_SESSION_ID } from '../workbench/useWorkbenchData';

type ThreadTreeProps = {
	projects: Project[];
	threads: Overview['threads'];
	selectedProjectId: string;
	selectedSessionId: string;
	filter: string;
	onSelectProject: (projectId: string) => void;
	onSelectSession: (threadId: string) => void;
};

/** The Projects tree body: workspaces with their nested real threads, or an honest empty state. */
export function ThreadTree({
	projects,
	threads,
	selectedProjectId,
	selectedSessionId,
	filter,
	onSelectProject,
	onSelectSession,
}: ThreadTreeProps) {
	const { t } = useI18n();
	const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

	if (!projects.length) {
		return (
			<EmptyState
				title={t('app.shell.threads.emptyTitle', 'No workspaces yet')}
				body={t('app.shell.threads.emptyBody', 'Open a folder to start your first workspace.')}
			/>
		);
	}

	const needle = filter.trim().toLowerCase();

	return (
		<div className="thread-tree">
			{projects.map((project) => {
				const projectThreads = threads.filter((thread) => thread.projectId === project.id);
				const open =
					project.id in collapsed ? !collapsed[project.id] : project.id === selectedProjectId;
				const visibleThreads = needle
					? projectThreads.filter((thread) =>
							String(thread.title ?? '')
								.toLowerCase()
								.includes(needle),
						)
					: projectThreads;
				if (needle && !visibleThreads.length && !project.name.toLowerCase().includes(needle)) {
					return null;
				}
				return (
					<section className="thread-workspace" key={project.id}>
						<button
							type="button"
							className="thread-workspace-head"
							aria-expanded={open}
							aria-controls={`thread-children-${project.id}`}
							onClick={() => {
								onSelectProject(project.id);
								setCollapsed((prev) => ({ ...prev, [project.id]: open }));
							}}
						>
							<ChevronRight
								aria-hidden="true"
								size={14}
								className={open ? 'thread-caret is-open' : 'thread-caret'}
							/>
							<FolderKanban aria-hidden="true" size={15} />
							<span className="thread-workspace-name">{project.name}</span>
							<span className="thread-count">{projectThreads.length}</span>
						</button>
						{open ? (
							// biome-ignore lint/a11y/useSemanticElements: <fieldset> carries form-control semantics plus UA chrome (border, margin-inline:2px, min-inline-size:min-content) that .thread-children — a single-use flex column — does not reset, regressing the disclosure layout; role="group" keeps the aria-controls grouping target intact.
							<div className="thread-children" role="group" id={`thread-children-${project.id}`}>
								<button
									type="button"
									className="thread-row is-new"
									aria-current={
										selectedProjectId === project.id && selectedSessionId === NEW_SESSION_ID
											? 'true'
											: undefined
									}
									onClick={() => {
										onSelectProject(project.id);
										onSelectSession(NEW_SESSION_ID);
									}}
								>
									<Plus aria-hidden="true" size={14} />
									<span>{t('app.shell.threads.newThread', 'New thread')}</span>
								</button>
								{visibleThreads.map((thread) => {
									const active =
										selectedProjectId === project.id && selectedSessionId === thread.id;
									return (
										<button
											type="button"
											key={thread.id}
											className="thread-row"
											aria-current={active ? 'true' : undefined}
											onClick={() => {
												onSelectProject(project.id);
												onSelectSession(thread.id);
											}}
										>
											<span
												className={`thread-dot tone-${toneForStatus(String(thread.status ?? 'unknown'))}`}
												aria-hidden="true"
											/>
											<MessageSquare aria-hidden="true" size={14} />
											<span className="thread-row-name">{thread.title}</span>
										</button>
									);
								})}
								{projectThreads.length ? null : (
									<p className="thread-empty">
										{t('app.shell.threads.workspaceEmpty', 'No threads yet — start one above.')}
									</p>
								)}
							</div>
						) : null}
					</section>
				);
			})}
		</div>
	);
}
