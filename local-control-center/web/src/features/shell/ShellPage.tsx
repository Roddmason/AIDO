/**
 * Center surface of the thread/loop shell.
 *
 * Hosts the real thread conversation ({@link ThreadConversation}): the selected thread's timeline plus
 * the composer that posts a user message and runs the coordinator (responds or blocks). Thread/workspace
 * navigation lives in the {@link ShellSidebar}; selection is controlled by the shell
 * (`ctx.selectedSessionId` holds the active thread id, `ctx.onSelectSession` changes it) so the sidebar
 * and the center stay in sync. Backed by `project_threads` — no decorative chat, no fabricated timeline.
 * @author Rodrigo Mason
 */
import type { RouteContext } from '../../app/routes';
import { ThreadConversation } from './ThreadConversation';

/** Renders the real thread conversation as the shell center, driven by the controlled selection. */
export function ShellPage({ ctx }: { ctx: RouteContext }) {
	return (
		<ThreadConversation
			overview={ctx.overview}
			selectedProject={ctx.selectedProject}
			selectedThreadId={ctx.selectedSessionId}
			mutate={ctx.mutate}
			token={ctx.token}
			onSelectThread={ctx.onSelectSession}
			onCreateProject={() => ctx.openWorkspaceDialog('open_folder')}
		/>
	);
}
