/**
 * Left sidebar of the thread/loop shell: a Threads | Loops tab switch over a shared filter field.
 *
 * Threads renders the workspace → thread tree ({@link ThreadTree}); Loops renders the selected
 * workspace's product loops with phase + status ({@link LoopList}). It replaces the area Explorer for
 * the `threads` area; all selection flows up via callbacks so the center (the Workbench) stays in sync.
 * The active tab persists for the session in localStorage so a reload reopens where the user left off.
 */
import { Search } from 'lucide-react';
import { useState } from 'react';
import type { Overview } from '../../api/types';
import { SegmentedControl } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { LoopList } from './LoopList';
import { ThreadTree } from './ThreadTree';

type SidebarTab = 'threads' | 'loops';
const TAB_STORAGE_KEY = 'aido:shell:sidebar-tab';

function readStoredTab(): SidebarTab {
	try {
		return window.localStorage.getItem(TAB_STORAGE_KEY) === 'loops' ? 'loops' : 'threads';
	} catch {
		return 'threads';
	}
}

type ShellSidebarProps = {
	overview: Overview;
	selectedProjectId: string;
	selectedSessionId: string;
	onSelectProject: (projectId: string) => void;
	onSelectSession: (sessionId: string) => void;
	onCreateProject: () => void;
};

/** Renders the Threads/Loops sidebar and owns only its active tab and filter text. */
export function ShellSidebar({
	overview,
	selectedProjectId,
	selectedSessionId,
	onSelectProject,
	onSelectSession,
	onCreateProject,
}: ShellSidebarProps) {
	const { t } = useI18n();
	const [tab, setTab] = useState<SidebarTab>(readStoredTab);
	const [filter, setFilter] = useState('');
	const projects = overview.projects.filter((project) => project.status === 'active');

	const selectTab = (next: SidebarTab) => {
		setTab(next);
		try {
			window.localStorage.setItem(TAB_STORAGE_KEY, next);
		} catch {
			// localStorage is optional in restricted browser contexts.
		}
	};

	return (
		<aside className="shell-sidebar" aria-label={t('app.shell.sidebar', 'Threads and loops')}>
			<div className="shell-sidebar-tabs">
				<SegmentedControl
					label={t('app.shell.tabsLabel', 'Sidebar view')}
					value={tab}
					onChange={selectTab}
					options={[
						{ value: 'threads', label: t('app.shell.tabThreads', 'Threads') },
						{ value: 'loops', label: t('app.shell.tabLoops', 'Loops') },
					]}
				/>
			</div>
			<div className="shell-search">
				<Search aria-hidden="true" size={15} />
				<input
					type="search"
					className="shell-search-input"
					aria-label={t('app.shell.searchLabel', 'Search threads and loops')}
					placeholder={t('app.shell.searchPlaceholder', 'Search…')}
					value={filter}
					onChange={(event) => setFilter(event.target.value)}
				/>
			</div>
			<div className="shell-sidebar-body">
				{tab === 'threads' ? (
					<ThreadTree
						projects={projects}
						sessions={overview.sessions}
						chats={overview.chats}
						selectedProjectId={selectedProjectId}
						selectedSessionId={selectedSessionId}
						filter={filter}
						onSelectProject={onSelectProject}
						onSelectSession={onSelectSession}
						onCreateProject={onCreateProject}
					/>
				) : (
					<LoopList projectId={selectedProjectId || undefined} filter={filter} />
				)}
			</div>
		</aside>
	);
}
