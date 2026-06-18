/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import type { IssueToPatchResponse } from '../../../api/client';
import type { Overview } from '../../../api/types';
import { Badge, DataTable, EmptyState, Surface } from '../../../components/primitives';
import { useI18n } from '../../../i18n/I18nProvider';
import { shortId, toneForStatus } from '../../../lib/format';
import { WorkflowTimeline } from '../WorkflowTimeline';
import { buildWorkflowTimeline } from '../timelineModel';
import type { TimelineArtifactRef } from '../timelineModel';
import { sortByTimeDesc } from '../workbenchSelectors';

type DeliveryRow = { id: string; label: string; owner: string; status: string };
type Signals = { workflows: number; evidence: number; approvals: number; workspaces: number };

function formatTime(value: string | null | undefined, emptyLabel: string) {
	if (!value) return emptyLabel;
	const parsed = Date.parse(value);
	return Number.isNaN(parsed) ? value : new Date(parsed).toLocaleString();
}

export function TimelinePanel({
	taskRunResult,
	isSubmittingTask,
	hasExecutableRuntime,
	deliveryRows,
	signals,
	workflows,
	workflowRuns,
	workflowEvents,
	pipelines,
	onOpenArtifact,
}: {
	taskRunResult: IssueToPatchResponse | null;
	isSubmittingTask: boolean;
	hasExecutableRuntime: boolean;
	deliveryRows: DeliveryRow[];
	signals: Signals;
	workflows: Overview['workflows'];
	workflowRuns: Overview['workflowRuns'];
	workflowEvents: Overview['workflowEvents'];
	pipelines: Overview['pipelines'];
	onOpenArtifact?: (artifact: TimelineArtifactRef) => void;
}) {
	const { t } = useI18n();
	const timeline = buildWorkflowTimeline(taskRunResult, isSubmittingTask, hasExecutableRuntime);
	const runs = sortByTimeDesc(workflowRuns).slice(0, 6);
	const events = sortByTimeDesc(workflowEvents).slice(0, 8);
	const workflowTitle = (workflowId: string) => workflows.find((workflow) => workflow.id === workflowId)?.title ?? shortId(workflowId);

	return (
		<div className="stack">
			<div className="signal-grid">
				<div className="signal-card"><span>{t('app.workbench.signals.workflows', 'Recent workflows')}</span><strong className="tnum">{signals.workflows}</strong></div>
				<div className="signal-card"><span>{t('app.workbench.signals.evidence', 'Evidence packages')}</span><strong className="tnum">{signals.evidence}</strong></div>
				<div className="signal-card"><span>{t('app.workbench.signals.approvals', 'Pending approvals')}</span><strong className="tnum">{signals.approvals}</strong></div>
				<div className="signal-card"><span>{t('app.workbench.signals.workspaces', 'Runtime workspaces')}</span><strong className="tnum">{signals.workspaces}</strong></div>
			</div>

			<Surface title={t('app.workbench.timeline.runTitle', 'Run timeline')} flat>
				<WorkflowTimeline variant="detailed" stages={timeline} label={t('app.workbench.timeline.runLabel', 'Detailed run timeline')} onOpenArtifact={onOpenArtifact} />
			</Surface>

			<Surface title={t('app.workbench.timeline.deliveryTitle', 'Project delivery flow')} flat>
				<ol className="delivery-rail">
					{deliveryRows.map((stage) => (
						<li className="delivery-step" key={stage.id}>
							<Badge tone={toneForStatus(stage.status)}>{stage.status}</Badge>
							<div>
								<strong>{stage.label}</strong>
								<span className="muted">{stage.owner}</span>
							</div>
						</li>
					))}
				</ol>
			</Surface>

			<Surface title={t('app.workbench.timeline.runsTitle', 'Workflow runs')} flat>
				<DataTable
					rows={runs}
					empty={<EmptyState title={t('app.workbench.timeline.runsEmptyTitle', 'No workflow runs')} body={t('app.workbench.timeline.runsEmptyBody', 'Runs are recorded once a governed task executes in this workspace.')} />}
					columns={[
						{ key: 'workflow', label: t('app.workbench.timeline.colWorkflow', 'Workflow'), render: (row) => workflowTitle(row.workflowId) },
						{ key: 'status', label: t('app.workbench.timeline.colStatus', 'Status'), render: (row) => <Badge tone={toneForStatus(String(row.status))}>{row.status}</Badge> },
						{ key: 'started', label: t('app.workbench.timeline.colStarted', 'Started'), render: (row) => <span className="mono">{formatTime(row.startedAt, t('app.workbenchEvidence.notRecorded', 'not recorded'))}</span> },
					]}
				/>
			</Surface>

			<Surface title={t('app.workbench.timeline.pipelinesTitle', 'Session pipelines')} flat>
				<DataTable
					rows={pipelines.slice(0, 5)}
					empty={<EmptyState title={t('app.workbench.timeline.pipelinesEmptyTitle', 'No intake pipelines')} body={t('app.workbench.timeline.pipelinesEmptyBody', 'Chat intake creates the first pipeline record for the selected work session.')} />}
					columns={[
						{ key: 'title', label: t('app.workbench.timeline.colPipeline', 'Pipeline'), render: (row) => String(row.title ?? '') },
						{ key: 'status', label: t('app.workbench.timeline.colStatus', 'Status'), render: (row) => <Badge tone={toneForStatus(String(row.status ?? ''))}>{String(row.status ?? '')}</Badge> },
					]}
				/>
			</Surface>

			<Surface title={t('app.workbench.timeline.eventsTitle', 'Workflow events')} flat>
				<DataTable
					rows={events}
					empty={<EmptyState title={t('app.workbench.timeline.eventsEmptyTitle', 'No workflow events')} body={t('app.workbench.timeline.eventsEmptyBody', 'Step and run events appear here as the workflow progresses.')} />}
					columns={[
						{ key: 'type', label: t('app.workbench.timeline.colType', 'Type'), render: (row) => <span className="mono">{row.type}</span> },
						{ key: 'severity', label: t('app.workbench.timeline.colSeverity', 'Severity'), render: (row) => <Badge tone={toneForStatus(String(row.severity))}>{row.severity}</Badge> },
						{ key: 'created', label: t('app.workbench.timeline.colWhen', 'When'), render: (row) => <span className="mono">{formatTime(row.createdAt, t('app.workbenchEvidence.notRecorded', 'not recorded'))}</span> },
					]}
				/>
			</Surface>
		</div>
	);
}
