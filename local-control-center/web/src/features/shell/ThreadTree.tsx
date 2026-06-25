/**
 * Threads tab of the {@link ShellSidebar}: a workspace → thread tree.
 *
 * Top level is the workspace (project); its children are that workspace's work sessions ("threads").
 * Selecting a workspace makes it the operational project; selecting a thread sets the active session
 * that drives the center. The data model has no deeper nesting than workspace → session, so the tree
 * is two levels — it does not fabricate sub-threads. Real overview data; collapsible per workspace
 * with honest empty states. Accessible via a disclosure pattern (button headers with aria-expanded).
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
	sessions: Overview['sessions'];
	chats: Overview['chats'];
	selectedProjectId: string;
	selectedSessionId: string;
	filter: string;
	onSelectProject: (projectId: string) => void;
	onSelectSession: (sessionId: string) => void;
	onCreateProject: () => void;
};

/** The Threads tab body: workspaces with their nested sessions, or an honest empty state. */
export function ThreadTree({
	projects,
	sessions,
	chats,
	selectedProjectId,
	selectedSessionId,
	filter,
	onSelectProject,
	onSelectSession,
	onCreateProject,
}: ThreadTreeProps) {
	const { t } = useI18n();
	// Expanded workspaces; the operational workspace starts open so its threads are visible.
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
				const projectSessions = sessions.filter((session) => session.projectId === project.id);
				// Default: the operational workspace is open, others closed; a user toggle overrides.
				const open =
					project.id in collapsed ? !collapsed[project.id] : project.id === selectedProjectId;
				const visibleSessions = needle
					? projectSessions.filter((session) =>
							String(session.name ?? '')
								.toLowerCase()
								.includes(needle),
						)
					: projectSessions;
				if (needle && !visibleSessions.length && !project.name.toLowerCase().includes(needle)) {
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
							<span className="thread-count">{projectSessions.length}</span>
						</button>
						{open ? (
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
								{visibleSessions.map((session) => {
									const chatCount = chats.filter((chat) => chat.sessionId === session.id).length;
									const active =
										selectedProjectId === project.id && selectedSessionId === session.id;
									return (
										<button
											type="button"
											key={session.id}
											className="thread-row"
											aria-current={active ? 'true' : undefined}
											onClick={() => {
												onSelectProject(project.id);
												onSelectSession(session.id);
											}}
										>
											<span
												className={`thread-dot tone-${toneForStatus(String(session.status ?? 'unknown'))}`}
												aria-hidden="true"
											/>
											<MessageSquare aria-hidden="true" size={14} />
											<span className="thread-row-name">{session.name}</span>
											{chatCount > 0 ? <span className="thread-count">{chatCount}</span> : null}
										</button>
									);
								})}
								{projectSessions.length ? null : (
									<p className="thread-empty">
										{t('app.shell.threads.workspaceEmpty', 'No threads yet — start one above.')}
									</p>
								)}
							</div>
						) : null}
					</section>
				);
			})}
			<button type="button" className="thread-add-workspace" onClick={onCreateProject}>
				<Plus aria-hidden="true" size={15} />
				{t('app.shell.threads.openFolder', 'Open folder')}
			</button>
		</div>
	);
}
