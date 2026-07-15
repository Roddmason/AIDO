/**
 * Renders a workflow run's merged timeline (steps + workflow events + audit events) as an ordered
 * list. The items are derived upstream by `buildWorkflowTimeline`; this is purely presentational.
 * Named distinctly from the workbench `WorkflowTimeline` (a run-stage rail) to avoid confusion.
 * @author Rodrigo Mason
 */
import { StatusChip as Badge, EmptyState, StatusDot } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import type { WorkflowTimelineItem } from './workflowLinks';

export function WorkflowRunTimeline({ items }: { items: WorkflowTimelineItem[] }) {
	const { t } = useI18n();
	if (!items.length) {
		return (
			<EmptyState
				title={t('ui.static.no.workflow.timeline.workflows.2d8544e4', 'No workflow timeline')}
				body={t(
					'ui.static.run.linked.workflow.events.and.step.evidence.have.not.been.recorded.6ad61027',
					'Run-linked workflow events and step evidence have not been recorded.',
				)}
			/>
		);
	}
	return (
		<ol
			className="workflow-timeline"
			aria-label={t('ui.static.workflow.timeline.c2b65038', 'Workflow timeline')}
		>
			{items.map((item) => (
				<li className="workflow-timeline-item" key={item.id}>
					<StatusDot tone={item.tone} />
					<div className="workflow-timeline-content">
						<div className="workflow-timeline-heading">
							<strong>{item.label}</strong>
							<Badge tone={item.tone}>{item.status}</Badge>
						</div>
						<p className="workflow-timeline-detail">{item.detail}</p>
						<div className="workflow-timeline-meta">
							<span className="mono">{item.source}</span>
							<span className="mono">
								{item.createdAt
									? new Date(item.createdAt).toLocaleString()
									: t('app.workflows.timeUnavailable', 'time unavailable')}
							</span>
						</div>
					</div>
				</li>
			))}
		</ol>
	);
}
