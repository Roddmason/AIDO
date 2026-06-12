import type { ModelGatewayRoutePreviewResponse } from '../../api/client';
import { Badge, DataTable, EmptyState, Surface } from '../../components/primitives';
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
	const candidateRows = preview?.candidates ?? [];
	const hasOperatorReportedBenchmarks = candidateRows.some(
		(row) => Number(row.scoreBreakdown?.benchmarkOperatorReportedSampleCount ?? 0) > 0,
	);
	const scoreMetric = (row: ModelGatewayRoutePreviewResponse['candidates'][number], key: string) => (
		Number(row.scoreBreakdown?.[key] ?? 0).toFixed(2)
	);
	const unknownCostPolicy = preview?.policyResult?.unknownCostPolicy;
	const unknownCostAction = typeof unknownCostPolicy?.action === 'string' ? unknownCostPolicy.action : '';
	const unknownCostReason = typeof unknownCostPolicy?.reason === 'string' ? unknownCostPolicy.reason : '';
	return (
		<PanelShell title="Route Preview">
			<div className="form-grid">
				<div className="grid three">
					<div className="field">
						<label htmlFor="route-preview-role">Preview role</label>
						<select id="route-preview-role" className="select" value={form.role} onChange={(event) => onChange('role', event.target.value)}>
							{EXECUTABLE_AGENT_ROLES.map((role) => (
								<option key={role} value={role}>{role}</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="route-preview-mode">Preview mode</label>
						<select id="route-preview-mode" className="select" value={form.mode} onChange={(event) => onChange('mode', event.target.value)}>
							{['free_first', 'cost_controlled', 'balanced_best_value', 'max_performance', 'manual_by_profile', 'local_private'].map((mode) => (
								<option key={mode} value={mode}>{mode}</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="route-preview-task-type">Preview task type</label>
						<input id="route-preview-task-type" className="input" value={form.taskType} onChange={(event) => onChange('taskType', event.target.value)} />
					</div>
					<div className="field">
						<label htmlFor="route-preview-risk">Preview risk level</label>
						<select id="route-preview-risk" className="select" value={form.risk} onChange={(event) => onChange('risk', event.target.value)}>
							{['low', 'medium', 'high', 'critical'].map((risk) => <option key={risk} value={risk}>{risk}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="route-preview-context">Preview context tokens</label>
						<input id="route-preview-context" className="input" type="number" min="0" value={form.tokens} onChange={(event) => onChange('tokens', event.target.value)} />
					</div>
					<div className="field">
						<label htmlFor="route-preview-budget">Preview budget remaining USD</label>
						<input id="route-preview-budget" className="input" type="number" min="0" step="0.01" value={form.budget} onChange={(event) => onChange('budget', event.target.value)} />
					</div>
					<div className="field">
						<label htmlFor="route-preview-privacy">Preview privacy level</label>
						<select id="route-preview-privacy" className="select" value={form.privacy} onChange={(event) => onChange('privacy', event.target.value)}>
							<option value="remote_allowed">remote_allowed</option>
							<option value="sensitive">sensitive</option>
							<option value="local_private">local_private</option>
						</select>
					</div>
				</div>
				<div className="inline">
					<label className="checkbox-row" htmlFor="route-preview-code-edit"><input id="route-preview-code-edit" type="checkbox" checked={form.requiresCodeEdit} onChange={(event) => onChange('requiresCodeEdit', event.target.checked)} />Preview requires code edit</label>
					<label className="checkbox-row" htmlFor="route-preview-tools"><input id="route-preview-tools" type="checkbox" checked={form.requiresTools} onChange={(event) => onChange('requiresTools', event.target.checked)} />Preview requires tools</label>
					<label className="checkbox-row" htmlFor="route-preview-search"><input id="route-preview-search" type="checkbox" checked={form.requiresSearch} onChange={(event) => onChange('requiresSearch', event.target.checked)} />Preview requires search</label>
					<label className="checkbox-row" htmlFor="route-preview-reasoning"><input id="route-preview-reasoning" type="checkbox" checked={form.requiresReasoning} onChange={(event) => onChange('requiresReasoning', event.target.checked)} />Preview requires reasoning</label>
					<label className="checkbox-row" htmlFor="route-preview-json"><input id="route-preview-json" type="checkbox" checked={form.requiresJson} onChange={(event) => onChange('requiresJson', event.target.checked)} />Preview requires JSON</label>
				</div>
				<button className="button primary" type="button" disabled={busyAction === 'route-preview'} onClick={() => onSubmit()}>
					Preview route
				</button>
				{preview ? (
					<div className="stack" aria-live="polite">
						<h3 className="surface-title">Selected route</h3>
						<div className="inline">
							<Badge tone={preview.selected ? 'ok' : 'warn'}>{preview.selected?.provider ?? 'none'}</Badge>
							<Badge>{preview.selected?.model ?? 'no model'}</Badge>
							<Badge>{preview.selected?.runtime ?? 'no runtime'}</Badge>
							<Badge>{preview.selected?.effort ?? 'default effort'}</Badge>
							<Badge>{money(preview.estimatedCostUsd)}</Badge>
							{unknownCostAction ? <Badge tone={unknownCostTone(unknownCostAction)}>unknown cost: {unknownCostAction}</Badge> : null}
						</div>
						<div className="muted">{preview.decisionReason}</div>
						{unknownCostReason ? <div className="muted">Unknown cost policy: {unknownCostReason}</div> : null}
						<div className="grid two">
							<Surface flat>
								<div className="metric-label">Budget result</div>
								<div className="mono">{JSON.stringify(preview.budgetResult ?? {})}</div>
							</Surface>
							<Surface flat>
								<div className="metric-label">Quota result</div>
								<div className="mono">{JSON.stringify(preview.quotaResult ?? {})}</div>
							</Surface>
						</div>
						<Surface flat>
							<h3 className="surface-title">Candidate scores</h3>
							{hasOperatorReportedBenchmarks ? <div className="muted">Manual benchmarks are not objective proof.</div> : null}
							<DataTable rows={candidateRows} empty={<EmptyState title="No candidates" body="Hard filters rejected every provider/model candidate." />} columns={[
								{ key: 'provider', label: 'Provider', render: (row) => <span className="mono">{row.provider}</span> },
								{ key: 'model', label: 'Model', render: (row) => <span className="mono">{row.model}</span> },
								{ key: 'runtime', label: 'Runtime', render: (row) => <Badge>{row.runtime}</Badge> },
								{ key: 'score', label: 'Score', render: (row) => Number(row.score ?? 0).toFixed(6) },
								{ key: 'objectiveSamples', label: 'Objective samples', render: (row) => scoreMetric(row, 'benchmarkObjectiveSampleCount') },
								{ key: 'manualSamples', label: 'Manual samples', render: (row) => scoreMetric(row, 'benchmarkOperatorReportedSampleCount') },
								{ key: 'benchmarkScore', label: 'Benchmark score', render: (row) => scoreMetric(row, 'benchmarkScore') },
								{ key: 'benchmarkContribution', label: 'Benchmark contribution', render: (row) => scoreMetric(row, 'benchmarkContribution') },
							]} />
						</Surface>
					</div>
				) : null}
			</div>
		</PanelShell>
	);
}
