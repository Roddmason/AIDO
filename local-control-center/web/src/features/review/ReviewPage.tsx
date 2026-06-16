/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { AlertTriangle, FileCheck2, GitBranch, ShieldAlert } from 'lucide-react';

import type { ActionRequest, Overview } from '../../api/types';
import { Badge, DataTable, Drawer, EmptyState, PageHeader, StatusDot, Surface } from '../../components/primitives';
import { Disclosure } from '../../components/Disclosure';
import { artifactDisplayName } from '../../lib/artifacts';
import { hasRealPatchChanges } from '../../lib/diff';
import { shortId, toneForStatus } from '../../lib/format';
import { redactVisibleText } from '../../lib/redaction';
import { useI18n } from '../../i18n/I18nProvider';
import {
	COLUMN_TONE,
	REVIEW_COLUMNS,
	buildReviewItems,
	groupReviewItems,
	patchWorkflowKind,
	prettyJson,
	requiresPatchEvidenceGate,
	riskTone,
	securityFindingsAreNonBlocking,
} from './model';
import type { PatchWorkflowKind, ReviewColumn, ReviewItem } from './model';
import { useReviewDecision } from './useReviewDecision';
import { useShipOperations } from './useShipOperations';
import type { ShipOperation } from './useShipOperations';
import { useArtifactPreview } from './useArtifactPreview';

/**
 * A shippable board item: a reviewed patch-workflow run whose status allows the
 * next ship step. Promote when approved (or a prior promotion failed); create a
 * PR once promoted to a branch. Status guards mirror the Jobs queue contract.
 */
function shipOperationFor(item: ReviewItem): { operation: ShipOperation; kind: PatchWorkflowKind } | null {
	if (!item.runId) return null;
	const kind = patchWorkflowKind(item.workflowKind);
	if (!kind) return null;
	if (item.runStatus === 'approved_for_integration' || item.runStatus === 'promotion_failed') {
		return { operation: 'promote', kind };
	}
	if (item.runStatus === 'promoted_to_branch') {
		return { operation: 'pull-request', kind };
	}
	return null;
}

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;
type Refresh = (silent?: boolean) => Promise<void>;
type Lang = 'en' | 'es';

