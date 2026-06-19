/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */

import type { LucideIcon } from 'lucide-react';
import {
	CheckCircle2,
	CircleDashed,
	Cpu,
	FileCode2,
	FilePlus2,
	FlaskConical,
	FolderGit2,
	GitPullRequest,
	ScrollText,
	XCircle,
} from 'lucide-react';

import type { IssueToPatchResponse } from '../../api/client';
import type { IssueTimelineEntry, TimelineStatus } from './workbenchSelectors';
import { buildIssueTimeline } from './workbenchSelectors';

/** A workbench-internal artifact a stage can link to (opened inside the workbench). */
export type TimelineArtifactRef = { kind: 'evidence'; id: string };

/** The seven leading run phases, in order. The eighth stage is the terminal verdict. */
export type TimelinePhase =
	| 'created'
	| 'workspace'
	| 'runtime'
	| 'editing'
	| 'qa'
	| 'evidence'
	| 'review'
	| 'terminal';

export type WorkflowTimelineStage = {
	/** Canonical stage id from buildIssueTimeline, kept visible as provenance. */
	id: string;
	phase: TimelinePhase;
	status: TimelineStatus;
	icon: LucideIcon;
	/** i18n key + English fallback for the phase label (e.g. "Runtime"). */
	labelKey: string;
	label: string;
	/** i18n key + English fallback for the human reason (what happened / what is missing). */
	reasonKey: string;
	reason: string;
	/** Optional artifact this stage produced (only when the stage actually has one). */
	artifact?: TimelineArtifactRef;
};

type PhaseDescriptor = {
	phase: Exclude<TimelinePhase, 'terminal'>;
	labelKey: string;
	label: string;
	icon: LucideIcon;
};

/** Fixed descriptors for the seven leading phases, indexed by issueTimelineOrder. */
const LEADING_PHASES: PhaseDescriptor[] = [
	{
		phase: 'created',
		labelKey: 'app.workbench.timeline.state.created',
		label: 'Created',
		icon: FilePlus2,
	},
	{
		phase: 'workspace',
		labelKey: 'app.workbench.timeline.state.workspace',
		label: 'Workspace',
		icon: FolderGit2,
	},
	{
		phase: 'runtime',
		labelKey: 'app.workbench.timeline.state.runtime',
		label: 'Runtime',
		icon: Cpu,
	},
	{
		phase: 'editing',
		labelKey: 'app.workbench.timeline.state.editing',
		label: 'Editing',
		icon: FileCode2,
	},
	{ phase: 'qa', labelKey: 'app.workbench.timeline.state.qa', label: 'QA', icon: FlaskConical },
	{
		phase: 'evidence',
		labelKey: 'app.workbench.timeline.state.evidence',
		label: 'Evidence',
		icon: ScrollText,
	},
	{
		phase: 'review',
		labelKey: 'app.workbench.timeline.state.review',
		label: 'Review',
		icon: GitPullRequest,
	},
];

type Reason = { key: string; text: string };
const FALLBACK_REASON: Record<TimelineStatus, string> = {
	done: 'Completed',
	active: 'In progress',
	pending: 'Not started yet',
	blocked: 'Blocked',
	failed: 'Failed',
};

/**
 * Human reason per (phase, status). Phrased so the operator understands what
 * happened and what is still missing — not the raw machine status.
 */
