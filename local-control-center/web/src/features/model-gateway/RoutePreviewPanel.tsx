/**
 * Simulador de ruteo del Model Gateway: arma los parámetros de una tarea hipotética y muestra qué ruta elegiría.
 * Sub-dominio de previsualización (dry-run, sin ejecutar): renderiza la ruta seleccionada, los resultados de
 * presupuesto/cuota y los candidatos con su desglose de score. El estado del formulario es controlado por el padre.
 * @author Rodrigo Mason
 */
import type { ModelGatewayRoutePreviewResponse } from '../../api/client';
import { StatusChip as Badge, DataTable, EmptyState, Surface } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { PanelShell } from './PanelShell';
import { EXECUTABLE_AGENT_ROLES, money } from './utils';

type RoutePreviewForm = {
	role: string;
	mode: string;
	taskType: string;
	risk: string;
	tokens: string;
	budget: string;
	privacy: string;
	requiresCodeEdit: boolean;
	requiresTools: boolean;
	requiresSearch: boolean;
	requiresReasoning: boolean;
	requiresJson: boolean;
};

/** Mapea la acción de política de costo desconocido (reject/require_approval/otro) al tono del badge. */
function unknownCostTone(action: string): 'ok' | 'warn' | 'danger' {
	if (action === 'reject') return 'danger';
	if (action === 'require_approval') return 'warn';
	return 'ok';
}

