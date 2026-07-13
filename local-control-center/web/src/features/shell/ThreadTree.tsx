/**
 * Threads tab of the shell sidebar: a workspace → thread tree backed by real `project_threads`.
 *
 * Top level is the workspace (project); its children are that project's real threads. Selecting a
 * workspace makes it the operational project; selecting a thread sets the active thread that drives the
 * center conversation. Real overview data (`overview.threads`) — no sessions/chats stand-ins and no
 * fabricated sub-threads. Collapsible per workspace with honest empty states; accessible via a
 * disclosure pattern (button headers with aria-expanded). Each thread row carries a contextual menu
 * (rename inline, archive, duplicate as a new loop, copy id, open artifacts, delete) and, when the
 * parent enables it, an "Archived" section with the project's archived threads.
 * @author Rodrigo Mason
 */
import {
	Archive,
	ArchiveRestore,
	ChevronRight,
	Copy,
	CopyPlus,
	FileStack,
	FolderKanban,
	MessageSquare,
	MoreHorizontal,
	Pencil,
	Plus,
	Trash2,
} from 'lucide-react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useRef, useState } from 'react';
import type { Overview, Project, Thread } from '../../api/types';
import { cn, EmptyState, IconButton } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { NEW_SESSION_ID } from '../workbench/useWorkbenchData';
import type { ThreadMenuItem } from './ThreadRowMenu';
import { ThreadRowMenu } from './ThreadRowMenu';

/** Lifecycle/utility callbacks each thread row can trigger; the sidebar owns the real mutations. */
export type ThreadRowActions = {
	rename: (thread: Thread, title: string) => void;
	archive: (thread: Thread) => void;
	unarchive: (thread: Thread) => void;
	/** Opens the confirm-delete dialog; the actual DELETE happens after confirmation. */
	requestDelete: (thread: Thread) => void;
	duplicate: (thread: Thread) => void;
	copyId: (thread: Thread) => void;
	openArtifacts: (thread: Thread) => void;
};

type ThreadTreeProps = {
	projects: Project[];
	threads: Overview['threads'];
	/** Archived thread headers (already filtered to `status === 'archived'`), all projects. */
	archivedThreads: Thread[];
	showArchived: boolean;
	selectedProjectId: string;
	selectedSessionId: string;
	filter: string;
	onSelectProject: (projectId: string) => void;
	onSelectSession: (threadId: string) => void;
	actions: ThreadRowActions;
};

/** Statuses where the run must stop before a delete is allowed (mirrors the server 409 rule). */
const DELETE_BLOCKING_STATUSES = new Set(['queued', 'running']);

function matchesNeedle(thread: Thread, needle: string) {
	return String(thread.title ?? '')
		.toLowerCase()
		.includes(needle);
}

/** The Projects tree body: workspaces with their nested real threads, or an honest empty state. */
export function ThreadTree({
	projects,
	threads,
	archivedThreads,
	showArchived,
	selectedProjectId,
	selectedSessionId,
	filter,
	onSelectProject,
	onSelectSession,
	actions,
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
				const projectArchived = showArchived
					? archivedThreads.filter((thread) => thread.projectId === project.id)
					: [];
				const open =
					project.id in collapsed ? !collapsed[project.id] : project.id === selectedProjectId;
				const visibleThreads = needle
					? projectThreads.filter((thread) => matchesNeedle(thread, needle))
					: projectThreads;
				const visibleArchived = needle
					? projectArchived.filter((thread) => matchesNeedle(thread, needle))
					: projectArchived;
				if (
					needle &&
					!visibleThreads.length &&
					!visibleArchived.length &&
					!project.name.toLowerCase().includes(needle)
				) {
					return null;
				}
				return (
					<section className="thread-workspace" key={project.id}>
						<button
							type="button"
							className="thread-workspace-head"
							title={project.name}
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
								{visibleThreads.map((thread) => (
									<ThreadRow
										key={thread.id}
										thread={thread}
										archived={false}
										active={selectedProjectId === project.id && selectedSessionId === thread.id}
										actions={actions}
										onSelect={() => {
											onSelectProject(project.id);
											onSelectSession(thread.id);
										}}
									/>
								))}
								{projectThreads.length ? null : (
									<p className="thread-empty">
										{t('app.shell.threads.workspaceEmpty', 'No threads yet — start one above.')}
									</p>
								)}
								{showArchived && visibleArchived.length ? (
									<>
										<p className="thread-archived-head">
											<Archive aria-hidden="true" size={12} />
											{t('app.shell.threads.archivedSection', 'Archived')}
										</p>
										{visibleArchived.map((thread) => (
											<ThreadRow
												key={thread.id}
												thread={thread}
												archived
												active={selectedProjectId === project.id && selectedSessionId === thread.id}
												actions={actions}
												onSelect={() => {
													onSelectProject(project.id);
													onSelectSession(thread.id);
												}}
											/>
										))}
									</>
								) : null}
							</div>
						) : null}
					</section>
				);
			})}
		</div>
	);
}

