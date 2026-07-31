/**
 * Pure presentation model for the Workbench product loop: the ordered sections the operator
 * navigates (conversation → review) and a per-section view (label key + tab count). Holds no
 * React and no I/O so the page stays a thin shell and this stays unit-testable, mirroring
 * timelineModel.ts.
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
};

/** The ten loop sections, in flow order. */
export const PRODUCT_LOOP_SECTIONS: ProductLoopSectionBlueprint[] = [
	{ id: 'conversation', labelKey: 'app.workbench.loop.conversation', label: 'Conversation' },
	{ id: 'questions', labelKey: 'app.workbench.loop.questions', label: 'Questions' },
	{ id: 'brief', labelKey: 'app.workbench.loop.brief', label: 'Product brief' },
	{ id: 'assumptions', labelKey: 'app.workbench.loop.assumptions', label: 'Assumptions' },
	{ id: 'decisions', labelKey: 'app.workbench.loop.decisions', label: 'Decisions' },
	{ id: 'architecture', labelKey: 'app.workbench.loop.architecture', label: 'Architecture' },
	{ id: 'backlog', labelKey: 'app.workbench.loop.backlog', label: 'Backlog' },
	{ id: 'iteration', labelKey: 'app.workbench.loop.iteration', label: 'Iteration' },
	{ id: 'execution', labelKey: 'app.workbench.loop.execution', label: 'Execution' },
	{ id: 'review', labelKey: 'app.workbench.loop.review', label: 'Review' },
];

/** Full FSM state order, covering every backend coordinator.PRODUCT_LOOP_STATES value so a live
 *  state always resolves to a rank (a missing state used to fall to indexOf === -1, which silently
 *  rendered the whole stepper as "upcoming"). Used only to rank a loop's progress
 *  (done/current/upcoming) in the read-only stepper; the happy-path phases in PRODUCT_LOOP_PHASES keep
 *  their intended relative order (iteration planning shown before execution). The backend FSM stays
 *  the authority on which transitions are allowed; this list never gates a mutation. A Python gate
 *  (test_product_loop_state_order_covers_backend_states) fails if the backend adds a state not listed
 *  here. */
export const PRODUCT_LOOP_STATE_ORDER = [
	'goal_received',
	'workspace_check',
	'runtime_check',
	'git_check',
	'discovery',
	'discovering',
	'awaiting_user',
	'planning',
	'brief_ready',
	'architecture_review',
	'backlog_ready',
	'iteration_planning',
	'branch_ready',
	'executing',
	'qa_running',
	'security_running',
	'quality_review',
	'review_ready',
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