export function RoutePreviewPanel({
	form,
	preview,
	busyAction,
	onChange,
	onSubmit,
}: {
	form: RoutePreviewForm;
	preview: ModelGatewayRoutePreviewResponse | null;
	busyAction: string;
	onChange: (field: keyof RoutePreviewForm, value: string | boolean) => void;
	onSubmit: () => void;
}) {
	const { t } = useI18n();
	const candidateRows = preview?.candidates ?? [];
	const hasOperatorReportedBenchmarks = candidateRows.some(
		(row) => Number(row.scoreBreakdown?.benchmarkOperatorReportedSampleCount ?? 0) > 0,
	);
	const scoreMetric = (row: ModelGatewayRoutePreviewResponse['candidates'][number], key: string) =>
		Number(row.scoreBreakdown?.[key] ?? 0).toFixed(2);
	const unknownCostPolicy = preview?.policyResult?.unknownCostPolicy;
	const unknownCostAction =
		typeof unknownCostPolicy?.action === 'string' ? unknownCostPolicy.action : '';
	const unknownCostReason =
		typeof unknownCostPolicy?.reason === 'string' ? unknownCostPolicy.reason : '';
	return (
		<PanelShell title={t('ui.static.route.preview.44d02743', 'Route Preview')}>
			<div className="form-grid">
				<div className="grid three">
					<div className="field">
						<label htmlFor="route-preview-role">
							{t('ui.static.preview.role.6006d236', 'Preview role')}
						</label>
						<select
							id="route-preview-role"
							className="select"
							value={form.role}
							onChange={(event) => onChange('role', event.target.value)}
						>
							{EXECUTABLE_AGENT_ROLES.map((role) => (
								<option key={role} value={role}>
									{role}
								</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="route-preview-mode">
							{t('ui.static.preview.mode.5eadd20c', 'Preview mode')}
						</label>
						<select
							id="route-preview-mode"
							className="select"
							value={form.mode}
							onChange={(event) => onChange('mode', event.target.value)}
						>
							{[
								{
									value: 'free_tier',
									label: t('app.modelGateway.routeMode.freeTier', 'Free tier only'),
								},
								{ value: 'free_first', label: 'free_first' },
								{ value: 'cost_controlled', label: 'cost_controlled' },
								{ value: 'balanced_best_value', label: 'balanced_best_value' },
								{ value: 'max_performance', label: 'max_performance' },
								{ value: 'manual_by_profile', label: 'manual_by_profile' },
								{ value: 'local_private', label: 'local_private' },
							].map((mode) => (
								<option key={mode.value} value={mode.value}>
									{mode.label}
								</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="route-preview-task-type">
							{t('ui.static.preview.task.type.9364293f', 'Preview task type')}
						</label>
						<input
							id="route-preview-task-type"
							className="input"
							value={form.taskType}
							onChange={(event) => onChange('taskType', event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="route-preview-risk">
							{t('ui.static.preview.risk.level.4554aa8e', 'Preview risk level')}
						</label>
						<select
							id="route-preview-risk"
							className="select"
							value={form.risk}
							onChange={(event) => onChange('risk', event.target.value)}
						>
							{['low', 'medium', 'high', 'critical'].map((risk) => (
								<option key={risk} value={risk}>
									{risk}
								</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="route-preview-context">
							{t('ui.static.preview.context.tokens.eaf223ff', 'Preview context tokens')}
						</label>
						<input
							id="route-preview-context"
							className="input"
							type="number"
							min="0"
							value={form.tokens}
							onChange={(event) => onChange('tokens', event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="route-preview-budget">
							{t('ui.static.preview.budget.remaining.usd.b6198c06', 'Preview budget remaining USD')}
						</label>
						<input
							id="route-preview-budget"
							className="input"
							type="number"
							min="0"
							step="0.01"
							value={form.budget}
							onChange={(event) => onChange('budget', event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="route-preview-privacy">
							{t('ui.static.preview.privacy.level.c675b6cb', 'Preview privacy level')}
						</label>
						<select
							id="route-preview-privacy"
							className="select"
							value={form.privacy}
							onChange={(event) => onChange('privacy', event.target.value)}
						>
							<option value="remote_allowed">remote_allowed</option>
							<option value="sensitive">sensitive</option>
							<option value="local_private">local_private</option>
						</select>
					</div>
				</div>
				<div className="inline">
					<label className="checkbox-row" htmlFor="route-preview-code-edit">
						<input
							id="route-preview-code-edit"
							type="checkbox"
							checked={form.requiresCodeEdit}
							onChange={(event) => onChange('requiresCodeEdit', event.target.checked)}
						/>
						{t('ui.static.preview.requires.code.edit.1796861a', 'Preview requires code edit')}
					</label>
					<label className="checkbox-row" htmlFor="route-preview-tools">
						<input
							id="route-preview-tools"
							type="checkbox"
							checked={form.requiresTools}
							onChange={(event) => onChange('requiresTools', event.target.checked)}
						/>
						{t('ui.static.preview.requires.tools.558ce0d7', 'Preview requires tools')}
					</label>
					<label className="checkbox-row" htmlFor="route-preview-search">
						<input
							id="route-preview-search"
							type="checkbox"
							checked={form.requiresSearch}
							onChange={(event) => onChange('requiresSearch', event.target.checked)}
						/>
						{t('ui.static.preview.requires.search.95be6046', 'Preview requires search')}
					</label>
					<label className="checkbox-row" htmlFor="route-preview-reasoning">
						<input
							id="route-preview-reasoning"
							type="checkbox"
							checked={form.requiresReasoning}
							onChange={(event) => onChange('requiresReasoning', event.target.checked)}
						/>
						{t('ui.static.preview.requires.reasoning.05c8b79d', 'Preview requires reasoning')}
					</label>
					<label className="checkbox-row" htmlFor="route-preview-json">
						<input
							id="route-preview-json"
							type="checkbox"
							checked={form.requiresJson}
							onChange={(event) => onChange('requiresJson', event.target.checked)}
						/>
						{t('ui.static.preview.requires.json.992cec24', 'Preview requires JSON')}
					</label>
				</div>
				<button
					className="button primary"
					type="button"
					disabled={busyAction === 'route-preview'}
					onClick={() => onSubmit()}
				>
					{t('app.modelGateway.routePreview.submit', 'Preview route')}
				</button>
				{preview ? (
					<div className="stack" aria-live="polite">
						<h3 className="surface-title">
							{t('ui.static.selected.route.508f3793', 'Selected route')}
						</h3>
						<div className="inline">
							<Badge tone={preview.selected ? 'ok' : 'warn'}>
								{preview.selected?.provider ?? t('app.workspace.detection.none', 'none')}
							</Badge>
							<Badge>
								{preview.selected?.model ?? t('app.modelGateway.routePreview.noModel', 'no model')}
							</Badge>
							<Badge>
								{preview.selected?.runtime ??
									t('app.modelGateway.routePreview.noRuntime', 'no runtime')}
							</Badge>
							<Badge>
								{preview.selected?.effort ??
									t('app.modelGateway.routePreview.defaultEffort', 'default effort')}
							</Badge>
							<Badge>
								{money(preview.estimatedCostUsd, t('app.runtime.card.unknown', 'unknown'))}
							</Badge>
							{unknownCostAction ? (
								<Badge tone={unknownCostTone(unknownCostAction)}>
									{t('app.modelGateway.routePreview.unknownCostBadge', 'unknown cost')}:{' '}
									{unknownCostAction}
								</Badge>
							) : null}
						</div>
						<div className="muted">{preview.decisionReason}</div>
						{unknownCostReason ? (
							<div className="muted">
								{t('app.modelGateway.routePreview.unknownCostPolicy', 'Unknown cost policy')}:{' '}
								{unknownCostReason}
							</div>
						) : null}
						<div className="grid two">
							<Surface flat>
								<div className="metric-label">
									{t('ui.static.budget.result.e9ecd30f', 'Budget result')}
								</div>
								<div className="mono">{JSON.stringify(preview.budgetResult ?? {})}</div>
							</Surface>
							<Surface flat>
								<div className="metric-label">
									{t('ui.static.quota.result.38fd1beb', 'Quota result')}
								</div>
								<div className="mono">{JSON.stringify(preview.quotaResult ?? {})}</div>
							</Surface>
						</div>
						<Surface flat>
							<h3 className="surface-title">
								{t('ui.static.candidate.scores.benchmark.provenance', 'Candidate scores')}
							</h3>
							{hasOperatorReportedBenchmarks ? (
								<div className="muted">
									{t(
										'ui.static.manual.benchmarks.are.not.objective.proof.benchmark.provenance',
										'Manual benchmarks are not objective proof.',
									)}
								</div>
							) : null}
							<DataTable
								rows={candidateRows}
								empty={
									<EmptyState
										title={t('ui.static.no.candidates.benchmark.provenance', 'No candidates')}
										body={t(
											'ui.static.hard.filters.rejected.every.provider.model.candidate.benchmark.provenance',
											'Hard filters rejected every provider/model candidate.',
										)}
									/>
								}
								columns={[
									{
										key: 'provider',
										label: t('ui.static.provider.7ceee3f3', 'Provider'),
										render: (row) => <span className="mono">{row.provider}</span>,
									},
									{
										key: 'model',
										label: t('ui.static.model.68c2cc7f', 'Model'),
										render: (row) => <span className="mono">{row.model}</span>,
									},
									{
										key: 'runtime',
										label: t('ui.static.runtime.c4740e4c', 'Runtime'),
										render: (row) => <Badge>{row.runtime}</Badge>,
									},
									{
										key: 'score',
										label: t('ui.static.score.benchmark.provenance', 'Score'),
										render: (row) => Number(row.score ?? 0).toFixed(6),
									},
									{
										key: 'objectiveSamples',
										label: t(
											'ui.static.objective.samples.benchmark.provenance',
											'Objective samples',
										),
										render: (row) => scoreMetric(row, 'benchmarkObjectiveSampleCount'),
									},
									{
										key: 'manualSamples',
										label: t('ui.static.manual.samples.benchmark.provenance', 'Manual samples'),
										render: (row) => scoreMetric(row, 'benchmarkOperatorReportedSampleCount'),
									},
									{
										key: 'benchmarkScore',
										label: t('ui.static.benchmark.score.benchmark.provenance', 'Benchmark score'),
										render: (row) => scoreMetric(row, 'benchmarkScore'),
									},
									{
										key: 'benchmarkContribution',
										label: t(
											'ui.static.benchmark.contribution.benchmark.provenance',
											'Benchmark contribution',
										),
										render: (row) => scoreMetric(row, 'benchmarkContribution'),
									},
								]}
							/>
						</Surface>
					</div>
				) : null}
			</div>
		</PanelShell>
	);
}
