/**
 * Governance route page: captures risks, architecture decisions and next steps as
 * first-class operational records with create/update forms, client-side filters and
 * a risk-status workflow; the local merge helpers fold optimistic write results back
 * into the shared Overview snapshot so the UI stays consistent before the next refresh.
 * @author Rodrigo Mason
 */
import { useEffect, useState } from 'react';
import {
	createArchitectureDecision,
	createNextStep,
	createRisk,
	updateRisk,
} from '../../api/client';
import type {
	ArchitectureDecisionStatus,
	NextStepPriority,
	Overview,
	Project,
	RiskSeverity,
	RiskStatus,
} from '../../api/types';
import {
	StatusChip as Badge,
	DataTable,
	EmptyState,
	PageHeader,
	Surface,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';

type Mutate = <T>(
	operation: (token: string) => Promise<T>,
	options?: { awaitRefresh?: boolean },
) => Promise<T>;

function recordTimestamp(record: { updatedAt?: string; createdAt?: string }) {
	const parsed = Date.parse(String(record.updatedAt ?? record.createdAt ?? ''));
	return Number.isNaN(parsed) ? 0 : parsed;
}

function upsertNewestById<T extends { id: string; updatedAt?: string; createdAt?: string }>(
	records: T[],
	incoming: T,
) {
	const existing = records.find((record) => record.id === incoming.id);
	if (existing && recordTimestamp(existing) > recordTimestamp(incoming)) return records;
	return existing
		? records.map((record) => (record.id === incoming.id ? incoming : record))
		: [incoming, ...records];
}

function mergeNewestById<T extends { id: string; updatedAt?: string; createdAt?: string }>(
	current: T[],
	incoming: T[],
) {
	return incoming.reduce((merged, record) => upsertNewestById(merged, record), current);
}

/**
 * Governance console: captures risks, architecture decisions and next steps as
 * first-class operational records (not chat notes), with create/update forms,
 * client-side filters and a risk-status workflow. Writes require an explicit
 * operational project; high/critical risks and accepted decisions enforce extra fields.
 */
export function GovernancePage({
	overview,
	selectedProject,
	mutate,
}: {
	overview: Overview;
	selectedProject: Project | null;
	mutate: Mutate;
}) {
	const { t } = useI18n();
	const project = selectedProject;
	const [riskRows, setRiskRows] = useState(overview.riskRegister);
	const [decisionRows, setDecisionRows] = useState(overview.architectureDecisions);
	const [nextStepRows, setNextStepRows] = useState(overview.nextSteps);
	const [riskTitle, setRiskTitle] = useState('');
	const [riskSeverity, setRiskSeverity] = useState<RiskSeverity>('medium');
	const [riskMitigation, setRiskMitigation] = useState('');
	const [decisionTitle, setDecisionTitle] = useState('');
	const [decisionStatus, setDecisionStatus] = useState<ArchitectureDecisionStatus>('proposed');
	const [decisionContext, setDecisionContext] = useState('');
	const [decisionText, setDecisionText] = useState('');
	const [nextStepTitle, setNextStepTitle] = useState('');
	const [nextStepPriority, setNextStepPriority] = useState<NextStepPriority>('medium');
	const [governanceFilter, setGovernanceFilter] = useState('');
	const [riskStatusFilter, setRiskStatusFilter] = useState<'all' | RiskStatus>('all');
	const [riskUpdateId, setRiskUpdateId] = useState('');
	const [riskUpdateStatus, setRiskUpdateStatus] = useState<RiskStatus>('monitoring');
	const [error, setError] = useState('');
	const [busy, setBusy] = useState(false);
	const query = governanceFilter.trim().toLowerCase();
	const matchesQuery = (...values: Array<string | null | undefined>) =>
		!query ||
		values.some((value) =>
			String(value ?? '')
				.toLowerCase()
				.includes(query),
		);
	const filteredRisks = riskRows.filter(
		(risk) =>
			(riskStatusFilter === 'all' || risk.status === riskStatusFilter) &&
			matchesQuery(risk.title, risk.severity, risk.status, risk.owner, risk.mitigation),
	);
	const filteredDecisions = decisionRows.filter((decision) =>
		matchesQuery(decision.title, decision.status, decision.context, decision.decision),
	);
	const filteredNextSteps = nextStepRows.filter((step) =>
		matchesQuery(step.title, step.priority, step.status, step.owner),
	);
	const selectedRiskId = riskUpdateId || riskRows[0]?.id || '';
	useEffect(() => {
		setRiskRows((current) => mergeNewestById(current, overview.riskRegister));
		setDecisionRows((current) => mergeNewestById(current, overview.architectureDecisions));
		setNextStepRows((current) => mergeNewestById(current, overview.nextSteps));
	}, [overview.architectureDecisions, overview.nextSteps, overview.riskRegister]);
	const saveRisk = async () => {
		if (!riskTitle.trim()) {
			setError(t('ui.static.risk.title.is.required.469006de', 'Risk title is required.'));
			return;
		}
		if (['high', 'critical'].includes(riskSeverity) && !riskMitigation.trim()) {
			setError(
				t(
					'ui.static.high.and.critical.risks.require.mitigation.d2100035',
					'High and critical risks require mitigation.',
				),
			);
			return;
		}
		if (!project) {
			setError(
				t(
					'ui.static.a.project.is.required.before.creating.governance.records.0a36e1b5',
					'A project is required before creating governance records.',
				),
			);
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate(
				(token) =>
					createRisk(token, {
						projectId: project.id,
						title: riskTitle.trim(),
						severity: riskSeverity,
						status: 'open',
						mitigation: riskMitigation.trim(),
						owner: 'technical_lead',
					}),
				{ awaitRefresh: false },
			);
			setRiskRows((current) => upsertNewestById(current, result.risk));
		} catch (saveError) {
			setError(
				saveError instanceof Error
					? saveError.message
					: t('app.pages.errRiskCreation', 'Risk creation failed.'),
			);
		} finally {
			setBusy(false);
		}
	};
	const saveRiskUpdate = async () => {
		if (!selectedRiskId) {
			setError(
				t(
					'ui.static.a.risk.is.required.before.updating.status.d945e7fa',
					'A risk is required before updating status.',
				),
			);
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate(
				(token) => updateRisk(token, selectedRiskId, { status: riskUpdateStatus }),
				{ awaitRefresh: false },
			);
			setRiskRows((current) => upsertNewestById(current, result.risk));
		} catch (saveError) {
			setError(
				saveError instanceof Error
					? saveError.message
					: t('app.pages.errRiskUpdate', 'Risk update failed.'),
			);
		} finally {
			setBusy(false);
		}
	};
	const saveDecision = async () => {
		if (!decisionTitle.trim()) {
			setError(t('ui.static.decision.title.is.required.2f387ea8', 'Decision title is required.'));
			return;
		}
		if (decisionStatus === 'accepted' && (!decisionContext.trim() || !decisionText.trim())) {
			setError(
				t(
					'ui.static.accepted.decisions.require.context.and.decision.text.b5edc6fa',
					'Accepted decisions require context and decision text.',
				),
			);
			return;
		}
		if (!project) {
			setError(
				t(
					'ui.static.a.project.is.required.before.creating.governance.records.0a36e1b5',
					'A project is required before creating governance records.',
				),
			);
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate(
				(token) =>
					createArchitectureDecision(token, {
						projectId: project.id,
						title: decisionTitle.trim(),
						status: decisionStatus,
						context: decisionContext.trim(),
						decision: decisionText.trim(),
						consequences: [],
					}),
				{ awaitRefresh: false },
			);
			setDecisionRows((current) => upsertNewestById(current, result.architectureDecision));
		} catch (saveError) {
			setError(
				saveError instanceof Error
					? saveError.message
					: t('app.pages.errDecisionCreation', 'Decision creation failed.'),
			);
		} finally {
			setBusy(false);
		}
	};
	const saveNextStep = async () => {
		if (!nextStepTitle.trim()) {
			setError(t('ui.static.next.step.title.is.required.92b152bd', 'Next step title is required.'));
			return;
		}
		if (!project) {
			setError(
				t(
					'ui.static.a.project.is.required.before.creating.governance.records.0a36e1b5',
					'A project is required before creating governance records.',
				),
			);
			return;
		}
		setError('');
		setBusy(true);
		try {
			const result = await mutate(
				(token) =>
					createNextStep(token, {
						projectId: project.id,
						title: nextStepTitle.trim(),
						priority: nextStepPriority,
						status: 'planned',
						owner: 'technical_lead',
					}),
				{ awaitRefresh: false },
			);
			setNextStepRows((current) => upsertNewestById(current, result.nextStep));
		} catch (saveError) {
			setError(
				saveError instanceof Error
					? saveError.message
					: t('app.pages.errNextStepCreation', 'Next step creation failed.'),
			);
		} finally {
			setBusy(false);
		}
	};
	return (
		<>
			<PageHeader
				kicker={t('ui.static.engineering.judgement.94b6be3b', 'Engineering judgement')}
				title={t('app.nav.governance', 'Governance')}
				summary={t(
					'ui.static.risks.decisions.and.next.steps.are.operational.records.not.c.121069e8',
					'Risks, decisions and next steps are operational records, not comments buried in chat.',
				)}
			/>
			<div className="grid three">
				<Surface title={t('ui.static.risks.92ddd0d2', 'Risks')}>
					<div className="metric-value">{riskRows.length}</div>
				</Surface>
				<Surface title={t('ui.static.decisions.af2f32cc', 'Decisions')}>
					<div className="metric-value">{decisionRows.length}</div>
				</Surface>
				<Surface title={t('ui.static.next.steps.11fc1420', 'Next steps')}>
					<div className="metric-value">{nextStepRows.length}</div>
				</Surface>
			</div>
			<Surface title={t('ui.static.strict.record.forms.09648e18', 'Strict record forms')}>
				<div className="inline">
					<Badge tone={project ? 'ok' : 'warn'}>
						{project
							? project.name
							: t('app.settings.project.noOperationalBadge', 'no operational project')}
					</Badge>
					{project ? null : (
						<span className="field-help">
							{t(
								'ui.static.select.an.active.operational.project.in.settings.before.crea.cdedb6c4',
								'Select an active operational project in Settings before creating governance records.',
							)}
						</span>
					)}
				</div>
				<div className="grid three">
					<div className="form-grid">
						<div className="field">
							<label htmlFor="risk-title">{t('ui.static.risk.title.d2581c4f', 'Risk title')}</label>
							<input
								id="risk-title"
								className="input"
								value={riskTitle}
								onChange={(event) => setRiskTitle(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="risk-severity">
								{t('ui.static.risk.severity.755bc9ed', 'Risk severity')}
							</label>
							<select
								id="risk-severity"
								className="select"
								value={riskSeverity}
								onChange={(event) => setRiskSeverity(event.target.value as RiskSeverity)}
							>
								<option value="low">low</option>
								<option value="medium">medium</option>
								<option value="high">high</option>
								<option value="critical">critical</option>
							</select>
						</div>
						<div className="field">
							<label htmlFor="risk-mitigation">
								{t('ui.static.risk.mitigation.515b5711', 'Risk mitigation')}
							</label>
							<input
								id="risk-mitigation"
								className="input"
								value={riskMitigation}
								onChange={(event) => setRiskMitigation(event.target.value)}
							/>
						</div>
						<button
							className="button primary"
							type="button"
							disabled={busy}
							onClick={() => {
								void saveRisk();
							}}
						>
							{t('ui.static.save.risk.33c025b0', 'Save risk')}
						</button>
					</div>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="decision-title">
								{t('ui.static.decision.title.ed22b7fe', 'Decision title')}
							</label>
							<input
								id="decision-title"
								className="input"
								value={decisionTitle}
								onChange={(event) => setDecisionTitle(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="decision-status">
								{t('ui.static.decision.status.9f1c54f2', 'Decision status')}
							</label>
							<select
								id="decision-status"
								className="select"
								value={decisionStatus}
								onChange={(event) =>
									setDecisionStatus(event.target.value as ArchitectureDecisionStatus)
								}
							>
								<option value="proposed">proposed</option>
								<option value="accepted">accepted</option>
								<option value="rejected">rejected</option>
								<option value="superseded">superseded</option>
								<option value="deprecated">deprecated</option>
							</select>
						</div>
						<div className="field">
							<label htmlFor="decision-context">
								{t('ui.static.decision.context.b59edd63', 'Decision context')}
							</label>
							<input
								id="decision-context"
								className="input"
								value={decisionContext}
								onChange={(event) => setDecisionContext(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="decision-text">
								{t('ui.static.decision.text.a276ce0a', 'Decision text')}
							</label>
							<input
								id="decision-text"
								className="input"
								value={decisionText}
								onChange={(event) => setDecisionText(event.target.value)}
							/>
						</div>
						<button
							className="button primary"
							type="button"
							disabled={busy}
							onClick={() => {
								void saveDecision();
							}}
						>
							{t('ui.static.save.decision.406fc627', 'Save decision')}
						</button>
					</div>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="next-step-title">
								{t('ui.static.next.step.title.2984700e', 'Next step title')}
							</label>
							<input
								id="next-step-title"
								className="input"
								value={nextStepTitle}
								onChange={(event) => setNextStepTitle(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="next-step-priority">
								{t('ui.static.next.step.priority.1652a14b', 'Next step priority')}
							</label>
							<select
								id="next-step-priority"
								className="select"
								value={nextStepPriority}
								onChange={(event) => setNextStepPriority(event.target.value as NextStepPriority)}
							>
								<option value="low">low</option>
								<option value="medium">medium</option>
								<option value="high">high</option>
								<option value="urgent">urgent</option>
							</select>
						</div>
						<button
							className="button primary"
							type="button"
							disabled={busy}
							onClick={() => {
								void saveNextStep();
							}}
						>
							{t('ui.static.save.next.step.7e6ec275', 'Save next step')}
						</button>
					</div>
				</div>
				{error ? (
					<div className="form-error" role="alert">
						{error}
					</div>
				) : null}
			</Surface>
			<div className="grid two">
				<Surface title={t('ui.static.governance.filters.7ded1ef3', 'Governance filters')}>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="governance-filter">
								{t('ui.static.governance.filter.092bf483', 'Governance filter')}
							</label>
							<input
								id="governance-filter"
								className="input"
								value={governanceFilter}
								onChange={(event) => setGovernanceFilter(event.target.value)}
								placeholder={t(
									'ui.static.filter.risks.decisions.and.next.steps.17f70033',
									'Filter risks, decisions, and next steps',
								)}
							/>
						</div>
						<div className="field">
							<label htmlFor="risk-status-filter">
								{t('ui.static.risk.status.filter.5a81dbb9', 'Risk status filter')}
							</label>
							<select
								id="risk-status-filter"
								className="select"
								value={riskStatusFilter}
								onChange={(event) => setRiskStatusFilter(event.target.value as 'all' | RiskStatus)}
							>
								<option value="all">all</option>
								<option value="open">open</option>
								<option value="monitoring">monitoring</option>
								<option value="mitigating">mitigating</option>
								<option value="mitigated">mitigated</option>
								<option value="accepted">accepted</option>
								<option value="closed">closed</option>
							</select>
						</div>
					</div>
				</Surface>
				<Surface title={t('ui.static.risk.update.form.63b4d923', 'Risk update form')}>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="risk-update-id">
								{t('ui.static.risk.to.update.4f30deec', 'Risk to update')}
							</label>
							<select
								id="risk-update-id"
								className="select"
								value={selectedRiskId}
								disabled={!riskRows.length}
								onChange={(event) => setRiskUpdateId(event.target.value)}
							>
								{riskRows.map((risk) => (
									<option key={risk.id} value={risk.id}>
										{risk.title}
									</option>
								))}
							</select>
						</div>
						<div className="field">
							<label htmlFor="risk-update-status">
								{t('ui.static.risk.update.status.b82f73d5', 'Risk update status')}
							</label>
							<select
								id="risk-update-status"
								className="select"
								value={riskUpdateStatus}
								onChange={(event) => setRiskUpdateStatus(event.target.value as RiskStatus)}
							>
								<option value="open">open</option>
								<option value="monitoring">monitoring</option>
								<option value="mitigating">mitigating</option>
								<option value="mitigated">mitigated</option>
								<option value="accepted">accepted</option>
								<option value="closed">closed</option>
							</select>
						</div>
						<button
							className="button primary"
							type="button"
							disabled={!selectedRiskId || busy}
							onClick={() => {
								void saveRiskUpdate();
							}}
						>
							{t('ui.static.update.risk.status.4ed521f0', 'Update risk status')}
						</button>
					</div>
				</Surface>
			</div>
			<div className="grid three">
				<Surface title={t('ui.static.risk.register.e2cb59b0', 'Risk register')}>
					<DataTable
						rows={filteredRisks}
						empty={
							<EmptyState
								title={t('ui.static.no.risks.df25a300', 'No risks')}
								body={t(
									'ui.static.open.technical.and.product.risks.appear.here.68f75ad6',
									'Open technical and product risks appear here.',
								)}
							/>
						}
						columns={[
							{
								key: 'title',
								label: t('ui.static.risk.5a8f23f5', 'Risk'),
								render: (row) => String(row.title ?? ''),
							},
							{
								key: 'severity',
								label: t('app.workbench.logs.colSeverity', 'Severity'),
								render: (row) => (
									<Badge tone={toneForStatus(String(row.severity ?? ''))}>
										{String(row.severity ?? '')}
									</Badge>
								),
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
				<Surface title={t('ui.static.decision.records.606d98ca', 'Decision records')}>
					<DataTable
						rows={filteredDecisions}
						empty={
							<EmptyState
								title={t('ui.static.no.decisions.15ab90ea', 'No decisions')}
								body={t(
									'ui.static.architecture.decisions.should.be.explicit.and.linked.to.risk.0bd4f789',
									'Architecture decisions should be explicit and linked to risks.',
								)}
							/>
						}
						columns={[
							{
								key: 'title',
								label: t('ui.static.decision.7f59a1f1', 'Decision'),
								render: (row) => String(row.title ?? ''),
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
				<Surface title={t('ui.static.next.steps.11fc1420', 'Next steps')}>
					<DataTable
						rows={filteredNextSteps}
						empty={
							<EmptyState
								title={t('ui.static.no.next.steps.dcb989e0', 'No next steps')}
								body={t(
									'ui.static.mitigations.and.follow.up.work.appear.here.3535c646',
									'Mitigations and follow-up work appear here.',
								)}
							/>
						}
						columns={[
							{
								key: 'title',
								label: t('ui.static.step.dc416e10', 'Step'),
								render: (row) => String(row.title ?? ''),
							},
							{
								key: 'priority',
								label: t('ui.static.priority.886cbff9', 'Priority'),
								render: (row) => (
									<Badge tone={toneForStatus(String(row.priority ?? ''))}>
										{String(row.priority ?? '')}
									</Badge>
								),
							},
						]}
					/>
				</Surface>
			</div>
		</>
	);
}
