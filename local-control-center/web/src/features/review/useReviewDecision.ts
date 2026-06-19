/**
 * Hook that owns the approve/reject decision for one action request: resolves its
 * linked evidence/artifacts, lazily loads the patch and security payloads, derives
 * the evidence gate, and submits the decision (chaining the workflow-approval call
 * when the action approves a patch/PR run). All decision state lives here.
 */
import { useEffect, useMemo, useState } from 'react';
import type { ArtifactPayload } from '../../api/client';
import {
	approveAction,
	approveIssueToPatch,
	approveIssueToPr,
	denyAction,
	fetchEvidenceArtifact,
} from '../../api/client';
import type { ActionRequest, Artifact, Overview } from '../../api/types';
import { findPatchArtifact } from '../../lib/diff';
import {
	evidenceCompleteness,
	findSecurityFindingsArtifact,
	linkedArtifacts,
	linkedEvidence,
	patchWorkflowApproval,
	requiresPatchEvidenceGate,
} from './model';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;

export type ReviewDecision = {
	artifacts: Artifact[];
	evidence: Overview['evidencePackages'];
	patchArtifact: Artifact | null;
	securityArtifact: Artifact | null;
	patchPayload: ArtifactPayload | null;
	securityPayload: ArtifactPayload | null;
	patchLoading: boolean;
	securityLoading: boolean;
	patchError: string;
	securityError: string;
	patchGate: ReturnType<typeof evidenceCompleteness> | null;
	decisionReason: string;
	setDecisionReason: (value: string) => void;
	decisionError: string;
	setDecisionError: (value: string) => void;
	decisionBlocked: boolean;
	approveBlocked: boolean;
	requiresGate: boolean;
	decide: (kind: 'approve' | 'reject') => Promise<boolean>;
};

/**
 * Wires the decision state for the currently selected action. Returns the loaded
 * evidence/gate plus `approveBlocked`/`decisionBlocked` flags and a `decide`
 * submitter; Approve stays blocked until the reason and the patch gate both pass.
 */
