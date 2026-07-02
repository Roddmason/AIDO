/**
 * Left sidebar of the thread/loop shell: a single Projects navigator (clean Codex-IDE shape).
 *
 * Top: a "New thread" action and a search field. Body: the workspace → thread tree ({@link ThreadTree})
 * under a "Projects" section label, plus a "Show archived" toggle that reveals each workspace's
 * archived threads (soft-deleted threads never render). Bottom: Open folder + Settings. It owns the
 * real thread lifecycle mutations its rows trigger — rename (PATCH), archive/unarchive with an undo
 * toast, confirm-then-delete (the server rejects running threads with 409), duplicate as a new loop
 * and copy-id — while selection flows up via callbacks so the center (the Workbench) stays in sync.
 * @author Rodrigo Mason
 */
import { Archive, Plus, Search, Settings as SettingsIcon } from 'lucide-react';
import { useEffect, useState } from 'react';
import {
	archiveThread,
	createThread,
	deleteThread,
	getThread,
	listThreads,
	postThreadMessage,
	renameThread,
	unarchiveThread,
} from '../../api/client';
import type { Overview, Thread } from '../../api/types';
import type { Mutate } from '../../app/routes';
import { Button, Dialog, useToast } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { NEW_SESSION_ID } from '../workbench/useWorkbenchData';
import { ThreadArtifactsDialog } from './ThreadArtifactsDialog';
import type { ThreadRowActions } from './ThreadTree';
import { ThreadTree } from './ThreadTree';

type ShellSidebarProps = {
	overview: Overview;
	selectedProjectId: string;
	selectedSessionId: string;
	mutate: Mutate;
	onSelectProject: (projectId: string) => void;
	onSelectSession: (sessionId: string) => void;
	onCreateProject: () => void;
	/** Opens the Settings modal at an optional section. */
	onOpenSettings: (section?: string) => void;
};

/** Audit reasons stored with each lifecycle mutation (backend data, not UI copy). */
const LIFECYCLE_REASONS = {
	archive: 'Archived from the sidebar by the operator.',
	undo: 'Archive undone from the sidebar by the operator.',
	unarchive: 'Unarchived from the sidebar by the operator.',
	delete: 'Deleted from the sidebar by the operator.',
} as const;

const THREAD_TITLE_MAX_LENGTH = 80;