type ThreadRowProps = {
	thread: Thread;
	archived: boolean;
	active: boolean;
	actions: ThreadRowActions;
	onSelect: () => void;
};

/** One thread row: select button, inline rename editor and the contextual actions menu. */
function ThreadRow({ thread, archived, active, actions, onSelect }: ThreadRowProps) {
	const { t } = useI18n();
	const [menu, setMenu] = useState<{ at: { x: number; y: number } | null } | null>(null);
	const [renaming, setRenaming] = useState(false);
	const triggerRef = useRef<HTMLButtonElement>(null);

	const deleteBlocked = DELETE_BLOCKING_STATUSES.has(String(thread.status));
	const menuLabel = t('app.shell.threads.menu.label', 'Thread actions');

	const commitRename = (value: string) => {
		setRenaming(false);
		const title = value.trim();
		if (!title || title === thread.title) return;
		actions.rename(thread, title);
	};

	const handleRenameKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
		if (event.key === 'Enter') {
			event.preventDefault();
			commitRename(event.currentTarget.value);
			return;
		}
		if (event.key === 'Escape') {
			event.stopPropagation();
			setRenaming(false);
		}
	};

	const deleteItem: ThreadMenuItem = {
		id: 'delete',
		label: t('app.shell.threads.menu.delete', 'Delete'),
		icon: Trash2,
		tone: 'danger',
		separated: true,
		disabled: deleteBlocked,
		note: deleteBlocked
			? t('app.shell.threads.menu.deleteBlocked', 'Running thread — stop the run first.')
			: undefined,
		onSelect: () => actions.requestDelete(thread),
	};
	const copyIdItem: ThreadMenuItem = {
		id: 'copy-id',
		label: t('app.shell.threads.menu.copyId', 'Copy thread id'),
		icon: Copy,
		onSelect: () => actions.copyId(thread),
	};
	const artifactsItem: ThreadMenuItem = {
		id: 'artifacts',
		label: t('app.shell.threads.menu.openArtifacts', 'Open artifacts'),
		icon: FileStack,
		onSelect: () => actions.openArtifacts(thread),
	};
	const items: ThreadMenuItem[] = archived
		? [
				{
					id: 'unarchive',
					label: t('app.shell.threads.menu.unarchive', 'Unarchive'),
					icon: ArchiveRestore,
					onSelect: () => actions.unarchive(thread),
				},
				copyIdItem,
				artifactsItem,
				deleteItem,
			]
		: [
				{
					id: 'rename',
					label: t('app.shell.threads.menu.rename', 'Rename'),
					icon: Pencil,
					onSelect: () => setRenaming(true),
				},
				{
					id: 'archive',
					label: t('app.shell.threads.menu.archive', 'Archive'),
					icon: Archive,
					onSelect: () => actions.archive(thread),
				},
				{
					id: 'duplicate',
					label: t('app.shell.threads.menu.duplicate', 'Duplicate as new loop'),
					icon: CopyPlus,
					onSelect: () => actions.duplicate(thread),
				},
				copyIdItem,
				artifactsItem,
				deleteItem,
			];

	return (
		<div
			className={cn('thread-row', archived && 'is-archived')}
			aria-current={active ? 'true' : undefined}
		>
			{renaming ? (
				<input
					// biome-ignore lint/a11y/noAutofocus: the editor replaces the row the user just acted on; focusing it is the expected inline-rename behavior (VS Code/Codex pattern), not a focus steal.
					autoFocus
					className="thread-rename-input"
					aria-label={t('app.shell.threads.renameLabel', 'New thread title')}
					defaultValue={thread.title}
					onFocus={(event) => event.currentTarget.select()}
					onKeyDown={handleRenameKeyDown}
					onBlur={(event) => commitRename(event.currentTarget.value)}
				/>
			) : (
				<button
					type="button"
					className="thread-row-select"
					title={thread.title}
					onClick={onSelect}
					onContextMenu={(event) => {
						event.preventDefault();
						setMenu({ at: { x: event.clientX, y: event.clientY } });
					}}
				>
					<span
						className={`thread-dot tone-${toneForStatus(String(thread.status ?? 'unknown'))}`}
						aria-hidden="true"
					/>
					{archived ? (
						<Archive aria-hidden="true" size={14} />
					) : (
						<MessageSquare aria-hidden="true" size={14} />
					)}
					<span className="thread-row-name">{thread.title}</span>
				</button>
			)}
			<IconButton
				ref={triggerRef}
				className="thread-menu-trigger"
				aria-label={`${menuLabel}: ${thread.title}`}
				aria-haspopup="menu"
				aria-expanded={menu ? true : undefined}
				onClick={() => setMenu((current) => (current ? null : { at: null }))}
			>
				<MoreHorizontal aria-hidden="true" size={14} />
			</IconButton>
			{menu && triggerRef.current ? (
				<ThreadRowMenu
					label={menuLabel}
					items={items}
					anchor={triggerRef.current}
					at={menu.at}
					onClose={() => setMenu(null)}
				/>
			) : null}
		</div>
	);
}
