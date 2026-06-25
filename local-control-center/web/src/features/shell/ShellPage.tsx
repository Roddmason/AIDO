/**
 * Center surface of the thread/loop shell.
 *
 * Hosts the real Workbench experience — composer, session timeline, product loop, run timeline, diff
 * and evidence — with its in-page explorer hidden, because the {@link ShellSidebar} already provides
 * workspace/thread/loop navigation. Session selection is controlled by the shell
 * (`ctx.selectedSessionId`/`ctx.onSelectSession`) so the sidebar and the center stay in sync. This is a
 * reframe of existing, real components: no new data source and no fabricated timeline.
 */
import type { RouteContext } from '../../app/routes';
import { WorkbenchPage } from '../workbench/WorkbenchPage';

/** Renders the Workbench as the shell center, driven by the shell's controlled session selection. */
export function ShellPage({ ctx }: { ctx: RouteContext }) {
	return (
		<WorkbenchPage
			hideExplorer
			selectedSessionId={ctx.selectedSessionId}
			onSelectSession={ctx.onSelectSession}
			overview={ctx.overview}
			selectedProject={ctx.selectedProject}
			runtimeProviders={ctx.runtimeProviders}
			runtimeProviderConfiguration={ctx.runtimeProviderConfiguration}
			mutate={ctx.mutate}
			token={ctx.token}
			onSelectProject={ctx.onSelectProject}
			onCreateProject={() => ctx.openWorkspaceDialog('open_folder')}
			onOpenJobs={() => ctx.navigateTo('review-board')}
			onOpenEvidence={() => ctx.navigateTo('evidence')}
			onOpenSettings={() => ctx.navigateTo('settings-project')}
			onOpenRuntimeSetup={() => ctx.navigateTo('settings-runtime')}
			onRefresh={() => ctx.refresh(true)}
		/>
	);
}
