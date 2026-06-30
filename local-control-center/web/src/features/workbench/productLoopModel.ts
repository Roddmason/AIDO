/**
 * Pure presentation model for the Workbench product loop: the ordered sections the operator
 * navigates (conversation → review) and a per-section view (label key + tab count). Holds no
 * React and no I/O so the page stays a thin shell and this stays unit-testable, mirroring
 * timelineModel.ts. The `backed` flag records which sections render live project data.
 * @author Rodrigo Mason
 */

/** The ten loop stages, identified in flow order. */
export type ProductLoopSectionId =
	| 'conversation'
	| 'questions'
	| 'brief'
	| 'assumptions'
	| 'decisions'
	| 'architecture'
	| 'backlog'
	| 'iteration'
	| 'execution'
	| 'review';

export type ProductLoopSectionBlueprint = {
	id: ProductLoopSectionId;
	/** i18n key + English fallback for the tab label (registered in the bilingual catalog). */
	labelKey: string;
	label: string;
	/** True when the section renders live project data instead of a placeholder shell. */
	backed: boolean;
};

/** The ten loop sections, in flow order. `backed` marks views wired to live project data. */
export const PRODUCT_LOOP_SECTIONS: ProductLoopSectionBlueprint[] = [
	{
		id: 'conversation',
		labelKey: 'app.workbench.loop.conversation',
		label: 'Conversation',
		backed: true,
	},
	{ id: 'questions', labelKey: 'app.workbench.loop.questions', label: 'Questions', backed: true },
	{ id: 'brief', labelKey: 'app.workbench.loop.brief', label: 'Product brief', backed: true },
	{
		id: 'assumptions',
		labelKey: 'app.workbench.loop.assumptions',
		label: 'Assumptions',
		backed: true,
	},
	{ id: 'decisions', labelKey: 'app.workbench.loop.decisions', label: 'Decisions', backed: true },
	{
		id: 'architecture',
		labelKey: 'app.workbench.loop.architecture',
		label: 'Architecture',
		backed: false,
	},
	{ id: 'backlog', labelKey: 'app.workbench.loop.backlog', label: 'Backlog', backed: true },
	{ id: 'iteration', labelKey: 'app.workbench.loop.iteration', label: 'Iteration', backed: true },
	{ id: 'execution', labelKey: 'app.workbench.loop.execution', label: 'Execution', backed: true },
	{ id: 'review', labelKey: 'app.workbench.loop.review', label: 'Review', backed: true },
];

/** Full FSM state order, mirroring the backend coordinator.PRODUCT_LOOP_STATES, used only to rank a
 *  loop's progress (done/current/upcoming) in the read-only stepper. The backend FSM stays the
 *  authority on which transitions are allowed; this list never gates a mutation. */
export const PRODUCT_LOOP_STATE_ORDER = [
	'goal_received',
	'discovering',
	'awaiting_user',
	'brief_ready',
	'architecture_review',
	'backlog_ready',
	'iteration_planning',
	'executing',
	'quality_review',
	'awaiting_approval',
	'awaiting_feedback',
	'reworking',
	'delivered',
	'blocked',
	'cancelled',
] as const;

/** The happy-path phases shown as stepper nodes, in order; off-path states (awaiting_user,
 *  reworking, blocked, cancelled) surface via the live state badge instead of as their own nodes. */
export const PRODUCT_LOOP_PHASES = [
	'goal_received',
	'discovering',
	'brief_ready',
	'architecture_review',
	'backlog_ready',
	'iteration_planning',
	'executing',
	'quality_review',
	'awaiting_approval',
	'delivered',
] as const;

export type ProductLoopSectionView = ProductLoopSectionBlueprint & { count: number };

/** Per-section item counts surfaced as tab badges; sections without a backend stay at 0. */
export type ProductLoopCounts = Partial<Record<ProductLoopSectionId, number>>;

/** Maps the blueprint plus live counts into the view list the tablist renders. */
export function buildProductLoopSections(counts: ProductLoopCounts): ProductLoopSectionView[] {
	return PRODUCT_LOOP_SECTIONS.map((section) => ({
		...section,
		count: Math.max(0, Math.trunc(counts[section.id] ?? 0)),
	}));
}