const REASONS: Record<TimelinePhase, Partial<Record<TimelineStatus, string>>> = {
	created: {
		done: 'Workflow run created',
		failed: 'Run could not be created',
		pending: 'Not started yet',
	},
	workspace: {
		done: 'Workspace allocated',
		failed: 'Workspace was not allocated',
		pending: 'Waiting for the workflow run',
	},
	runtime: {
		done: 'Executable runtime selected',
		blocked: 'No executable runtime is configured',
		failed: 'Runtime is unavailable',
		pending: 'Waiting for the run',
	},
	editing: {
		active: 'Applying changes through the runtime',
		done: 'Changes applied',
		failed: 'Run failed before editing finished',
		pending: 'Waiting for an executable runtime',
	},
	qa: { done: 'QA checks passed', failed: 'QA checks failed', pending: 'QA has not run yet' },
	evidence: {
		done: 'Evidence package ready',
		failed: 'Evidence package is missing',
		pending: 'Waiting for the artifact package',
	},
	review: {
		active: 'Awaiting human approval',
		done: 'Approved for integration',
		blocked: 'Review is blocked',
		pending: 'Waiting for evidence',
	},
	terminal: {
		done: 'Delivery completed',
		failed: 'Delivery failed',
		blocked: 'Delivery blocked',
		pending: 'No terminal verdict yet',
	},
};

function reasonFor(phase: TimelinePhase, status: TimelineStatus): Reason {
	const text = REASONS[phase][status] ?? FALLBACK_REASON[status];
	return { key: `app.workbench.timeline.reason.${phase}.${status}`, text };
}

/** Picks the terminal phase label and icon from the resolved status. */
function terminalPresentation(status: TimelineStatus): {
	labelKey: string;
	label: string;
	icon: LucideIcon;
} {
	if (status === 'done')
		return { labelKey: 'app.workbench.timeline.state.done', label: 'Done', icon: CheckCircle2 };
	if (status === 'failed' || status === 'blocked')
		return { labelKey: 'app.workbench.timeline.state.failed', label: 'Failed', icon: XCircle };
	return {
		labelKey: 'app.workbench.timeline.state.terminal',
		label: 'Done / Failed',
		icon: CircleDashed,
	};
}

function toStage(entry: IssueTimelineEntry, index: number): WorkflowTimelineStage {
	const descriptor = LEADING_PHASES[index];
	if (descriptor) {
		const reason = reasonFor(descriptor.phase, entry.status);
		const artifact: TimelineArtifactRef | undefined =
			descriptor.phase === 'evidence' && entry.status === 'done' && entry.detail
				? { kind: 'evidence', id: entry.detail }
				: undefined;
		return {
			id: entry.id,
			phase: descriptor.phase,
			status: entry.status,
			icon: descriptor.icon,
			labelKey: descriptor.labelKey,
			label: descriptor.label,
			reasonKey: reason.key,
			reason: reason.text,
			artifact,
		};
	}
	const terminal = terminalPresentation(entry.status);
	const reason = reasonFor('terminal', entry.status);
	return {
		id: entry.id,
		phase: 'terminal',
		status: entry.status,
		icon: terminal.icon,
		labelKey: terminal.labelKey,
		label: terminal.label,
		reasonKey: reason.key,
		reason: reason.text,
	};
}

/**
 * Builds the operator-facing run timeline from an issue_to_patch response.
 * Delegates the staged status derivation to `buildIssueTimeline` so behavior
 * stays in a single, already-tested place.
 */
export function buildWorkflowTimeline(
	result: IssueToPatchResponse | null,
	isSubmittingTask: boolean,
	hasExecutableRuntime: boolean,
): WorkflowTimelineStage[] {
	return buildIssueTimeline(result, isSubmittingTask, hasExecutableRuntime).map(toStage);
}

/**
 * Index of the stage the operator should focus on: the active/failed/blocked one,
 * else the last completed, else the first stage (a fresh run that has not started
 * focuses "Created", not the terminal verdict). Returns -1 only for an empty list.
 */
export function currentStageIndex(stages: WorkflowTimelineStage[]): number {
	const inFlight = stages.findIndex(
		(stage) => stage.status === 'active' || stage.status === 'failed' || stage.status === 'blocked',
	);
	if (inFlight !== -1) return inFlight;
	let lastDone = -1;
	stages.forEach((stage, index) => {
		if (stage.status === 'done') lastDone = index;
	});
	if (lastDone !== -1) return lastDone;
	return stages.length ? 0 : -1;
}