/** Renders the Projects sidebar and owns its filter text plus the thread lifecycle actions. */
export function ShellSidebar({
	overview,
	selectedProjectId,
	selectedSessionId,
	mutate,
	onSelectProject,
	onSelectSession,
	onCreateProject,
	onOpenSettings,
}: ShellSidebarProps) {
	const { t } = useI18n();
	const { notify } = useToast();
	const [filter, setFilter] = useState('');
	const [showArchived, setShowArchived] = useState(false);
	const [archivedThreads, setArchivedThreads] = useState<Thread[]>([]);
	const [deleteTarget, setDeleteTarget] = useState<Thread | null>(null);
	const [deleting, setDeleting] = useState(false);
	const [artifactsTarget, setArtifactsTarget] = useState<Thread | null>(null);
	const projects = overview.projects.filter((project) => project.status === 'active');

	// Archived headers live outside the overview (its list excludes them), so the toggle drives its
	// own read; re-fetching when `overview.threads` changes keeps it in sync after archive/unarchive.
	// biome-ignore lint/correctness/useExhaustiveDependencies(overview.threads): intentional extra dependency — every lifecycle mutation refreshes the overview, and that refresh must re-pull the archived list too.
	useEffect(() => {
		if (!showArchived) return undefined;
		const controller = new AbortController();
		listThreads({ includeArchived: true }, controller.signal)
			.then((response) =>
				setArchivedThreads(response.threads.filter((thread) => thread.status === 'archived')),
			)
			.catch(() => {
				if (controller.signal.aborted) return;
				notify({
					title: t('app.shell.threads.archivedLoadError', 'Could not load archived threads.'),
					tone: 'danger',
				});
			});
		return () => controller.abort();
	}, [showArchived, overview.threads, notify, t]);

	const errorMessage = (error: unknown, fallback: string) =>
		error instanceof Error && error.message ? error.message : fallback;

	const restoreThread = async (thread: Thread, reselect: boolean, reason: string) => {
		try {
			await mutate((token) => unarchiveThread(token, thread.id, { reason }), {
				awaitRefresh: false,
			});
			if (reselect) {
				onSelectProject(thread.projectId);
				onSelectSession(thread.id);
			}
			notify({
				title: t('app.shell.threads.restored', 'Thread restored'),
				body: thread.title,
				tone: 'ok',
			});
		} catch (error) {
			notify({
				title: t('app.shell.threads.restoreError', 'Could not restore the thread.'),
				body: errorMessage(error, ''),
				tone: 'danger',
			});
		}
	};

	const actions: ThreadRowActions = {
		rename: async (thread, title) => {
			try {
				await mutate(
					(token) =>
						renameThread(token, thread.id, { title: title.slice(0, THREAD_TITLE_MAX_LENGTH) }),
					{ awaitRefresh: false },
				);
			} catch (error) {
				notify({
					title: t('app.shell.threads.renameError', 'Could not rename the thread.'),
					body: errorMessage(error, ''),
					tone: 'danger',
				});
			}
		},
		archive: async (thread) => {
			const wasSelected = selectedSessionId === thread.id;
			try {
				await mutate(
					(token) => archiveThread(token, thread.id, { reason: LIFECYCLE_REASONS.archive }),
					{ awaitRefresh: false },
				);
				if (wasSelected) onSelectSession(NEW_SESSION_ID);
				notify({
					title: t('app.shell.threads.archived', 'Thread archived'),
					body: thread.title,
					tone: 'info',
					durationMs: 8000,
					action: {
						label: t('app.shell.threads.undo', 'Undo'),
						onPress: () => void restoreThread(thread, wasSelected, LIFECYCLE_REASONS.undo),
					},
				});
			} catch (error) {
				notify({
					title: t('app.shell.threads.archiveError', 'Could not archive the thread.'),
					body: errorMessage(error, ''),
					tone: 'danger',
				});
			}
		},
		unarchive: (thread) => void restoreThread(thread, false, LIFECYCLE_REASONS.unarchive),
		requestDelete: (thread) => setDeleteTarget(thread),
		duplicate: async (thread) => {
			try {
				const detail = await getThread(thread.id);
				const created = await mutate(
					(token) =>
						createThread(token, {
							projectId: thread.projectId,
							ownerType: thread.ownerType,
							ownerId: thread.ownerId,
							title: `${thread.title} (copy)`.slice(0, THREAD_TITLE_MAX_LENGTH),
						}),
					{ awaitRefresh: false },
				);
				// Re-seeding the first user message is what makes the copy a *new loop*: the coordinator
				// re-runs intake on it. A bare thread (no user message yet) duplicates as an empty shell.
				const firstUserMessage = detail.messages.find((message) => message.kind === 'user');
				if (firstUserMessage) {
					await mutate(
						(token) =>
							postThreadMessage(token, created.thread.id, { content: firstUserMessage.content }),
						{ awaitRefresh: false },
					);
				}
				onSelectProject(thread.projectId);
				onSelectSession(created.thread.id);
				notify({
					title: t('app.shell.threads.duplicated', 'New loop created from thread'),
					body: created.thread.title,
					tone: 'ok',
				});
			} catch (error) {
				notify({
					title: t('app.shell.threads.duplicateError', 'Could not duplicate the thread.'),
					body: errorMessage(error, ''),
					tone: 'danger',
				});
			}
		},
		copyId: async (thread) => {
			try {
				await navigator.clipboard.writeText(thread.id);
				notify({
					title: t('app.shell.threads.idCopied', 'Thread id copied'),
					body: thread.id,
					tone: 'ok',
				});
			} catch {
				notify({
					title: t('app.shell.threads.idCopyError', 'Could not copy the thread id.'),
					body: thread.id,
					tone: 'danger',
					durationMs: 0,
				});
			}
		},
		openArtifacts: (thread) => setArtifactsTarget(thread),
	};

	const confirmDelete = async () => {
		if (!deleteTarget) return;
		setDeleting(true);
		try {
			await mutate(
				(token) => deleteThread(token, deleteTarget.id, { reason: LIFECYCLE_REASONS.delete }),
				{ awaitRefresh: false },
			);
			if (selectedSessionId === deleteTarget.id) onSelectSession(NEW_SESSION_ID);
			notify({
				title: t('app.shell.threads.deleted', 'Thread deleted'),
				body: deleteTarget.title,
				tone: 'ok',
			});
			setDeleteTarget(null);
		} catch (error) {
			notify({
				title: t('app.shell.threads.deleteError', 'Could not delete the thread.'),
				body: errorMessage(error, ''),
				tone: 'danger',
			});
		} finally {
			setDeleting(false);
		}
	};

	const startNewThread = () => {
		if (selectedProjectId) onSelectSession(NEW_SESSION_ID);
		else onCreateProject();
	};

	return (
		<aside className="shell-sidebar" aria-label={t('app.shell.sectionProjects', 'Projects')}>
			<div className="shell-sidebar-top">
				<button type="button" className="shell-new-thread" onClick={startNewThread}>
					<Plus aria-hidden="true" size={16} />
					{t('app.shell.threads.newThread', 'New thread')}
				</button>
				<div className="shell-search">
					<Search aria-hidden="true" size={15} />
					<input
						type="search"
						className="shell-search-input"
						aria-label={t('app.shell.searchLabel', 'Search projects and threads')}
						placeholder={t('app.shell.searchPlaceholder', 'Search…')}
						value={filter}
						onChange={(event) => setFilter(event.target.value)}
					/>
				</div>
			</div>
			<div className="shell-sidebar-body">
				<p className="shell-section-label">{t('app.shell.sectionProjects', 'Projects')}</p>
				<ThreadTree
					projects={projects}
					threads={overview.threads}
					archivedThreads={archivedThreads}
					showArchived={showArchived}
					selectedProjectId={selectedProjectId}
					selectedSessionId={selectedSessionId}
					filter={filter}
					onSelectProject={onSelectProject}
					onSelectSession={onSelectSession}
					actions={actions}
				/>
				<button
					type="button"
					className="thread-archived-toggle"
					aria-pressed={showArchived}
					onClick={() => setShowArchived((value) => !value)}
				>
					<Archive aria-hidden="true" size={14} />
					{showArchived
						? t('app.shell.threads.hideArchived', 'Hide archived')
						: t('app.shell.threads.showArchived', 'Show archived')}
				</button>
				{showArchived && !archivedThreads.length ? (
					<p className="thread-empty">
						{t('app.shell.threads.archivedEmpty', 'No archived threads yet.')}
					</p>
				) : null}
			</div>
			<div className="shell-sidebar-footer">
				<button type="button" className="shell-footer-item" onClick={onCreateProject}>
					<Plus aria-hidden="true" size={15} />
					{t('app.shell.threads.openFolder', 'Open folder')}
				</button>
				<button
					type="button"
					className="shell-footer-item"
					onClick={() => onOpenSettings()}
					aria-label={t('app.shell.settings.ariaLabel', 'Open settings')}
				>
					<SettingsIcon aria-hidden="true" size={15} />
					{t('app.shell.settings', 'Settings')}
				</button>
			</div>
			<Dialog
				open={deleteTarget != null}
				onClose={() => {
					if (!deleting) setDeleteTarget(null);
				}}
				label={t('app.shell.threads.deleteTitle', 'Delete thread')}
				className="thread-delete-dialog"
			>
				<div className="thread-delete-body">
					<p>
						{t(
							'app.shell.threads.deleteBody',
							'The thread is hidden everywhere; its messages and artifacts stay stored for audit.',
						)}
					</p>
					<p className="thread-delete-name">{deleteTarget?.title}</p>
					<div className="thread-delete-actions">
						<Button variant="secondary" disabled={deleting} onClick={() => setDeleteTarget(null)}>
							{t('app.shell.threads.deleteCancel', 'Cancel')}
						</Button>
						<Button variant="danger" loading={deleting} onClick={() => void confirmDelete()}>
							{t('app.shell.threads.deleteConfirm', 'Delete thread')}
						</Button>
					</div>
				</div>
			</Dialog>
			<ThreadArtifactsDialog thread={artifactsTarget} onClose={() => setArtifactsTarget(null)} />
		</aside>
	);
}
