/**
 * Left rail of the Workbench: picks the active workspace folder and lets the user switch between
 * its work sessions and recent runs, which drives what the rest of the page shows.
 * @author Rodrigo Mason
 */
import { Bot, FolderKanban, GitBranch, History, Workflow } from 'lucide-react';

import type { Overview, Project } from '../../api/types';
import { StatusChip as Badge, EmptyState, Surface } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { formatTime, shortId, toneForStatus } from '../../lib/format';

/** Workspace/session/run navigator; selection is controlled by the page via the on* callbacks. */
export function WorkbenchExplorer({
	projects,
	project,
	branch,
	sessions,
	recentRuns,
	workflows,
	selectedSessionId,
	selectedRunId,
	newSessionSentinel,
	onSelectProject,
	onSelectSession,
	onSelectRun,
	onCreateProject,
}: {
	projects: Project[];
	project: Project | null;
	branch: string;
	sessions: Overview['threads'];
	recentRuns: Overview['workflowRuns'];
	workflows: Overview['workflows'];
	selectedSessionId: string;
	selectedRunId: string;
	newSessionSentinel: string;
	onSelectProject: (projectId: string) => void;
	onSelectSession: (sessionId: string) => void;
	onSelectRun: (runId: string) => void;
	onCreateProject: () => void;
}) {
	const { t } = useI18n();
	const activeSession = sessions.find((session) => session.id === selectedSessionId) ?? null;
	const workflowTitle = (workflowId: string) =>
		workflows.find((workflow) => workflow.id === workflowId)?.title ?? shortId(workflowId);

	return (
		<aside
			className="workbench-session-rail"
			aria-label={t('app.workbench.explorer.region', 'Workspace explorer')}
		>
			<Surface title={t('app.workbench.workspace.title', 'Workspace folder')}>
				<div className="workspace-context">
					<div className="field">
						<label htmlFor="workbench-project">
							{t('app.workbench.workspace.label', 'Workspace folder')}
						</label>
						<select
							id="workbench-project"
							className="select workspace-select"
							value={project?.id ?? ''}
							disabled={!projects.length}
							onChange={(event) => onSelectProject(event.target.value)}
						>
							{projects.length ? null : (
								<option value="">{t('app.workbench.workspace.none', 'No active workspace')}</option>
							)}
							{projects.map((item) => (
								<option key={item.id} value={item.id}>
									{item.name}
								</option>
							))}
						</select>
					</div>
					<div className="workspace-root-card">
						<span>{t('app.workbench.workspace.rootPath', 'Root path')}</span>
						<strong className="mono">
							{project
								? String(project.path ?? project.id)
								: t('app.workbench.workspace.missingPath', 'not selected')}
						</strong>
						<div className="workspace-root-meta">
							<Badge tone={project ? toneForStatus(String(project.status ?? 'active')) : 'warn'}>
								{project
									? String(project.status ?? 'active')
									: t('app.workbench.workspace.noSelection', 'no selection')}
							</Badge>
							<Badge>
								<GitBranch aria-hidden="true" size={13} /> {branch}
							</Badge>
						</div>
					</div>
					<button className="button primary" type="button" onClick={onCreateProject}>
						<FolderKanban aria-hidden="true" size={16} />
						{t('app.workbench.workspace.create', 'Import or create workspace')}
					</button>
				</div>
			</Surface>

			<Surface title={t('app.workbench.sessions.title', 'Project threads')}>
				<div className="session-list">
					<button
						className="session-item new-session"
						type="button"
						aria-current={
							selectedSessionId === newSessionSentinel || !activeSession ? 'page' : undefined
						}
						onClick={() => onSelectSession(newSessionSentinel)}
					>
						<span>
							<Bot aria-hidden="true" size={15} />{' '}
							{t('app.workbench.sessions.new', 'New project thread')}
						</span>
						<small>{t('app.workbench.sessions.newHint', 'Starts a real project thread')}</small>
					</button>
					{sessions.map((session) => {
						return (
							<button
								key={session.id}
								className="session-item"
								type="button"
								aria-current={activeSession?.id === session.id ? 'page' : undefined}
								onClick={() => onSelectSession(session.id)}
							>
								<span>
									<History aria-hidden="true" size={15} /> {session.title}
								</span>
								<small>
									{String(session.status)} -{' '}
									{formatTime(
										session.updatedAt,
										t('app.workbenchEvidence.notRecorded', 'not recorded'),
									)}
								</small>
							</button>
						);
					})}
					{sessions.length ? null : (
						<EmptyState
							title={t('app.workbench.sessions.emptyTitle', 'No project threads')}
							body={t(
								'app.workbench.sessions.emptyBody',
								'Your first prompt will create a real thread for this workspace folder.',
							)}
						/>
					)}
				</div>
			</Surface>

			<Surface title={t('app.workbench.recentRuns.title', 'Recent runs')}>
				<div className="command-list">
					{recentRuns.slice(0, 6).map((run) => (
						<button
							key={run.id}
							className="command-item"
							type="button"
							aria-current={selectedRunId === run.id ? 'page' : undefined}
							onClick={() => onSelectRun(run.id)}
						>
							<span>
								<Workflow aria-hidden="true" size={15} /> {workflowTitle(run.workflowId)}
							</span>
							<small>
								{String(run.status)} -{' '}
								{formatTime(run.startedAt, t('app.workbenchEvidence.notRecorded', 'not recorded'))}
							</small>
						</button>
					))}
					{recentRuns.length ? null : (
						<EmptyState
							title={t('app.workbench.recentRuns.emptyTitle', 'No runs yet')}
							body={t(
								'app.workbench.recentRuns.emptyBody',
								'Autonomous intake creates workflow runs you can inspect here.',
							)}
						/>
					)}
				</div>
			</Surface>
		</aside>
	);
}