export function useReviewDecision(
	action: ActionRequest | null,
	overview: Overview,
	token: string,
	mutate: Mutate,
): ReviewDecision {
	const [decisionReason, setDecisionReason] = useState('');
	const [decisionError, setDecisionError] = useState('');
	const [patchPayload, setPatchPayload] = useState<ArtifactPayload | null>(null);
	const [patchLoadingId, setPatchLoadingId] = useState('');
	const [patchError, setPatchError] = useState('');
	const [securityPayload, setSecurityPayload] = useState<ArtifactPayload | null>(null);
	const [securityLoadingId, setSecurityLoadingId] = useState('');
	const [securityError, setSecurityError] = useState('');

	const artifacts = useMemo(
		() => (action ? linkedArtifacts(action, overview) : []),
		[overview, action],
	);
	const evidence = useMemo(
		() => (action ? linkedEvidence(action, overview) : []),
		[overview, action],
	);
	const patchArtifact = useMemo(() => findPatchArtifact(artifacts), [artifacts]);
	const securityArtifact = useMemo(() => findSecurityFindingsArtifact(artifacts), [artifacts]);

	const patchGate = useMemo(
		() =>
			action
				? evidenceCompleteness({
						action,
						artifacts,
						diffError: patchError,
						diffLoading: Boolean(patchLoadingId),
						diffPayload: patchPayload,
						evidence,
						securityError,
						securityLoading: Boolean(securityLoadingId),
						securityPayload,
					})
				: null,
		[
			action,
			artifacts,
			evidence,
			patchError,
			patchLoadingId,
			patchPayload,
			securityError,
			securityLoadingId,
			securityPayload,
		],
	);

	// Reset the reason whenever the reviewed action changes.
	useEffect(() => {
		setDecisionReason('');
		setDecisionError('');
	}, [action?.id]);

	// Lazily load the patch artifact through the protected evidence endpoint.
	useEffect(() => {
		setPatchPayload(null);
		setPatchError('');
		if (!action || !patchArtifact || !requiresPatchEvidenceGate(action)) {
			setPatchLoadingId('');
			return undefined;
		}
		const artifactId = String(patchArtifact.id ?? '');
		const evidenceId = String(patchArtifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setPatchError('Patch artifact metadata is incomplete.');
			return undefined;
		}
		let cancelled = false;
		setPatchLoadingId(artifactId);
		void fetchEvidenceArtifact(token, evidenceId, artifactId)
			.then((payload) => {
				if (!cancelled) setPatchPayload(payload);
			})
			.catch((error) => {
				if (!cancelled)
					setPatchError(error instanceof Error ? error.message : 'Patch artifact preview failed.');
			})
			.finally(() => {
				if (!cancelled) setPatchLoadingId('');
			});
		return () => {
			cancelled = true;
		};
	}, [action, patchArtifact, token]);

	// Lazily load the security findings artifact.
	useEffect(() => {
		setSecurityPayload(null);
		setSecurityError('');
		if (!action || !securityArtifact || !requiresPatchEvidenceGate(action)) {
			setSecurityLoadingId('');
			return undefined;
		}
		const artifactId = String(securityArtifact.id ?? '');
		const evidenceId = String(securityArtifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setSecurityError('Security findings artifact metadata is incomplete.');
			return undefined;
		}
		let cancelled = false;
		setSecurityLoadingId(artifactId);
		void fetchEvidenceArtifact(token, evidenceId, artifactId)
			.then((payload) => {
				if (!cancelled) setSecurityPayload(payload);
			})
			.catch((error) => {
				if (!cancelled)
					setSecurityError(
						error instanceof Error ? error.message : 'Security findings preview failed.',
					);
			})
			.finally(() => {
				if (!cancelled) setSecurityLoadingId('');
			});
		return () => {
			cancelled = true;
		};
	}, [action, securityArtifact, token]);

	const trimmedReason = decisionReason.trim();
	const decisionBlocked = !trimmedReason;
	const approveBlocked = decisionBlocked || Boolean(patchGate?.required && !patchGate.complete);

	const decide = async (kind: 'approve' | 'reject'): Promise<boolean> => {
		if (!action) return false;
		if (!trimmedReason) {
			setDecisionError('A human reason is required before this request can be decided.');
			return false;
		}
		if (kind === 'approve' && patchGate?.required && !patchGate.complete) {
			setDecisionError(
				'Complete linked evidence, a readable non-empty patch artifact, passing QA evidence, and non-blocking security findings are required before approve patch.',
			);
			return false;
		}
		setDecisionError('');
		try {
			await mutate(async (writeToken) => {
				if (kind === 'reject') {
					return denyAction(writeToken, action.jobId, action.id, trimmedReason);
				}
				const approved = await approveAction(writeToken, action.jobId, action.id, trimmedReason);
				const workflowApproval = patchWorkflowApproval(action);
				if (workflowApproval?.kind === 'issue_to_patch') {
					return approveIssueToPatch(writeToken, workflowApproval.runId, trimmedReason);
				}
				if (workflowApproval?.kind === 'issue_to_pr') {
					return approveIssueToPr(writeToken, workflowApproval.runId, trimmedReason);
				}
				return approved;
			});
			return true;
		} catch (error) {
			setDecisionError(error instanceof Error ? error.message : 'Decision failed.');
			return false;
		}
	};

	return {
		artifacts,
		evidence,
		patchArtifact,
		securityArtifact,
		patchPayload,
		securityPayload,
		patchLoading: Boolean(patchLoadingId),
		securityLoading: Boolean(securityLoadingId),
		patchError,
		securityError,
		patchGate,
		decisionReason,
		setDecisionReason,
		decisionError,
		setDecisionError,
		decisionBlocked,
		approveBlocked,
		requiresGate: action ? requiresPatchEvidenceGate(action) : false,
		decide,
	};
}