const COPY: Record<Lang, {
	kicker: string;
	title: string;
	summary: string;
	boardLabel: string;
	columns: Record<ReviewColumn, { title: string; empty: { title: string; body: string } }>;
	risk: string;
	qa: string;
	diff: string;
	evidence: string;
	review: string;
	decidedBy: string;
	openEvidence: string;
	drawerLabel: string;
	reasonLabel: string;
	reasonHelpGate: string;
	reasonHelpPlain: string;
	approve: string;
	approvePatch: string;
	reject: string;
	itemsSuffix: string;
	ship: string;
	shipDrawerLabel: string;
	promote: string;
	createPr: string;
	shipReasonLabel: string;
	shipReasonHelp: string;
	shipBranchLabel: string;
	shipPrTitleLabel: string;
	shipPrBaseLabel: string;
	shipAdvanced: string;
	shipPromoted: string;
	evidenceSectionTitle: string;
	openFullEvidence: string;
}> = {
	en: {
		kicker: 'Operational review ledger',
		title: 'Review',
		summary: 'One inbox for every human decision: pending approvals, QA failures, security blockers, evidence-ready runs, approvals for integration and recent decisions. Cards reflect server state — open one to approve or reject with full evidence.',
		boardLabel: 'Review board',
		columns: {
			needs_review: {
				title: 'Needs review',
				empty: { title: 'Nothing waiting on you', body: 'Runs land here when QA produces evidence and a sensitive action needs a human decision.' },
			},
			blocked: {
				title: 'Blocked',
				empty: { title: 'No blocked runs', body: 'A run appears here when QA fails, a runtime is unavailable, or a decision was rejected.' },
			},
			ready: {
				title: 'Ready',
				empty: { title: 'Nothing ready to ship', body: 'Approved runs wait here for branch promotion or PR creation.' },
			},
			done: {
				title: 'Done',
				empty: { title: 'No completed runs yet', body: 'Runs move here once a PR is created, the workflow completes, or a request is decided.' },
			},
		},
		risk: 'risk',
		qa: 'QA',
		diff: 'diff',
		evidence: 'evidence',
		review: 'Review',
		decidedBy: 'by',
		openEvidence: 'View evidence',
		drawerLabel: 'Action request review',
		reasonLabel: 'Human decision reason',
		reasonHelpGate: 'Approve patch also requires complete linked evidence, non-blocking security findings, and a real diff. Reject only requires a recorded reason.',
		reasonHelpPlain: 'Approve or reject is blocked until this reason is recorded.',
		approve: 'Approve',
		approvePatch: 'Approve patch',
		reject: 'Reject',
		itemsSuffix: 'items',
		ship: 'Ship',
		shipDrawerLabel: 'Run detail',
		promote: 'Promote branch',
		createPr: 'Create PR',
		shipReasonLabel: 'Workflow operation reason',
		shipReasonHelp: 'Branch promotion and PR creation stay blocked until a reason is recorded.',
		shipBranchLabel: 'Promotion branch (optional)',
		shipPrTitleLabel: 'PR title (optional)',
		shipPrBaseLabel: 'PR base branch (optional)',
		shipAdvanced: 'Advanced options',
		shipPromoted: 'Last operation',
		evidenceSectionTitle: 'Linked evidence',
		openFullEvidence: 'Open full evidence',
	},
	es: {
		kicker: 'Registro operacional de revisión',
		title: 'Revisión',
		summary: 'Una sola bandeja para cada decisión humana: aprobaciones pendientes, fallas de QA, bloqueos de seguridad, ejecuciones con evidencia lista, aprobaciones para integración y decisiones recientes. Las cards reflejan el estado del servidor — abre una para aprobar o rechazar con evidencia completa.',
		boardLabel: 'Tablero de revisión',
		columns: {
			needs_review: {
				title: 'Por revisar',
				empty: { title: 'Nada esperando por ti', body: 'Las ejecuciones llegan aquí cuando QA produce evidencia y una acción sensible necesita una decisión humana.' },
			},
			blocked: {
				title: 'Bloqueado',
				empty: { title: 'Sin ejecuciones bloqueadas', body: 'Una ejecución aparece aquí cuando QA falla, un runtime no está disponible o una decisión fue rechazada.' },
			},
			ready: {
				title: 'Listo',
				empty: { title: 'Nada listo para integrar', body: 'Las ejecuciones aprobadas esperan aquí la promoción de rama o la creación del PR.' },
			},
			done: {
				title: 'Hecho',
				empty: { title: 'Aún sin ejecuciones completadas', body: 'Las ejecuciones llegan aquí cuando se crea un PR, el flujo termina o se decide una solicitud.' },
			},
		},
		risk: 'riesgo',
		qa: 'QA',
		diff: 'diff',
		evidence: 'evidencia',
		review: 'Revisar',
		decidedBy: 'por',
		openEvidence: 'Ver evidencia',
		drawerLabel: 'Revisión de solicitud de acción',
		reasonLabel: 'Razón de la decisión humana',
		reasonHelpGate: 'Aprobar el parche también requiere evidencia vinculada completa, hallazgos de seguridad no bloqueantes y un diff real. Rechazar solo requiere una razón registrada.',
		reasonHelpPlain: 'Aprobar o rechazar está bloqueado hasta registrar esta razón.',
		approve: 'Aprobar',
		approvePatch: 'Aprobar parche',
		reject: 'Rechazar',
		itemsSuffix: 'ítems',
		ship: 'Integrar',
		shipDrawerLabel: 'Detalle de la ejecución',
		promote: 'Promover rama',
		createPr: 'Crear PR',
		shipReasonLabel: 'Razón de la operación de workflow',
		shipReasonHelp: 'La promoción de rama y la creación de PR quedan bloqueadas hasta registrar una razón.',
		shipBranchLabel: 'Rama de promoción (opcional)',
		shipPrTitleLabel: 'Título del PR (opcional)',
		shipPrBaseLabel: 'Rama base del PR (opcional)',
		shipAdvanced: 'Opciones avanzadas',
		shipPromoted: 'Última operación',
		evidenceSectionTitle: 'Evidencia vinculada',
		openFullEvidence: 'Abrir evidencia completa',
	},
};

