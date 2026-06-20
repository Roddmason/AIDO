/**
 * Hook encapsulating the post-approval ship lifecycle (promote a patch run to a
 * branch, then open a PR). Holds the reason and optional branch/PR fields, runs
 * the kind-aware endpoint, surfaces the last operation, and refreshes on success.
 */
import { useState } from 'react';

import {
	createPullRequestFromIssueToPr,
	createPullRequestFromPromotedBranch,
	promoteIssueToPrBranch,
	promotePatchToBranch,
} from '../../api/client';
import type { PatchWorkflowKind } from './model';
import { asRecord } from './model';

type Refresh = (silent?: boolean) => Promise<void>;
export type ShipOperation = 'promote' | 'pull-request';

/**
 * Shared promote/PR ship lifecycle for a reviewed patch-workflow run. The Review
 * board consumes this so the status-gated, reason-gated branch-promotion and
 * PR-creation contract has a single implementation (the retired Jobs & Approvals
 * queue used the same hook). The reason stays mandatory; status guards live at the
 * call sites (a run is only promotable / PR-able in the right state).
 */
export type ShipOperations = {
	reason: string;
	setReason: (value: string) => void;
	branchName: string;
	setBranchName: (value: string) => void;
	pullRequestTitle: string;
	setPullRequestTitle: (value: string) => void;
	pullRequestBaseBranch: string;
	setPullRequestBaseBranch: (value: string) => void;
	error: string;
	setError: (value: string) => void;
	busyId: string;
	reasonRecorded: boolean;
	lastOperation: { status: string; reason: string; runId: string } | null;
	run: (operation: ShipOperation, runId: string, kind: PatchWorkflowKind) => Promise<void>;
	reset: () => void;
};

/**
 * Builds the ship-operation state for one run. `run` is the single entry point —
 * it requires a non-empty reason, dispatches promote vs PR by `kind`, and records
 * the result; `reset` clears the form when the drawer closes or reopens.
 */
export function useShipOperations(
	token: string,
	refresh: Refresh,
	// Single, page-owned reason shared with the decision flow (inherited from the approval).
	reason: string,
	setReason: (value: string) => void,
): ShipOperations {
	const [branchName, setBranchName] = useState('');
	const [pullRequestTitle, setPullRequestTitle] = useState('');
	const [pullRequestBaseBranch, setPullRequestBaseBranch] = useState('');
	const [error, setError] = useState('');
	const [busyId, setBusyId] = useState('');
	const [lastOperation, setLastOperation] = useState<{
		status: string;
		reason: string;
		runId: string;
	} | null>(null);

	const trimmedReason = reason.trim();

	const recordOperation = (result: unknown, fallbackStatus: string) => {
		const record = asRecord(result);
		const workflowRun = asRecord(record.workflowRun);
		setLastOperation({
			status: String(record.status ?? workflowRun.status ?? fallbackStatus),
			reason: String(record.reason ?? ''),
			runId: String(workflowRun.id ?? ''),
		});
	};

	const run = async (operation: ShipOperation, runId: string, kind: PatchWorkflowKind) => {
		if (!trimmedReason) {
			setError('Workflow operation reason is required.');
			return;
		}
		setError('');
		setBusyId(`${operation}:${kind}:${runId}`);
		try {
			if (operation === 'promote') {
				const branch = branchName.trim();
				const body = {
					reason: trimmedReason,
					...(branch ? { branchName: branch } : {}),
				};
				const result =
					kind === 'issue_to_pr'
						? await promoteIssueToPrBranch(token, runId, body)
						: await promotePatchToBranch(token, runId, body);
				recordOperation(result, 'promoted_to_branch');
			} else {
				const title = pullRequestTitle.trim();
				const baseBranch = pullRequestBaseBranch.trim();
				const body = {
					reason: trimmedReason,
					...(title ? { title } : {}),
					...(baseBranch ? { baseBranch } : {}),
				};
				const result =
					kind === 'issue_to_pr'
						? await createPullRequestFromIssueToPr(token, runId, body)
						: await createPullRequestFromPromotedBranch(token, runId, body);
				recordOperation(result, 'pull_request_requested');
			}
			void refresh(true).catch(() => undefined);
		} catch (caught) {
			setError(caught instanceof Error ? caught.message : 'Workflow operation failed.');
		} finally {
			setBusyId('');
		}
	};

	// Clears the ship form (branch/PR/error/result). The reason is page-owned (so it carries
	// from the approval into the ship flow) and is reset there per item, not here.
	const reset = () => {
		setBranchName('');
		setPullRequestTitle('');
		setPullRequestBaseBranch('');
		setError('');
		setBusyId('');
		setLastOperation(null);
	};

	return {
		reason,
		setReason,
		branchName,
		setBranchName,
		pullRequestTitle,
		setPullRequestTitle,
		pullRequestBaseBranch,
		setPullRequestBaseBranch,
		error,
		setError,
		busyId,
		reasonRecorded: Boolean(trimmedReason),
		lastOperation,
		run,
		reset,
	};
}
