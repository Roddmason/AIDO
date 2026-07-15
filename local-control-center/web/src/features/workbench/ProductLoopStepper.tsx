/**
 * Read-only overview of a project's product loop: the live FSM state badge plus a happy-path stepper
 * (done / current / upcoming) derived from the durable loop state, or a "start product loop" affordance
 * when none exists. Pure presentation: the page owns the fetch and the start mutation. The backend FSM
 * stays authoritative; the done/upcoming ranking here is only a visual hint.
 * @author Rodrigo Mason
 */

import type { ProjectProductLoopResponse } from '../../api/client';
import { StatusChip as Badge, Button, EmptyState, StatusDot } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { PRODUCT_LOOP_PHASES, PRODUCT_LOOP_STATE_ORDER } from './productLoopModel';

type ProductLoopRecord = ProjectProductLoopResponse['loops'][number];

type ProductLoopStepperProps = {
	loop: ProductLoopRecord | null;
	onStart: () => void;
	starting: boolean;
};

/** Renders the loop stepper, or the start affordance when the project has no loop yet. */
export function ProductLoopStepper({ loop, onStart, starting }: ProductLoopStepperProps) {
	const { t } = useI18n();

	if (!loop) {
		return (
			<EmptyState
				title={t('app.workbench.loop.noLoop', 'No product loop yet')}
				body={t(
					'app.workbench.loop.noLoopBody',
					'Start a product loop to track this idea from discovery to delivery.',
				)}
				action={
					<Button variant="primary" onClick={onStart} loading={starting}>
						{t('app.workbench.loop.start', 'Start product loop')}
					</Button>
				}
			/>
		);
	}

	const currentRank = PRODUCT_LOOP_STATE_ORDER.indexOf(
		loop.state as (typeof PRODUCT_LOOP_STATE_ORDER)[number],
	);
	const isBlocked = loop.state === 'blocked';

	return (
		<div className="stack">
			<div className="inline">
				<Badge tone={toneForStatus(loop.status)}>{loop.state}</Badge>
				<span className="muted mono">v{loop.version}</span>
			</div>
			<div className="inline">
				{PRODUCT_LOOP_PHASES.map((phase) => {
					const nodeRank = PRODUCT_LOOP_STATE_ORDER.indexOf(phase);
					const isCurrent = phase === loop.state;
					const isDone = !isBlocked && currentRank >= 0 && nodeRank < currentRank;
					return (
						<span className="inline" key={phase}>
							<StatusDot tone={isCurrent ? 'info' : isDone ? 'ok' : 'pending'} />
							<span className={isCurrent ? undefined : 'muted'}>{phase.replace(/_/g, ' ')}</span>
						</span>
					);
				})}
			</div>
		</div>
	);
}
