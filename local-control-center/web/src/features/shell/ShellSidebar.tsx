/**
 * Left sidebar of the thread/loop shell: a single Projects navigator (clean Codex-IDE shape).
 *
 * Top: a "New thread" action and a search field. Body: the workspace → thread tree ({@link ThreadTree})
 * under a "Projects" section label. Bottom: Open folder + Settings. It replaces the area Explorer for the
 * `threads` area, and all selection flows up via callbacks so the center (the Workbench) stays in sync.
 * There are no tabs — the product-loop list lives in the inspector (Plan), keeping this a focused
 * project/thread navigator.
 * @author Rodrigo Mason
 */
import { Plus, Search, Settings as SettingsIcon } from 'lucide-react';
import { useState } from 'react';
import type { Overview } from '../../api/types';
import { useI18n } from '../../i18n/I18nProvider';
import { NEW_SESSION_ID } from '../workbench/useWorkbenchData';
import { ThreadTree } from './ThreadTree';

type ShellSidebarProps = {
	overview: Overview;
	selectedProjectId: string;
	selectedSessionId: string;
	onSelectProject: (projectId: string) => void;
	onSelectSession: (sessionId: string) => void;
	onCreateProject: () => void;
	/** Opens the Settings modal at an optional section. */
	onOpenSettings: (section?: string) => void;
};

/** Renders the Projects sidebar and owns only its filter text. */
export function ShellSidebar({
	overview,
	selectedProjectId,
	selectedSessionId,
	onSelectProject,
	onSelectSession,
	onCreateProject,
	onOpenSettings,
}: ShellSidebarProps) {
	const { t } = useI18n();
	const [filter, setFilter] = useState('');
	const projects = overview.projects.filter((project) => project.status === 'active');

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
					sessions={overview.sessions}
					chats={overview.chats}
					selectedProjectId={selectedProjectId}
					selectedSessionId={selectedSessionId}
					filter={filter}
					onSelectProject={onSelectProject}
					onSelectSession={onSelectSession}
				/>
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
		</aside>
	);
}
