/**
 * Loops tab of the shell sidebar: the selected workspace's product loops as a status list.
 *
 * Each row shows the loop title, its current phase (humanized FSM state), a status badge and a compact
 * phase-progress bar over the happy-path phases — a product-lifecycle view that is AIDO's own, not a
 * generic chat log. Read-only by design: a loop's full detail and FSM transitions live in the Workbench
 * center, and per-loop selection is not wired yet, so the rows are an honest status display rather than
 * a no-op click target. Data is real (useProductLoop); loading/empty/error are rendered honestly and no
 * value is fabricated (a missing status reads `unknown`, never an implied `active`).
 */
import { Badge, EmptyState } from '../../components/primitives';
import { ErrorState, Skeleton } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { PRODUCT_LOOP_PHASES } from '../workbench/productLoopModel';
import { useProductLoop } from '../workbench/useProductLoop';

// Index of each happy-path phase, used to fill the progress bar up to the loop's current phase.
const PHASE_INDEX = new Map<string, number>(
	PRODUCT_LOOP_PHASES.map((state, index) => [state, index]),
);

/** Turns an FSM state id (e.g. `iteration_planning`) into a readable phase label. The value comes
 *  from backend data, not a static literal, so it is not subject to the i18n catalog gate. */
function humanizeState(state: string): string {
	const spaced = state.replace(/_/g, ' ');
	return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Renders the phase-progress bar: one segment per happy-path phase, filled up to the current phase.
 *  Off-path states (awaiting_user, reworking, blocked, cancelled) have no index, so no fill — their
 *  meaning is carried by the status badge instead. The accessible name states the numeric progress. */
function PhaseBar({ state, label }: { state: string; label: string }) {
	const total = PRODUCT_LOOP_PHASES.length;
	const current = PHASE_INDEX.get(state) ?? -1;
	const ariaLabel = current >= 0 ? `${label}: ${current + 1}/${total}` : label;
	return (
		<div className="loop-phase-bar" role="img" aria-label={ariaLabel}>
			{PRODUCT_LOOP_PHASES.map((phase, index) => (
				<span
					key={phase}
					className={index <= current ? 'loop-phase-seg is-filled' : 'loop-phase-seg'}
				/>
			))}
		</div>
	);
}

/** The Loops tab body: the project's product loops with phase + status, or an honest empty/loading/error. */
export function LoopList({ projectId, filter }: { projectId: string | undefined; filter: string }) {
	const { t } = useI18n();
	const loop = useProductLoop(projectId);

	if (!projectId) {
		return (
			<EmptyState
				title={t('app.shell.loops.noProjectTitle', 'No workspace selected')}
				body={t('app.shell.loops.noProjectBody', 'Pick a workspace in Threads to see its loops.')}
			/>
		);
	}
	if (loop.loading && !loop.data) {
		return (
			<div className="stack compact" aria-busy="true">
				<Skeleton className="h-12" />
				<Skeleton className="h-12" />
			</div>
		);
	}
	if (loop.error) {
		return (
			<ErrorState
				title={t('app.shell.loops.errorTitle', 'Could not load loops')}
				body={loop.error}
			/>
		);
	}

	const needle = filter.trim().toLowerCase();
	const loops = (loop.data?.loops ?? []).filter(
		(item) =>
			!needle ||
			String(item.title ?? '')
				.toLowerCase()
				.includes(needle),
	);

	if (!loops.length) {
		return (
			<EmptyState
				title={t('app.shell.loops.emptyTitle', 'No active loops')}
				body={t(
					'app.shell.loops.emptyBody',
					'Start a product loop from the composer to track its phase here.',
				)}
			/>
		);
	}

	return (
		<ul className="loop-list" aria-label={t('app.shell.loops.listLabel', 'Product loops')}>
			{loops.map((item) => {
				const state = String(item.state ?? 'goal_received');
				const status = String(item.status ?? 'unknown');
				return (
					<li key={item.id} className="loop-row">
						<span className="loop-row-head">
							<strong className="loop-row-title">
								{item.title || t('app.shell.loops.untitled', 'Untitled loop')}
							</strong>
							<Badge tone={toneForStatus(status)}>{status}</Badge>
						</span>
						<span className="loop-row-phase">{humanizeState(state)}</span>
						<PhaseBar state={state} label={t('app.shell.loops.phaseProgress', 'Phase progress')} />
					</li>
				);
			})}
		</ul>
	);
}
