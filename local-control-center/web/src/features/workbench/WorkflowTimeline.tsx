/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { ArrowUpRight } from 'lucide-react';

import { useI18n } from '../../i18n/I18nProvider';
import { currentStageIndex } from './timelineModel';
import type { TimelineArtifactRef, WorkflowTimelineStage } from './timelineModel';

type WorkflowTimelineProps = {
	stages: WorkflowTimelineStage[];
	/** Already-translated accessible name for the timeline list. */
	label: string;
	variant?: 'rail' | 'detailed';
	onOpenArtifact?: (artifact: TimelineArtifactRef) => void;
};

export function WorkflowTimeline({ stages, label, variant = 'detailed', onOpenArtifact }: WorkflowTimelineProps) {
	const { t } = useI18n();
	const current = currentStageIndex(stages);
	const isRail = variant === 'rail';

	const list = (
		<ol className="run-flow" data-variant={variant} aria-label={label}>
			{stages.map((stage, index) => {
				const state = t(stage.labelKey, stage.label);
				const reason = t(stage.reasonKey, stage.reason);
				const Icon = stage.icon;
				return (
					<li
						className="run-flow-step"
						key={`${stage.id}-${index}`}
						data-status={stage.status}
						data-phase={stage.phase}
						aria-current={index === current ? 'step' : undefined}
						aria-label={isRail ? `${state}: ${reason}` : undefined}
						title={isRail ? `${state} — ${reason}` : undefined}
					>
						<span className="run-flow-node" data-status={stage.status}>
							<Icon size={16} aria-hidden="true" />
						</span>
						<div className="run-flow-body">
							<div className="run-flow-head">
								<span className="run-flow-state">{state}</span>
								<span className="run-flow-id mono" aria-hidden="true">
									{stage.id}
								</span>
							</div>
							{isRail ? null : <p className="run-flow-reason">{reason}</p>}
							{!isRail && stage.artifact ? (
								<button className="run-flow-link" type="button" onClick={() => onOpenArtifact?.(stage.artifact as TimelineArtifactRef)}>
									<ArrowUpRight size={14} aria-hidden="true" />
									{t('app.workbench.timeline.viewEvidence', 'View evidence')}
								</button>
							) : null}
						</div>
					</li>
				);
			})}
		</ol>
	);

	if (!isRail) return list;

	const focus = stages[current] ?? stages[stages.length - 1];
	return (
		<div className="run-flow-rail-shell">
			{list}
			{focus ? (
				<p className="run-flow-rail-current" data-status={focus.status}>
					<span className="run-flow-rail-state">{t(focus.labelKey, focus.label)}</span>
					<span className="run-flow-rail-reason">{t(focus.reasonKey, focus.reason)}</span>
				</p>
			) : null}
		</div>
	);
}