function DetailRow({ label, value }: { label: string; value: string }) {
	return (
		<div className="diff-row" role="row">
			<div role="cell" className="mono">{label}</div>
			<div role="cell">{value || 'not recorded'}</div>
		</div>
	);
}

function ReviewCard({
	item,
	copy,
	selected,
	onOpenReview,
	onOpenDetail,
}: {
	item: ReviewItem;
	copy: (typeof COPY)[Lang];
	selected: boolean;
	onOpenReview: (item: ReviewItem, trigger: HTMLElement | null) => void;
	onOpenDetail: (item: ReviewItem, trigger: HTMLElement | null) => void;
}) {
	const titleId = `review-card-${item.key}`;
	const triggerRef = useRef<HTMLButtonElement>(null);
	const shipTriggerRef = useRef<HTMLButtonElement>(null);
	const shipOp = shipOperationFor(item);
	const open = () => onOpenReview(item, triggerRef.current);

	const handleCardKeyDown = (event: KeyboardEvent<HTMLElement>) => {
		if (!item.canDecide) return;
		if (event.target !== event.currentTarget) return;
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			open();
		}
	};

	const riskBadge = item.riskLevel ? (
		<Badge tone={riskTone(item.riskLevel)}>
			{(item.riskLevel === 'critical' || item.riskLevel === 'high')
				? <ShieldAlert size={14} aria-hidden="true" />
				: <AlertTriangle size={14} aria-hidden="true" />}
			{item.riskLevel}
		</Badge>
	) : null;

	return (
		<article
			className="card review-card"
			data-risk={item.riskLevel ?? undefined}
			data-selected={selected ? 'true' : undefined}
			tabIndex={0}
			aria-labelledby={titleId}
			onKeyDown={handleCardKeyDown}
		>
			<div className="card-header">
				<h3 id={titleId} className="card-title">{item.projectName}</h3>
				{riskBadge}
			</div>
			<div className="card-meta">
				<StatusDot tone={toneForStatus(item.runStatus ?? undefined)} />
				<span className="mono">{shortId(item.runId ?? item.actionId)}</span>
				{item.qaVerdict ? <Badge tone={toneForStatus(item.qaVerdict)}>{copy.qa} {item.qaVerdict}</Badge> : null}
				{item.runStatus ? <Badge tone={toneForStatus(item.runStatus)}>{item.runStatus}</Badge> : null}
			</div>
			<p className="card-body review-card-body">{item.runLabel}</p>
			<div className="inline review-card-actions">
				<span className="review-card-refs">
					{item.hasDiff ? <span className="inline review-ref"><GitBranch size={14} aria-hidden="true" />{copy.diff}</span> : null}
					{item.hasEvidence ? <span className="inline review-ref"><FileCheck2 size={14} aria-hidden="true" />{copy.evidence}</span> : null}
					{item.source === 'decided_action' && item.decidedBy
						? <span className="review-decided mono">{copy.decidedBy} {item.decidedBy}</span>
						: null}
				</span>
				{item.canDecide ? (
					<button
						ref={triggerRef}
						className="button primary review-card-cta"
						type="button"
						aria-label={`${copy.review}: ${item.runLabel} · ${item.projectName}`}
						onClick={open}
					>
						{copy.review}
					</button>
				) : shipOp ? (
					<button
						ref={shipTriggerRef}
						className="button primary review-card-cta"
						type="button"
						aria-label={`${shipOp.operation === 'promote' ? copy.promote : copy.createPr}: ${item.runLabel} · ${item.projectName}`}
						onClick={() => onOpenDetail(item, shipTriggerRef.current)}
					>
						{shipOp.operation === 'promote' ? copy.promote : copy.createPr}
					</button>
				) : (item.hasEvidence ? (
					<button
						ref={shipTriggerRef}
						className="button review-evidence-link"
						type="button"
						aria-label={`${copy.openEvidence}: ${item.runLabel} · ${item.projectName}`}
						onClick={() => onOpenDetail(item, shipTriggerRef.current)}
					>
						{copy.openEvidence}
					</button>
				) : null)}
			</div>
		</article>
	);
}

