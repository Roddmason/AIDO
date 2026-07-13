/**
 * Workbench Timeline panel: the run-progress view. Combines signal counters, the
 * detailed run timeline, the project delivery flow and recent runs/pipelines/events.
 * @author Rodrigo Mason
 */
import type { Overview } from '../../../api/types';
import { Badge, DataTable, EmptyState, Surface } from '../../../components/primitives';
import { useI18n } from '../../../i18n/I18nProvider';
import { formatTime, shortId, toneForStatus } from '../../../lib/format';
import type { TimelineArtifactRef, WorkflowTimelineStage } from '../timelineModel';
import { WorkflowTimeline } from '../WorkflowTimeline';
import { sortByTimeDesc } from '../workbenchSelectors';

type Signals = { workflows: number; evidence: number; approvals: number; workspaces: number };

export function TimelinePanel({
	runTimeline,
	hasRun,
	deliveryTimeline,
	hasPipeline,
	signals,
	workflows,
	workflowRuns,
	workflowEvents,
	onOpenArtifact,
}: {
	runTimeline: WorkflowTimelineStage[];
	hasRun: boolean;
	deliveryTimeline: WorkflowTimelineStage[];
	hasPipeline: boolean;
	signals: Signals;
	workflows: Overview['workflows'];
	workflowRuns: Overview['workflowRuns'];
	workflowEvents: Overview['workflowEvents'];
	onOpenArtifact?: (artifact: TimelineArtifactRef) => void;
}) {
	const { t } = useI18n();
	const runs = sortByTimeDesc(workflowRuns).slice(0, 6);
	const events = sortByTimeDesc(workflowEvents).slice(0, 8);
	const workflowTitle = (workflowId: string) =>
		workflows.find((workflow) => workflow.id === workflowId)?.title ?? shortId(workflowId);

	return (
		<div className="stack">
			<div className="signal-grid">
				<div className="signal-card">
					<span>{t('app.workbench.signals.workflows', 'Recent workflows')}</span>
					<strong className="tnum">{signals.workflows}</strong>
				</div>
				<div className="signal-card">
					<span>{t('app.workbench.signals.evidence', 'Evidence packages')}</span>
					<strong className="tnum">{signals.evidence}</strong>
				</div>
				<div className="signal-card">
					<span>{t('app.workbench.signals.approvals', 'Pending approvals')}</span>
					<strong className="tnum">{signals.approvals}</strong>
				</div>
				<div className="signal-card">
					<span>{t('app.workbench.signals.workspaces', 'Runtime workspaces')}</span>
					<strong className="tnum">{signals.workspaces}</strong>
				</div>
			</div>

			<Surface title={t('app.workbench.timeline.runTitle', 'Run timeline')} flat>
				{hasRun ? (
					<WorkflowTimeline
						variant="detailed"
						stages={runTimeline}
						label={t('app.workbench.timeline.runLabel', 'Detailed run timeline')}
						onOpenArtifact={onOpenArtifact}
					/>
				) : (
					<EmptyState
						title={t('app.workbench.timeline.runEmptyTitle', 'No run yet')}
						body={t(
							'app.workbench.timeline.runEmptyBody',
							'Submit an autonomous intake to see its run progress here.',
						)}
					/>
				)}
			</Surface>

			<Surface title={t('app.workbench.timeline.deliveryTitle', 'Project delivery flow')} flat>
				{hasPipeline ? (
					<WorkflowTimeline
						variant="detailed"
						stages={deliveryTimeline}
						label={t('app.workbench.timeline.deliveryLabel', 'Project delivery phases')}
					/>
				) : (
					<EmptyState
						title={t('app.workbench.timeline.deliveryEmptyTitle', 'No delivery flow yet')}
						body={t(
							'app.workbench.timeline.deliveryEmptyBody',
							'Start a thread intake to create the delivery flow.',
						)}
					/>
				)}
			</Surface>

			<Surface title={t('app.workbench.timeline.runsTitle', 'Workflow runs')} flat>
				<DataTable
					rows={runs}
					empty={
						<EmptyState
							title={t('app.workbench.timeline.runsEmptyTitle', 'No workflow runs')}
							body={t(
								'app.workbench.timeline.runsEmptyBody',
								'Runs are recorded once autonomous workspace work executes.',
							)}
						/>
					}
					columns={[
						{
							key: 'workflow',
							label: t('app.workbench.timeline.colWorkflow', 'Workflow'),
							render: (row) => workflowTitle(row.workflowId),
						},
						{
							key: 'status',
							label: t('app.workbench.timeline.colStatus', 'Status'),
							render: (row) => <Badge tone={toneForStatus(String(row.status))}>{row.status}</Badge>,
						},
						{
							key: 'started',
							label: t('app.workbench.timeline.colStarted', 'Started'),
							render: (row) => (
								<span className="mono">
									{formatTime(
										row.startedAt,
										t('app.workbenchEvidence.notRecorded', 'not recorded'),
									)}
								</span>
							),
						},
					]}
				/>
			</Surface>

			<Surface title={t('app.workbench.timeline.eventsTitle', 'Workflow events')} flat>
				<DataTable
					rows={events}
					empty={
						<EmptyState
							title={t('app.workbench.timeline.eventsEmptyTitle', 'No workflow events')}
							body={t(
								'app.workbench.timeline.eventsEmptyBody',
								'Step and run events appear here as the workflow progresses.',
							)}
						/>
					}
					columns={[
						{
							key: 'type',
							label: t('app.workbench.timeline.colType', 'Type'),
							render: (row) => <span className="mono">{row.type}</span>,
						},
						{
							key: 'severity',
							label: t('app.workbench.timeline.colSeverity', 'Severity'),
							render: (row) => (
								<Badge tone={toneForStatus(String(row.severity))}>{row.severity}</Badge>
							),
						},
						{
							key: 'created',
							label: t('app.workbench.timeline.colWhen', 'When'),
							render: (row) => (
								<span className="mono">
									{formatTime(
										row.createdAt,
										t('app.workbenchEvidence.notRecorded', 'not recorded'),
									)}
								</span>
							),
						},
					]}
				/>
			</Surface>
		</div>
	);
}
