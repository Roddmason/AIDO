/**
 * Run-scoped inspector body shown inside the shell Inspector when a workflow run is selected
 * (deep link `#workflows?run=<id>`). Joins every record linked to the run via `linksForRun`
 * and presents them across six tabs — Overview, Timeline, Agents, Evidence, Artifacts, Policy —
 * reusing the same primitives and evidence derivations the Workflows page uses, so a run is a
 * first-class, linkable object instead of a transient drawer selection.
 * @author Rodrigo Mason
 */
import { useEffect, useMemo, useRef, useState } from 'react';

import {
	type ArtifactPayload,
	cancelJob,
	downloadEvidenceArtifact,
	fetchEvidenceArtifact,
	retryJob,
} from '../../api/client';
import type { Artifact, Overview } from '../../api/types';
import {
	StatusChip as Badge,
	DataTable,
	Drawer,
	EmptyState,
	Surface,
	Tabs,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { artifactDisplayName, artifactMimeType, artifactSizeLabel } from '../../lib/artifacts';
import { findPatchArtifact } from '../../lib/diff';
import { shortId, toneForStatus } from '../../lib/format';
import { redactVisibleText } from '../../lib/redaction';
import { WorkflowRunTimeline } from './WorkflowRunTimeline';
import {
	buildWorkflowTimeline,
	completionMissing,
	costLabel,
	linksForRun,
	objectValue,
	tokenLabel,
	workflowBlockers,
	workflowPullRequestUrls,
} from './workflowLinks';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;

type RunDetailTab = 'overview' | 'timeline' | 'agents' | 'evidence' | 'artifacts' | 'policy';

/**
 * Inspector body for a single workflow run. Owns the active tab, the artifact preview/download
 * state and the gated queue-recovery flow (retry/cancel require a non-empty reason). All linked
 * records are derived from `runId` via `linksForRun`.
 */
export function RunDetail({
	overview,
	runId,
	token,
	mutate,
}: {
	overview: Overview;
	runId: string;
	token: string;
	mutate: Mutate;
}) {
	const { t } = useI18n();
	const [activeTab, setActiveTab] = useState<RunDetailTab>('overview');
	const [previewArtifact, setPreviewArtifact] = useState<Artifact | null>(null);
	const [previewPayload, setPreviewPayload] = useState<ArtifactPayload | null>(null);
	const [previewLoadingId, setPreviewLoadingId] = useState('');
	const [downloadLoadingId, setDownloadLoadingId] = useState('');
	const [previewError, setPreviewError] = useState('');
	const previewRequestRef = useRef(0);
	const [jobMutationReason, setJobMutationReason] = useState('');
	const [jobMutationBusyId, setJobMutationBusyId] = useState('');
	const [jobMutationError, setJobMutationError] = useState('');
	const headingRef = useRef<HTMLHeadingElement>(null);

	const linked = useMemo(() => linksForRun(overview, runId), [overview, runId]);
	const run = linked.runs[0];
	const workflow = useMemo(
		() => overview.workflows.find((item) => item.id === String(run?.workflowId ?? '')),
		[overview.workflows, run],
	);
	const timelineItems = useMemo(() => buildWorkflowTimeline(linked, t), [linked, t]);
	const missingForCompleted = useMemo(() => completionMissing(linked, t), [linked, t]);
	const blockers = useMemo(() => workflowBlockers(linked, t), [linked, t]);
	const patchArtifact = useMemo(() => findPatchArtifact(linked.artifacts), [linked.artifacts]);
	const pullRequestUrls = useMemo(() => workflowPullRequestUrls(linked), [linked]);

	useEffect(() => {
		headingRef.current?.focus();
	}, []);

	// biome-ignore lint/correctness/useExhaustiveDependencies: runId is the intended reset trigger.
	useEffect(() => {
		setActiveTab('overview');
		setPreviewArtifact(null);
		setPreviewPayload(null);
		setPreviewError('');
		setJobMutationReason('');
		setJobMutationError('');
	}, [runId]);

	useEffect(() => {
		if (!previewArtifact) return undefined;
		const onKeyDown = (event: KeyboardEvent) => {
			if (event.key === 'Escape') {
				setPreviewArtifact(null);
				setPreviewPayload(null);
				setPreviewError('');
			}
		};
		window.addEventListener('keydown', onKeyDown);
		return () => window.removeEventListener('keydown', onKeyDown);
	}, [previewArtifact]);

	const openPreview = async (artifact: Artifact) => {
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setPreviewError(t('app.workbench.evidence.incomplete', 'Artifact metadata is incomplete.'));
			return;
		}
		const generation = ++previewRequestRef.current;
		setPreviewArtifact(artifact);
		setPreviewPayload(null);
		setPreviewError('');
		setPreviewLoadingId(artifactId);
		try {
			const payload = await fetchEvidenceArtifact(token, evidenceId, artifactId);
			if (previewRequestRef.current === generation) setPreviewPayload(payload);
		} catch (error) {
			if (previewRequestRef.current === generation)
				setPreviewError(
					error instanceof Error
						? error.message
						: t('app.workflows.errArtifactPreview', 'Artifact preview failed.'),
				);
		} finally {
			if (previewRequestRef.current === generation) setPreviewLoadingId('');
		}
	};

	const downloadArtifact = async (artifact: Artifact) => {
		const artifactId = String(artifact.id ?? '');
		const evidenceId = String(artifact.evidencePackageId ?? '');
		if (!artifactId || !evidenceId) {
			setPreviewError(t('app.workbench.evidence.incomplete', 'Artifact metadata is incomplete.'));
			return;
		}
		setPreviewError('');
		setDownloadLoadingId(artifactId);
		try {
			await downloadEvidenceArtifact(token, evidenceId, artifactId, artifactDisplayName(artifact));
		} catch (error) {
			setPreviewError(
				error instanceof Error
					? error.message
					: t('app.workbench.evidence.downloadError', 'Artifact download failed.'),
			);
		} finally {
			setDownloadLoadingId('');
		}
	};

	const runJobMutation = async (op: 'retry' | 'cancel', jobId: string, reason: string) => {
		if (!reason.trim()) {
			setJobMutationError(
				t('app.workflows.errQueueReasonRequired', 'Queue change reason is required.'),
			);
			return;
		}
		setJobMutationBusyId(jobId);
		setJobMutationError('');
		try {
			await mutate((writeToken) =>
				op === 'retry'
					? retryJob(writeToken, jobId, reason.trim())
					: cancelJob(writeToken, jobId, reason.trim()),
			);
		} catch (error) {
			setJobMutationError(
				error instanceof Error
					? error.message
					: t('app.workflows.errQueueOp', 'Queue operation failed.'),
			);
		} finally {
			setJobMutationBusyId('');
		}
	};

	if (!run) {
		return (
			<div className="run-detail">
				<EmptyState
					title={t('app.runDetail.notFound', 'Run not found')}
					body={t(
						'app.runDetail.notFoundBody',
						'This run id has no linked record in the current overview.',
					)}
				/>
			</div>
		);
	}

	const tabs: { id: RunDetailTab; label: string }[] = [
		{ id: 'overview', label: t('app.runDetail.tab.overview', 'Overview') },
		{ id: 'timeline', label: t('app.runDetail.tab.timeline', 'Timeline') },
		{ id: 'agents', label: t('app.runDetail.tab.agents', 'Agents') },
		{ id: 'evidence', label: t('app.runDetail.tab.evidence', 'Evidence') },
		{ id: 'artifacts', label: t('app.runDetail.tab.artifacts', 'Artifacts') },
		{ id: 'policy', label: t('app.runDetail.tab.policy', 'Policy') },
	];

	return (
		<div className="run-detail">
			<div className="run-detail-header">
				<h3 className="surface-title" ref={headingRef} tabIndex={-1}>
					{workflow?.title ?? t('app.runDetail.untitledRun', 'Workflow run')}
				</h3>
				<div className="inline">
					<Badge tone={toneForStatus(String(run.status ?? ''))}>{String(run.status ?? '')}</Badge>
					<span className="mono">{shortId(String(run.id ?? ''))}</span>
					{run.startedAt ? (
						<span className="mono">
							{t('app.runDetail.started', 'Started')}{' '}
							{new Date(String(run.startedAt)).toLocaleString()}
						</span>
					) : null}
				</div>
			</div>

			<Tabs
				className="run-detail-tabs"
				idBase="run-detail"
				tabs={tabs}
				activeTab={activeTab}
				onChange={(id) => setActiveTab(id as RunDetailTab)}
				label={t('app.runDetail.tablistLabel', 'Run detail sections')}
			>
				{activeTab === 'overview' ? (
					<div className="stack">
						<Surface
							title={t(
								'ui.static.what.is.missing.for.completed.f6ca783a',
								'What is missing for completed',
							)}
							flat
						>
							{missingForCompleted.length ? (
								<ul className="compact-list">
									{missingForCompleted.map((item) => (
										<li key={item}>{item}</li>
									))}
								</ul>
							) : (
								<p className="text-muted">
									{t(
										'ui.static.completion.prerequisites.satisfied.by.linked.records.51ee9c46',
										'Completion prerequisites satisfied by linked records.',
									)}
								</p>
							)}
						</Surface>
						<Surface title={t('app.workbench.inspector.blockers', 'Blockers')} flat>
							<DataTable
								rows={blockers}
								empty={
									<EmptyState
										title={t('app.workbench.inspector.noBlockers', 'No blockers')}
										body={t(
											'ui.static.no.linked.blocker.reason.has.been.recorded.e960291f',
											'No linked blocker reason has been recorded.',
										)}
									/>
								}
								columns={[
									{
										key: 'source',
										label: t('ui.static.source.6da13add', 'Source'),
										render: (row) => <span className="mono">{row.source}</span>,
									},
									{
										key: 'status',
										label: t('ui.static.status.bae7d5be', 'Status'),
										render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge>,
									},
									{
										key: 'reason',
										label: t('ui.static.reason.f219cc06', 'Reason'),
										render: (row) => redactVisibleText(row.reason),
									},
								]}
							/>
						</Surface>
						<Surface title={t('ui.static.links.0974c8fd', 'Links')} flat>
							<div className="stack">
								{patchArtifact ? (
									<div className="inline">
										<span className="mono">
											{t('ui.static.diff.artifact.4b3d14f2', 'Diff artifact')}
										</span>
										<span>{artifactDisplayName(patchArtifact)}</span>
										<span className="mono">{shortId(String(patchArtifact.hash ?? ''))}</span>
									</div>
								) : (
									<p className="text-muted">
										{t(
											'ui.static.no.diff.artifact.link.recorded.074dd704',
											'No diff artifact link recorded.',
										)}
									</p>
								)}
								<div className="inline">
									{linked.evidence.length ? (
										linked.evidence.map((item) => (
											<a className="button" href="#evidence" key={item.id}>
												Evidence {item.id}
											</a>
										))
									) : (
										<span className="text-muted">
											{t(
												'ui.static.no.evidence.link.recorded.090f170c',
												'No evidence link recorded.',
											)}
										</span>
									)}
								</div>
								<div className="inline">
									{pullRequestUrls.length ? (
										pullRequestUrls.map((url) => (
											<a className="button" href={url} key={url} rel="noreferrer" target="_blank">
												PR {url}
											</a>
										))
									) : (
										<span className="text-muted">
											{t('ui.static.no.pr.link.recorded.5d6bb7dc', 'No PR link recorded.')}
										</span>
									)}
								</div>
							</div>
						</Surface>
						<Surface
							title={t('ui.static.runtime.and.run.state.944e320d', 'Runtime and run state')}
							flat
						>
							<DataTable
								rows={linked.runs}
								empty={
									<EmptyState
										title={t('app.workbench.timeline.runsEmptyTitle', 'No workflow runs')}
										body={t(
											'ui.static.a.run.record.appears.after.workflow.execution.starts.2d1d2f67',
											'A run record appears after workflow execution starts.',
										)}
									/>
								}
								columns={[
									{
										key: 'run',
										label: t('ui.static.run.44c29edb', 'Run'),
										render: (row) => <span className="mono">{shortId(String(row.id ?? ''))}</span>,
									},
									{
										key: 'status',
										label: t('ui.static.status.bae7d5be', 'Status'),
										render: (row) => (
											<Badge tone={toneForStatus(String(row.status ?? ''))}>
												{String(row.status ?? '')}
											</Badge>
										),
									},
									{
										key: 'runtime',
										label: t('ui.static.runtime.c4740e4c', 'Runtime'),
										render: (row) => {
											const metadata = objectValue(row.metadata);
											const runtime = objectValue(metadata.runtime);
											return (
												<span className="mono">
													{String(
														runtime.id ?? t('app.workbench.workspace.missingPath', 'not selected'),
													)}
												</span>
											);
										},
									},
									{
										key: 'qa',
										label: t('ui.static.qa.verdict.48a65c68', 'QA verdict'),
										render: (row) => {
											const metadata = objectValue(row.metadata);
											return (
												<Badge tone={toneForStatus(String(metadata.qaVerdict ?? 'not_run'))}>
													{String(metadata.qaVerdict ?? 'not_run')}
												</Badge>
											);
										},
									},
									{
										key: 'diff',
										label: t('app.workbench.tab.diff', 'Diff'),
										render: (row) => {
											const metadata = objectValue(row.metadata);
											const diff = objectValue(metadata.diffSummary);
											const changedFiles = Array.isArray(diff.changedFiles)
												? diff.changedFiles.length
												: Number(diff.changedFiles ?? 0);
											return (
												<span className="mono">
													{String(diff.state ?? t('app.workflows.diffNotCaptured', 'not captured'))}{' '}
													/ {Number.isFinite(changedFiles) ? changedFiles : 0}{' '}
													{t('app.workflows.diffFilesSuffix', 'files')}
												</span>
											);
										},
									},
								]}
							/>
						</Surface>
						<Surface title={t('app.nav.workspaces', 'Workspaces')} flat>
							<DataTable
								rows={linked.workspaces}
								empty={
									<EmptyState
										title={t('ui.static.no.workspaces.09e5b922', 'No workspaces')}
										body={t(
											'ui.static.workspace.allocations.appear.after.implementation.steps.64d15c62',
											'Workspace allocations appear after implementation steps.',
										)}
									/>
								}
								columns={[
									{
										key: 'task',
										label: t('ui.static.task.7bb0ddf9', 'Task'),
										render: (row) => <span className="mono">{String(row.taskId ?? '')}</span>,
									},
									{
										key: 'status',
										label: t('ui.static.status.bae7d5be', 'Status'),
										render: (row) => (
											<Badge tone={toneForStatus(String(row.status ?? ''))}>
												{String(row.status ?? '')}
											</Badge>
										),
									},
								]}
							/>
						</Surface>
						<Surface title={t('ui.static.jobs.and.leases.4768364f', 'Jobs and leases')} flat>
							<div className="field">
								<label htmlFor="run-detail-job-mutation-reason">
									{t('app.copy.standalone.3', 'Queue change reason')}
								</label>
								<textarea
									id="run-detail-job-mutation-reason"
									className="textarea"
									value={jobMutationReason}
									onChange={(event) => {
										setJobMutationReason(event.target.value);
										setJobMutationError('');
									}}
								/>
								<div className="field-help">
									{t(
										'app.workflows.queueReasonHelp',
										'Retry and Cancel are disabled until a reason is recorded.',
									)}
								</div>
							</div>
							{jobMutationError ? (
								<div className="form-error" role="alert">
									{jobMutationError}
								</div>
							) : null}
							<DataTable
								rows={linked.jobs}
								empty={
									<EmptyState
										title={t('ui.static.no.jobs.e0f919e2', 'No jobs')}
										body={t(
											'ui.static.jobs.linked.to.workflow.runs.appear.here.7c0a2243',
											'Jobs linked to workflow runs appear here.',
										)}
									/>
								}
								columns={[
									{
										key: 'kind',
										label: t('app.workbench.evidence.colKind', 'Kind'),
										render: (row) => <span className="mono">{row.kind}</span>,
									},
									{
										key: 'status',
										label: t('ui.static.status.bae7d5be', 'Status'),
										render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge>,
									},
									{
										key: 'lease',
										label: t('ui.static.lease.b24ef8c7', 'Lease'),
										render: (row) => (
											<span>
												{row.leaseOwner
													? `${row.leaseOwner} until ${row.leaseExpiresAt}`
													: t('app.runtime.card.none', 'none')}
											</span>
										),
									},
									{
										key: 'actions',
										label: t('ui.static.actions.c3cd636a', 'Actions'),
										render: (row) => {
											const busy = jobMutationBusyId === row.id;
											const blocked = !jobMutationReason.trim() || Boolean(jobMutationBusyId);
											return (
												<div className="inline" aria-busy={busy}>
													<button
														className="button"
														type="button"
														aria-label={`${t('app.workflows.ariaRetryJob', 'Retry job')} ${row.kind}`}
														disabled={blocked}
														onClick={() => void runJobMutation('retry', row.id, jobMutationReason)}
													>
														{busy
															? t('app.workflows.jobRetrying', 'Retrying')
															: t('ui.static.retry.9f5cd8a2', 'Retry')}
													</button>
													<button
														className="button danger"
														type="button"
														aria-label={`${t('app.workflows.ariaCancelJob', 'Cancel job')} ${row.kind}`}
														disabled={blocked}
														onClick={() => void runJobMutation('cancel', row.id, jobMutationReason)}
													>
														{busy
															? t('app.workflows.jobCancelling', 'Cancelling')
															: t('ui.static.cancel.77dfd213', 'Cancel')}
													</button>
												</div>
											);
										},
									},
								]}
							/>
						</Surface>
					</div>
				) : null}

				{activeTab === 'timeline' ? <WorkflowRunTimeline items={timelineItems} /> : null}

				{activeTab === 'agents' ? (
					<div className="stack">
						<Surface title={t('app.runDetail.agentRuns', 'Agent runs')} flat>
							<DataTable
								rows={linked.agentRuns}
								empty={
									<EmptyState
										title={t('ui.static.no.agent.runs.5482c9e5', 'No agent runs')}
										body={t(
											'ui.static.agent.run.records.appear.after.runtime.selection.72e0d9bd',
											'Agent run records appear after runtime selection.',
										)}
									/>
								}
								columns={[
									{
										key: 'agent',
										label: t('ui.static.agent.run.b458871f', 'Agent run'),
										render: (row) => <span className="mono">{shortId(String(row.id ?? ''))}</span>,
									},
									{
										key: 'profile',
										label: t('ui.static.profile.ff4fc027', 'Profile'),
										render: (row) => {
											const metadata = objectValue(row.metadata);
											return (
												<span className="mono">
													{String(
														metadata.agentProfileId ??
															metadata.runtimeType ??
															t('app.runtime.card.unknown', 'unknown'),
													)}
												</span>
											);
										},
									},
									{
										key: 'status',
										label: t('ui.static.status.bae7d5be', 'Status'),
										render: (row) => (
											<Badge tone={toneForStatus(String(row.status ?? ''))}>
												{String(row.status ?? '')}
											</Badge>
										),
									},
								]}
							/>
						</Surface>
						<Surface title={t('ui.static.model.calls.88e40906', 'Model calls')} flat>
							<DataTable
								rows={linked.modelCalls}
								empty={
									<EmptyState
										title={t('ui.static.no.model.calls.3e3394fe', 'No model calls')}
										body={t(
											'ui.static.model.calls.linked.to.workflow.agent.runs.appear.here.0ae76932',
											'Model calls linked to workflow agent runs appear here.',
										)}
									/>
								}
								columns={[
									{
										key: 'provider',
										label: t('ui.static.provider.7ceee3f3', 'Provider'),
										render: (row) => <span className="mono">{String(row.provider ?? '')}</span>,
									},
									{
										key: 'model',
										label: t('ui.static.model.68c2cc7f', 'Model'),
										render: (row) => <span className="mono">{String(row.model ?? '')}</span>,
									},
									{
										key: 'status',
										label: t('ui.static.status.bae7d5be', 'Status'),
										render: (row) => (
											<Badge tone={toneForStatus(String(row.status ?? ''))}>
												{String(row.status ?? '')}
											</Badge>
										),
									},
									{
										key: 'tokens',
										label: t('ui.static.tokens.9a1f9463', 'Tokens'),
										render: (row) => (
											<span className="mono">
												{tokenLabel(row.promptTokens, t('app.runtime.card.unknown', 'unknown'))} /{' '}
												{tokenLabel(row.completionTokens, t('app.runtime.card.unknown', 'unknown'))}
											</span>
										),
									},
									{
										key: 'cost',
										label: t('ui.static.cost.64ae43e8', 'Cost'),
										render: (row) => (
											<span className="mono">
												{costLabel(row.costUsd, t('app.runtime.card.unknown', 'unknown'))}
											</span>
										),
									},
								]}
							/>
						</Surface>
						<Surface
							title={t('ui.static.tool.calls.and.approvals.3788745c', 'Tool calls and approvals')}
							flat
						>
							<DataTable
								rows={linked.toolCalls}
								empty={
									<EmptyState
										title={t('ui.static.no.tool.calls.7ab6a86e', 'No tool calls')}
										body={t(
											'ui.static.agent.runtime.calls.linked.to.this.workflow.appear.here.7fb6ecbb',
											'Agent runtime calls linked to this workflow appear here.',
										)}
									/>
								}
								columns={[
									{
										key: 'tool',
										label: t('ui.static.tool.9a830c71', 'Tool'),
										render: (row) => <span className="mono">{String(row.toolName ?? '')}</span>,
									},
									{
										key: 'status',
										label: t('ui.static.status.bae7d5be', 'Status'),
										render: (row) => (
											<Badge tone={toneForStatus(String(row.status ?? ''))}>
												{String(row.status ?? '')}
											</Badge>
										),
									},
									{
										key: 'command',
										label: t('app.workbench.evidence.colCommand', 'Command'),
										render: (row) => {
											const payload = row.payload as Record<string, unknown> | undefined;
											return <span className="mono">{String(payload?.command ?? '')}</span>;
										},
									},
								]}
							/>
							<DataTable
								rows={linked.approvals}
								empty={
									<EmptyState
										title={t('ui.static.no.approvals.380cc620', 'No approvals')}
										body={t(
											'ui.static.granular.approvals.linked.to.workflow.jobs.appear.here.7308c236',
											'Granular approvals linked to workflow jobs appear here.',
										)}
									/>
								}
								columns={[
									{
										key: 'action',
										label: t('ui.static.action.97c89a4d', 'Action'),
										render: (row) => <span className="mono">{row.actionType}</span>,
									},
									{
										key: 'status',
										label: t('ui.static.status.bae7d5be', 'Status'),
										render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge>,
									},
									{
										key: 'risk',
										label: t('ui.static.risk.5a8f23f5', 'Risk'),
										render: (row) => (
											<Badge tone={toneForStatus(row.riskLevel)}>{row.riskLevel}</Badge>
										),
									},
								]}
							/>
						</Surface>
					</div>
				) : null}

				{activeTab === 'evidence' ? (
					<div className="stack">
						<Surface title={t('ui.static.evidence.and.tests.0c061aed', 'Evidence and tests')} flat>
							<DataTable
								rows={linked.evidence}
								empty={
									<EmptyState
										title={t('ui.static.no.evidence.fade4ede', 'No evidence')}
										body={t(
											'ui.static.qa.packages.linked.to.this.workflow.run.appear.here.37481b29',
											'QA packages linked to this workflow run appear here.',
										)}
									/>
								}
								columns={[
									{
										key: 'task',
										label: t('ui.static.task.7bb0ddf9', 'Task'),
										render: (row) => String(row.taskId ?? ''),
									},
									{
										key: 'verdict',
										label: t('ui.static.verdict.7f6e5a6e', 'Verdict'),
										render: (row) => (
											<Badge tone={toneForStatus(String(row.qaVerdict ?? ''))}>
												{String(row.qaVerdict ?? '')}
											</Badge>
										),
									},
									{
										key: 'source',
										label: t('ui.static.source.6da13add', 'Source'),
										render: (row) => (
											<span className="mono">
												{String(row.evidenceSource ?? 'operator_attested')}
											</span>
										),
									},
									{
										key: 'diffs',
										label: t('ui.static.diff.refs.cc464235', 'Diff refs'),
										render: (row) => String(Array.isArray(row.diffRefs) ? row.diffRefs.length : 0),
									},
									{
										key: 'completeness',
										label: t('ui.static.completeness.4008e0b6', 'Completeness'),
										render: (row) => {
											const hasQa = Array.isArray(row.testResults) && row.testResults.length > 0;
											const hasDiffRefs = Array.isArray(row.diffRefs) && row.diffRefs.length > 0;
											return (
												<span className="mono">
													{hasQa && hasDiffRefs
														? t('app.workflows.completenessComplete', 'complete')
														: t('app.workflows.completenessPartial', 'partial')}
												</span>
											);
										},
									},
								]}
							/>
							<DataTable
								rows={linked.testResults}
								empty={
									<EmptyState
										title={t('ui.static.no.test.records.4878c3ab', 'No test records')}
										body={t(
											'ui.static.test.results.appear.after.evidence.ingestion.26f3959e',
											'Test results appear after evidence ingestion.',
										)}
									/>
								}
								columns={[
									{
										key: 'command',
										label: t('app.workbench.evidence.colCommand', 'Command'),
										render: (row) => <span className="mono">{String(row.command ?? '')}</span>,
									},
									{
										key: 'status',
										label: t('ui.static.status.bae7d5be', 'Status'),
										render: (row) => (
											<Badge tone={toneForStatus(String(row.status ?? ''))}>
												{String(row.status ?? '')}
											</Badge>
										),
									},
								]}
							/>
						</Surface>
						<Surface title={t('ui.static.steps.cdde4f20', 'Steps')} flat>
							<DataTable
								rows={linked.steps}
								empty={
									<EmptyState
										title={t('ui.static.no.steps.00a23c5b', 'No steps')}
										body={t(
											'ui.static.start.the.workflow.to.expand.steps.a5f96b0b',
											'Start the workflow to expand steps.',
										)}
									/>
								}
								columns={[
									{
										key: 'name',
										label: t('ui.static.step.dc416e10', 'Step'),
										render: (row) => <span className="mono">{row.name}</span>,
									},
									{
										key: 'status',
										label: t('ui.static.status.bae7d5be', 'Status'),
										render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge>,
									},
									{
										key: 'risk',
										label: t('ui.static.risk.5a8f23f5', 'Risk'),
										render: (row) => (
											<span className="mono">
												{String(
													row.riskLevel ?? t('app.workbenchEvidence.notRecorded', 'not recorded'),
												)}
											</span>
										),
									},
									{
										key: 'model',
										label: t('ui.static.model.mode.f9d81107', 'Model mode'),
										render: (row) => (
											<span className="mono">
												{String(row.modelMode ?? row.manualModelOverride ?? 'policy')}
											</span>
										),
									},
								]}
							/>
						</Surface>
					</div>
				) : null}

				{activeTab === 'artifacts' ? (
					<Surface title={t('ui.static.artifacts.a5b79f59', 'Artifacts')} flat>
						<DataTable
							rows={linked.artifacts}
							empty={
								<EmptyState
									title={t('ui.static.no.artifacts.3e0935de', 'No artifacts')}
									body={t(
										'ui.static.logs.reports.and.screenshots.linked.to.evidence.appear.here.9ce88334',
										'Logs, reports and screenshots linked to evidence appear here.',
									)}
								/>
							}
							columns={[
								{
									key: 'name',
									label: t('ui.static.name.709a2322', 'Name'),
									render: (row) => <span className="mono">{artifactDisplayName(row)}</span>,
								},
								{
									key: 'kind',
									label: t('app.workbench.evidence.colKind', 'Kind'),
									render: (row) => (
										<Badge>{String(row.kind ?? t('app.review.artifact', 'artifact'))}</Badge>
									),
								},
								{
									key: 'size',
									label: t('ui.static.size.b7152342', 'Size'),
									render: (row) => artifactSizeLabel(row),
								},
								{
									key: 'hash',
									label: t('ui.static.hash.873507a0', 'Hash'),
									render: (row) => <span className="mono">{shortId(String(row.hash ?? ''))}</span>,
								},
								{
									key: 'action',
									label: t('ui.static.action.97c89a4d', 'Action'),
									render: (row) => {
										const name = artifactDisplayName(row);
										const loading = previewLoadingId === String(row.id ?? '');
										const downloading = downloadLoadingId === String(row.id ?? '');
										return (
											<div className="inline" aria-busy={loading || downloading}>
												<button
													className="button"
													type="button"
													aria-label={`${t('app.workflows.ariaPreviewArtifact', 'Preview workflow artifact')} ${name}`}
													disabled={loading}
													onClick={() => void openPreview(row)}
												>
													{loading
														? t('app.review.opening', 'Opening')
														: t('app.workbench.evidence.preview', 'Preview')}
												</button>
												<button
													className="button"
													type="button"
													aria-label={`${t('app.workflows.ariaDownloadArtifact', 'Download workflow artifact')} ${name}`}
													disabled={downloading}
													onClick={() => void downloadArtifact(row)}
												>
													{downloading
														? t('app.workbench.evidence.downloading', 'Downloading')
														: t('app.workbench.evidence.download', 'Download')}
												</button>
											</div>
										);
									},
								},
							]}
						/>
					</Surface>
				) : null}

				{activeTab === 'policy' ? (
					<Surface
						title={t(
							'ui.static.security.and.policy.decisions.f06bb7ad',
							'Security and policy decisions',
						)}
						flat
					>
						<DataTable
							rows={linked.permissionDecisions}
							empty={
								<EmptyState
									title={t('app.workflows.noPolicyDecisions', 'No policy decisions')}
									body={t(
										'ui.static.policy.decisions.linked.through.workflow.tool.calls.appear.h.7cf16ea5',
										'Policy decisions linked through workflow tool calls appear here.',
									)}
								/>
							}
							columns={[
								{
									key: 'decision',
									label: t('ui.static.decision.7f59a1f1', 'Decision'),
									render: (row) => (
										<Badge tone={toneForStatus(String(row.decision ?? ''))}>
											{String(row.decision ?? '')}
										</Badge>
									),
								},
								{
									key: 'risk',
									label: t('ui.static.risk.5a8f23f5', 'Risk'),
									render: (row) => (
										<Badge tone={toneForStatus(String(row.riskLevel ?? ''))}>
											{String(row.riskLevel ?? '')}
										</Badge>
									),
								},
								{
									key: 'categories',
									label: t('ui.static.categories.6ccb6007', 'Categories'),
									render: (row) => {
										const payload = row.payload as Record<string, unknown> | undefined;
										const categories = Array.isArray(payload?.categories)
											? payload.categories.join(', ')
											: '';
										return <span className="mono">{categories}</span>;
									},
								},
								{
									key: 'reason',
									label: t('ui.static.reason.f219cc06', 'Reason'),
									render: (row) => String(row.reason ?? ''),
								},
							]}
						/>
					</Surface>
				) : null}
			</Tabs>

			<Drawer
				label={t('ui.static.workflow.artifact.preview.df9b22a5', 'Workflow artifact preview')}
				open={Boolean(previewArtifact)}
				onClose={() => {
					setPreviewArtifact(null);
					setPreviewPayload(null);
					setPreviewError('');
				}}
			>
				<div className="drawer-body">
					{previewArtifact ? (
						<>
							<div className="stack">
								<div className="inline">
									<Badge>
										{String(previewArtifact.kind ?? t('app.review.artifact', 'artifact'))}
									</Badge>
									<Badge>{artifactMimeType(previewArtifact, previewPayload)}</Badge>
									<Badge>{artifactSizeLabel(previewArtifact)}</Badge>
								</div>
								<h3 className="artifact-title">{artifactDisplayName(previewArtifact)}</h3>
								<div className="mono">
									sha256{' '}
									{String(
										previewPayload?.hash ||
											previewArtifact.hash ||
											t('app.workbenchEvidence.notRecorded', 'not recorded'),
									)}
								</div>
							</div>
							{previewError ? (
								<div className="form-error" role="alert">
									{previewError}
								</div>
							) : null}
							{downloadLoadingId ? (
								<div className="sr-only" role="status">
									{t('ui.static.downloading.artifact.b640e8fe', 'Downloading artifact')}
								</div>
							) : null}
							{previewPayload?.text ? (
								<pre className="artifact-preview">{redactVisibleText(previewPayload.text, '')}</pre>
							) : (
								<EmptyState
									title={
										previewLoadingId
											? t('app.workbench.evidence.loading', 'Loading artifact')
											: t('app.review.binaryOrEmptyArtifact', 'Binary or empty artifact')
									}
									body={t(
										'ui.static.non.text.artifacts.remain.downloadable.but.are.not.rendered.42481492',
										'Non-text artifacts remain downloadable, but are not rendered inline.',
									)}
								/>
							)}
							<button
								className="button primary"
								type="button"
								disabled={downloadLoadingId === String(previewArtifact.id ?? '')}
								aria-label={`${t('app.workflows.ariaDownloadPreviewArtifact', 'Download workflow preview artifact')} ${artifactDisplayName(previewArtifact)}`}
								onClick={() => void downloadArtifact(previewArtifact)}
							>
								{downloadLoadingId === String(previewArtifact.id ?? '')
									? t('ui.static.downloading.artifact.b640e8fe', 'Downloading artifact')
									: t('app.review.downloadArtifact', 'Download artifact')}
							</button>
						</>
					) : null}
				</div>
			</Drawer>
		</div>
	);
}