export function ReviewPage({
	overview,
	token,
	mutate,
	refresh,
}: {
	overview: Overview;
	token: string;
	mutate: Mutate;
	refresh: Refresh;
}) {
	const { language } = useI18n();
	const lang: Lang = language === 'es' ? 'es' : 'en';
	const copy = COPY[lang];

	const [selectedActionId, setSelectedActionId] = useState('');
	const triggerRef = useRef<HTMLElement | null>(null);

	const items = useMemo(() => buildReviewItems(overview), [overview]);
	const columns = useMemo(() => groupReviewItems(items), [items]);

	const selectedAction = useMemo<ActionRequest | null>(
		() => overview.actionRequests.find((action) => action.id === selectedActionId) ?? null,
		[overview, selectedActionId],
	);
	const decision = useReviewDecision(selectedAction, overview, token, mutate);
	const artifactPreview = useArtifactPreview(token);

	// Ship lifecycle (promote/PR) for a reviewed, approved run — additive to the
	// decision flow. Keyed by item key so it re-resolves the live run after a
	// refresh (promote → the same drawer then offers Create PR).
	const ship = useShipOperations(token, refresh);
	const [shipItemKey, setShipItemKey] = useState('');
	const shipTriggerRef = useRef<HTMLElement | null>(null);
	const liveShipItem = useMemo(
		() => (shipItemKey ? items.find((item) => item.key === shipItemKey) ?? null : null),
		[items, shipItemKey],
	);
	const shipOp = liveShipItem ? shipOperationFor(liveShipItem) : null;
	const detailEvidence = useMemo(() => {
		if (!liveShipItem) return [];
		const pkgId = liveShipItem.evidencePackageId;
		const runId = liveShipItem.runId;
		return overview.evidencePackages.filter(
			(record) => (pkgId && String(record.id ?? '') === pkgId) || (runId && String(record.workflowRunId ?? '') === runId),
		);
	}, [overview, liveShipItem]);

	const openReview = (item: ReviewItem, trigger: HTMLElement | null) => {
		if (!item.actionId) return;
		triggerRef.current = trigger;
		setSelectedActionId(item.actionId);
	};
	const closeReview = () => {
		setSelectedActionId('');
		artifactPreview.clear();
		const trigger = triggerRef.current;
		triggerRef.current = null;
		if (trigger) trigger.focus();
	};
	const openDetail = (item: ReviewItem, trigger: HTMLElement | null) => {
		shipTriggerRef.current = trigger;
		ship.reset();
		setShipItemKey(item.key);
	};
	const closeDetail = () => {
		setShipItemKey('');
		ship.reset();
		const trigger = shipTriggerRef.current;
		shipTriggerRef.current = null;
		if (trigger) trigger.focus();
	};
	const submitDecision = async (kind: 'approve' | 'reject') => {
		const ok = await decision.decide(kind);
		if (!ok) return;
		closeReview();
		void refresh(true);
	};

	const requiresGate = selectedAction ? requiresPatchEvidenceGate(selectedAction) : false;

	return (
		<>
			<PageHeader kicker={copy.kicker} title={copy.title} summary={copy.summary} />
			<section className="review-board" role="region" aria-label={copy.boardLabel}>
				{REVIEW_COLUMNS.map((column) => {
					const lane = columns[column];
					const meta = copy.columns[column];
					const headerId = `review-col-${column}`;
					return (
						<section
							key={column}
							className="review-column"
							data-focal={column === 'needs_review' ? 'true' : undefined}
							data-motion-item
							role="group"
							aria-labelledby={headerId}
						>
							<header className="review-column-header">
								<StatusDot tone={COLUMN_TONE[column]} />
								<span id={headerId} className="review-column-title">{meta.title}</span>
								<span className="review-column-count">
									<Badge tone={lane.length ? COLUMN_TONE[column] : undefined}>{lane.length}</Badge>
								</span>
							</header>
							<div className="review-column-scroll">
								{lane.length === 0 ? (
									<EmptyState title={meta.empty.title} body={meta.empty.body} />
								) : (
									lane.map((item) => (
										<ReviewCard
											key={item.key}
											item={item}
											copy={copy}
											selected={item.actionId === selectedActionId && Boolean(selectedActionId)}
											onOpenReview={openReview}
											onOpenDetail={openDetail}
										/>
									))
								)}
							</div>
						</section>
					);
				})}
			</section>

			<Drawer label={copy.drawerLabel} open={Boolean(selectedAction)} onClose={closeReview}>
				<div className="drawer-body">
					{selectedAction ? (
						<div className="stack">
							<div className="inline">
								<Badge tone={riskTone(selectedAction.riskLevel)}>{selectedAction.riskLevel}</Badge>
								<Badge>{selectedAction.actionType}</Badge>
								<Badge>{selectedAction.expiresAt ? `expires ${selectedAction.expiresAt}` : 'no expiration recorded'}</Badge>
							</div>
							<div className="diff-grid" role="table" aria-label="Action request scope">
								<DetailRow label="Command" value={redactVisibleText(selectedAction.command || 'not recorded', 'not recorded')} />
								<DetailRow label="Argv" value={redactVisibleText((selectedAction.commandArgv ?? []).join(' '), '')} />
								<DetailRow label="Workspace" value={`${selectedAction.workspaceId || 'not recorded'} ${selectedAction.workspacePath || ''}`.trim()} />
								<DetailRow label="Runtime" value={`${selectedAction.runtimeId || 'not recorded'} ${redactVisibleText(prettyJson(selectedAction.runtime), '')}`} />
								<DetailRow label="Job" value={selectedAction.jobId} />
								<DetailRow label="Project" value={selectedAction.projectId} />
							</div>
							<Surface title="Policy reason" flat>
								<pre className="artifact-preview">{redactVisibleText(selectedAction.reason, '')}</pre>
							</Surface>
							<Surface title="Linked evidence packages" flat>
								<DataTable
									rows={decision.evidence}
									empty={<EmptyState title="No linked evidence packages" body="This request did not record evidence package references." />}
									columns={[
										{ key: 'id', label: 'Evidence', render: (row) => <span className="mono">{String(row.id ?? '')}</span> },
										{ key: 'verdict', label: 'QA', render: (row) => <Badge tone={toneForStatus(String(row.qaVerdict ?? ''))}>{String(row.qaVerdict ?? '')}</Badge> },
										{ key: 'source', label: 'Source', render: (row) => <span className="mono">{String(row.evidenceSource ?? 'operator_attested')}</span> },
										{ key: 'task', label: 'Task', render: (row) => String(row.taskId ?? '') },
									]}
								/>
							</Surface>
							<Surface title="Linked artifacts" flat>
								<DataTable
									rows={decision.artifacts}
									empty={<EmptyState title="No linked artifacts" body="Diff and evidence artifacts must be attached by the requesting runtime." />}
									columns={[
										{ key: 'name', label: 'Name', render: (row) => artifactDisplayName(row) },
										{ key: 'kind', label: 'Kind', render: (row) => <span className="mono">{String(row.kind ?? '')}</span> },
										{ key: 'hash', label: 'Hash', render: (row) => <span className="mono">{String(row.hash ?? '').slice(0, 12) || 'not recorded'}</span> },
										{
											key: 'action',
											label: 'Action',
											render: (row) => {
												const loading = artifactPreview.loadingId === String(row.id ?? '');
												const downloading = artifactPreview.downloadingId === String(row.id ?? '');
												// Serialize downloads: while any artifact is downloading, every Download
												// button is disabled so two concurrent downloads cannot race on the shared
												// downloadingId/error slot. The active row keeps its "Downloading" label.
												const downloadBusy = Boolean(artifactPreview.downloadingId);
												return (
													<div className="inline" aria-busy={loading || downloading}>
														<button className="button" type="button" aria-label={`Preview artifact ${artifactDisplayName(row)}`} disabled={loading} onClick={() => void artifactPreview.openPreview(row)}>{loading ? 'Opening' : 'Preview'}</button>
														<button className="button" type="button" aria-label={`Download artifact ${artifactDisplayName(row)}`} disabled={downloadBusy} onClick={() => void artifactPreview.download(row)}>{downloading ? 'Downloading' : 'Download'}</button>
													</div>
												);
											},
										},
									]}
								/>
								{artifactPreview.error ? <div className="form-error" role="alert">{artifactPreview.error}</div> : null}
								{artifactPreview.artifact ? (
									<div className="stack">
										<div className="inline">
											<Badge>{String(artifactPreview.artifact.kind ?? 'artifact')}</Badge>
											<span className="mono">sha256 {String(artifactPreview.payload?.hash || artifactPreview.artifact.hash || 'not recorded')}</span>
										</div>
										{artifactPreview.payload?.text ? (
											<pre className="artifact-preview">{redactVisibleText(artifactPreview.payload.text, '')}</pre>
										) : (
											<EmptyState title={artifactPreview.loadingId ? 'Loading artifact' : 'Binary or empty artifact'} body="Non-text artifacts remain downloadable, but are not rendered inline." />
										)}
									</div>
								) : null}
							</Surface>
							{decision.patchGate?.required ? (
								<Surface title="Evidence completeness" flat>
									<div className="inline">
										<Badge tone={decision.patchGate.complete ? 'ok' : 'warn'}>{decision.patchGate.complete ? 'evidence_complete' : 'evidence_incomplete'}</Badge>
										<span>{decision.patchGate.complete ? 'Approve patch can proceed after a human reason.' : 'Approve patch is blocked until evidence is complete.'}</span>
									</div>
									<pre className="artifact-preview">{decision.patchGate.reasons.join('\n')}</pre>
								</Surface>
							) : null}
							{decision.patchGate?.required ? (
								<Surface title="Full diff before approval" flat>
									{decision.patchLoading ? <EmptyState title="Loading patch artifact" body="The diff is read through the protected evidence artifact endpoint." /> : null}
									{decision.patchError ? <div className="form-error" role="alert">{decision.patchError}</div> : null}
									{!decision.patchArtifact ? <EmptyState title="No patch artifact recorded" body="A patch approval requires a linked diff artifact before the approve patch action is enabled." /> : null}
									{decision.patchArtifact && decision.patchPayload?.text && !hasRealPatchChanges(decision.patchPayload.text) ? <EmptyState title="Patch artifact has no proven changes" body="The patch artifact is empty or malformed, so approve patch remains blocked." /> : null}
									{decision.patchPayload?.text ? <pre className="artifact-preview">{redactVisibleText(decision.patchPayload.text, '')}</pre> : null}
								</Surface>
							) : null}
							{decision.patchGate?.required ? (
								<Surface title="Security findings before approval" flat>
									{decision.securityLoading ? <EmptyState title="Loading security findings" body="Security evidence is read through the protected evidence artifact endpoint." /> : null}
									{decision.securityError ? <div className="form-error" role="alert">{decision.securityError}</div> : null}
									{!decision.securityArtifact ? <EmptyState title="No security findings recorded" body="Patch approval requires linked non-blocking security findings before the approve patch action is enabled." /> : null}
									{decision.securityArtifact && decision.securityPayload?.text && !securityFindingsAreNonBlocking(decision.securityPayload) ? <EmptyState title="Security findings are blocking or unreadable" body="The security findings artifact must be valid JSON with no blocking policy decision." /> : null}
									{decision.securityPayload?.text ? <pre className="artifact-preview">{redactVisibleText(decision.securityPayload.text, '')}</pre> : null}
								</Surface>
							) : null}
							<div className="field">
								<label htmlFor="review-decision-reason">{copy.reasonLabel}</label>
								<textarea
									id="review-decision-reason"
									className="textarea"
									value={decision.decisionReason}
									onChange={(event) => {
										decision.setDecisionReason(event.target.value);
										decision.setDecisionError('');
									}}
								/>
								<div className="field-help">{decision.patchGate?.required ? copy.reasonHelpGate : copy.reasonHelpPlain}</div>
							</div>
							{decision.decisionError ? <div className="form-error" role="alert">{decision.decisionError}</div> : null}
							<div className="inline">
								<button className="button primary" type="button" disabled={decision.approveBlocked} onClick={() => void submitDecision('approve')}>
									{requiresGate ? copy.approvePatch : copy.approve}
								</button>
								<button className="button danger" type="button" disabled={decision.decisionBlocked} onClick={() => void submitDecision('reject')}>
									{copy.reject}
								</button>
							</div>
						</div>
					) : null}
				</div>
			</Drawer>

			<Drawer label={copy.shipDrawerLabel} open={Boolean(liveShipItem)} onClose={closeDetail}>
				<div className="drawer-body">
					{liveShipItem ? (
						<div className="stack">
							<div className="inline">
								<Badge tone={toneForStatus(liveShipItem.runStatus ?? undefined)}>{liveShipItem.runStatus}</Badge>
								{liveShipItem.workflowKind ? <Badge>{liveShipItem.workflowKind}</Badge> : null}
								<span className="mono">{shortId(liveShipItem.runId)}</span>
							</div>
							<p className="card-body">{liveShipItem.runLabel}</p>
								<Surface title={copy.evidenceSectionTitle} flat>
									<DataTable
										rows={detailEvidence}
										empty={<EmptyState title="No linked evidence packages" body="This run did not record evidence package references." />}
										columns={[
											{ key: 'id', label: 'Evidence', render: (row) => <span className="mono">{String(row.id ?? '')}</span> },
											{ key: 'verdict', label: 'QA', render: (row) => <Badge tone={toneForStatus(String(row.qaVerdict ?? ''))}>{String(row.qaVerdict ?? '')}</Badge> },
											{ key: 'source', label: 'Source', render: (row) => <span className="mono">{String(row.evidenceSource ?? 'operator_attested')}</span> },
											{ key: 'task', label: 'Task', render: (row) => String(row.taskId ?? '') },
										]}
									/>
									<a className="settings-console-link" href="#evidence">{copy.openFullEvidence}</a>
								</Surface>
								{shipOp ? (
								<>
								{/* Ship operations: promote / create PR (shippable runs only) */}
							<div className="field">
								<label htmlFor="review-ship-reason">{copy.shipReasonLabel}</label>
								<textarea
									id="review-ship-reason"
									className="textarea"
									value={ship.reason}
									onChange={(event) => {
										ship.setReason(event.target.value);
										ship.setError('');
									}}
								/>
								<div className="field-help">{copy.shipReasonHelp}</div>
							</div>
							<Disclosure title={copy.shipAdvanced}>
								<div className="field">
									<label htmlFor="review-ship-branch">{copy.shipBranchLabel}</label>
									<input id="review-ship-branch" className="input" value={ship.branchName} onChange={(event) => ship.setBranchName(event.target.value)} />
								</div>
								<div className="field">
									<label htmlFor="review-ship-pr-title">{copy.shipPrTitleLabel}</label>
									<input id="review-ship-pr-title" className="input" value={ship.pullRequestTitle} onChange={(event) => ship.setPullRequestTitle(event.target.value)} />
								</div>
								<div className="field">
									<label htmlFor="review-ship-pr-base">{copy.shipPrBaseLabel}</label>
									<input id="review-ship-pr-base" className="input" value={ship.pullRequestBaseBranch} onChange={(event) => ship.setPullRequestBaseBranch(event.target.value)} />
								</div>
							</Disclosure>
							{ship.error ? <div className="form-error" role="alert">{ship.error}</div> : null}
							{ship.lastOperation ? (
								<div className="inline" role="status">
									<Badge tone={toneForStatus(ship.lastOperation.status)}>{ship.lastOperation.status}</Badge>
									<span>{redactVisibleText(ship.lastOperation.reason || copy.shipPromoted, copy.shipPromoted)}</span>
									{ship.lastOperation.runId ? <span className="mono">{shortId(ship.lastOperation.runId)}</span> : null}
								</div>
							) : null}
								<div className="inline">
									<button
										className="button primary"
										type="button"
										disabled={!ship.reasonRecorded || Boolean(ship.busyId)}
										onClick={() => void ship.run(shipOp.operation, liveShipItem.runId ?? '', shipOp.kind)}
									>
										{shipOp.operation === 'promote' ? copy.promote : copy.createPr}
									</button>
								</div>
							</>
							) : null}
						</div>
					) : null}
				</div>
			</Drawer>
		</>
	);
}
