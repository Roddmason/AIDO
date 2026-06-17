/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import type {
	ModelGatewayBenchmark,
	ModelGatewayBenchmarkOutcome,
	ModelGatewayModel,
	ModelGatewayProviderAccount,
} from '../../api/types';
import { Badge, DataTable, EmptyState } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { PanelShell } from './PanelShell';
import { boolLabel, EXECUTABLE_AGENT_ROLES, money, text } from './utils';

type Translate = (key: string, fallback?: string) => string;

type BenchmarkForm = {
	provider: string;
	model: string;
	runtime: string;
	role: string;
	success: boolean;
	qaPass: boolean;
	rework: boolean;
	cost: string;
	latency: string;
	provenance: 'operator_reported';
};

function provenanceLabel(provenance: unknown, t: Translate) {
	const value = text(provenance, 'operator_reported');
	if (value === 'automated_run') return t('app.modelGateway.benchmark.provenance.automatedRun', 'Automated run');
	if (value === 'release_validation') return t('app.modelGateway.benchmark.provenance.releaseValidation', 'Release validation');
	return t('app.modelGateway.benchmark.provenance.operatorReported', 'Manual/operator-reported');
}

function provenanceTone(provenance: unknown) {
	const value = text(provenance, 'operator_reported');
	return value === 'operator_reported' ? 'warn' : 'ok';
}

function objectiveCount(row: ModelGatewayBenchmark) {
	return Number(row.objectiveTasksAttempted ?? 0);
}

function operatorReportedCount(row: ModelGatewayBenchmark) {
	return Number(row.operatorReportedTasks ?? 0);
}

function benchmarkValidity(row: ModelGatewayBenchmark, t: Translate) {
	if (objectiveCount(row) === 0 && operatorReportedCount(row) > 0) {
		return <Badge tone="warn">{t('ui.static.manual.reports.are.not.objective.proof.benchmark.provenance', 'Manual reports are not objective proof')}</Badge>;
	}
	if (row.insufficientData) return <Badge tone="warn">{t('app.modelGateway.benchmark.validity.insufficientObjective', 'insufficient objective data')}</Badge>;
	return <Badge tone="ok">{t('app.modelGateway.benchmark.validity.objectiveEvidence', 'objective benchmark evidence')}</Badge>;
}

export function BenchmarksPanel({
	benchmarks,
	benchmarkOutcomes,
	providers,
	models,
	form,
	modelOptions,
	busyAction,
	error,
	onChange,
	onProviderChange,
	onSubmit,
}: {
	benchmarks: ModelGatewayBenchmark[];
	benchmarkOutcomes: ModelGatewayBenchmarkOutcome[];
	providers: ModelGatewayProviderAccount[];
	models: ModelGatewayModel[];
	form: BenchmarkForm;
	modelOptions: string[];
	busyAction: string;
	error: string;
	onChange: (field: keyof BenchmarkForm, value: string | boolean) => void;
	onProviderChange: (provider: string, firstModel: string | undefined) => void;
	onSubmit: () => void;
}) {
	const { t } = useI18n();
	return (
		<PanelShell title={t('ui.static.benchmarks.4f46ac72', 'Benchmarks')}>
			<div className="form-grid">
				<div className="grid three">
					<div className="field">
						<label htmlFor="benchmark-provider">{t('ui.static.benchmark.provider.8264f17b', 'Benchmark provider')}</label>
						<select
							id="benchmark-provider"
							className="select"
							value={form.provider}
							onChange={(event) => {
								const provider = event.target.value;
								const firstModel = models.find((item) => item.providerId === provider)?.model;
								onProviderChange(provider, firstModel ? text(firstModel, form.model) : undefined);
							}}
						>
							{Array.from(new Set(['codex_cli', 'claude_code_cli', 'nvidia_nim', ...providers.map((item) => text(item.providerId, '')).filter(Boolean)])).map((provider) => (
								<option key={provider} value={provider}>{provider}</option>
							))}
						</select>
					</div>
					<div className="field">
						<label htmlFor="benchmark-model">{t('ui.static.benchmark.model.415919bb', 'Benchmark model')}</label>
						<select id="benchmark-model" className="select" value={form.model} onChange={(event) => onChange('model', event.target.value)}>
							{modelOptions.map((model) => <option key={model} value={model}>{model}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="benchmark-runtime">{t('ui.static.benchmark.runtime.165ef7f8', 'Benchmark runtime')}</label>
						<select id="benchmark-runtime" className="select" value={form.runtime} onChange={(event) => onChange('runtime', event.target.value)}>
							{['api', 'cli', 'local', 'gateway', 'manual'].map((runtime) => <option key={runtime} value={runtime}>{runtime}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="benchmark-role">{t('ui.static.benchmark.role.8c2e2bcc', 'Benchmark role')}</label>
						<select id="benchmark-role" className="select" value={form.role} onChange={(event) => onChange('role', event.target.value)}>
							{EXECUTABLE_AGENT_ROLES.map((role) => <option key={role} value={role}>{role}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="benchmark-cost">{t('ui.static.benchmark.cost.usd.dfa6493c', 'Benchmark cost USD')}</label>
						<input id="benchmark-cost" className="input" type="number" min="0" step="0.01" value={form.cost} onChange={(event) => onChange('cost', event.target.value)} />
					</div>
					<div className="field">
						<label htmlFor="benchmark-latency">{t('ui.static.benchmark.latency.ms.5025b2b7', 'Benchmark latency ms')}</label>
						<input id="benchmark-latency" className="input" type="number" min="0" step="1" value={form.latency} onChange={(event) => onChange('latency', event.target.value)} />
					</div>
				</div>
				<div className="inline">
					<Badge tone="warn">{provenanceLabel(form.provenance, t)}</Badge>
					<span className="muted">{t('ui.static.manual.reports.are.not.objective.proof.period.benchmark.provenance', 'Manual reports are not objective proof.')}</span>
					<label className="checkbox-row" htmlFor="benchmark-success"><input id="benchmark-success" type="checkbox" checked={form.success} onChange={(event) => onChange('success', event.target.checked)} />{t('ui.static.benchmark.success.154d2b71', 'Benchmark success')}</label>
					<label className="checkbox-row" htmlFor="benchmark-qa"><input id="benchmark-qa" type="checkbox" checked={form.qaPass} onChange={(event) => onChange('qaPass', event.target.checked)} />{t('ui.static.benchmark.qa.pass.3bceae7f', 'Benchmark QA pass')}</label>
					<label className="checkbox-row" htmlFor="benchmark-rework"><input id="benchmark-rework" type="checkbox" checked={form.rework} onChange={(event) => onChange('rework', event.target.checked)} />{t('ui.static.benchmark.rework.c9d74673', 'Benchmark rework')}</label>
				</div>
				{error ? <div className="form-error" role="alert">{error}</div> : null}
				<button className="button primary" type="button" disabled={busyAction === 'record-benchmark-outcome'} onClick={() => onSubmit()}>{t('ui.static.record.benchmark.outcome.a0345975', 'Record benchmark outcome')}</button>
			</div>
			<DataTable rows={benchmarks} empty={<EmptyState title={t('ui.static.insufficient.data.ccc693ba', 'insufficient data')} body={t('ui.static.benchmarks.require.repeated.task.outcomes.before.success.rat.301f8db9', 'Benchmarks require repeated task outcomes before success rate, QA pass rate, cost, latency or rework rate can be shown.')} />} columns={[
				{ key: 'provider', label: t('ui.static.provider.7ceee3f3', 'Provider'), render: (row) => text(row.providerId) },
				{ key: 'model', label: t('ui.static.model.68c2cc7f', 'Model'), render: (row) => text(row.model) },
				{ key: 'role', label: t('ui.static.role.c3f104d1', 'Role'), render: (row) => text(row.role) },
				{ key: 'attempts', label: t('ui.static.tasks.attempted.dd3ed12f', 'Tasks attempted'), render: (row) => text(row.tasksAttempted, '0') },
				{ key: 'evidenceMix', label: t('ui.static.evidence.mix.benchmark.provenance', 'Evidence mix'), render: (row) => `${objectiveCount(row)} objective / ${operatorReportedCount(row)} manual` },
				{ key: 'validity', label: t('ui.static.validity.benchmark.provenance', 'Validity'), render: (row) => benchmarkValidity(row, t) },
				{ key: 'success', label: t('ui.static.success.rate.12374104', 'Success rate'), render: (row) => row.successRate === null || row.successRate === undefined ? t('ui.static.insufficient.data.ccc693ba', 'insufficient data') : `${(Number(row.successRate) * 100).toFixed(2)}%` },
				{ key: 'qa', label: t('ui.static.qa.pass.rate.f00a47c8', 'QA pass rate'), render: (row) => row.qaPassRate === null || row.qaPassRate === undefined ? t('ui.static.insufficient.data.ccc693ba', 'insufficient data') : `${(Number(row.qaPassRate) * 100).toFixed(2)}%` },
				{ key: 'cost', label: t('ui.static.avg.cost.4fbb932f', 'Avg cost'), render: (row) => money(row.avgCost) },
				{ key: 'latency', label: t('ui.static.avg.latency.da781428', 'Avg latency'), render: (row) => text(row.avgLatencyMs) },
				{ key: 'rework', label: t('ui.static.rework.rate.f1b0babc', 'Rework rate'), render: (row) => row.reworkRate === null || row.reworkRate === undefined ? t('ui.static.insufficient.data.ccc693ba', 'insufficient data') : `${(Number(row.reworkRate) * 100).toFixed(2)}%` },
				{ key: 'last', label: t('ui.static.last.used.f1109d3d', 'Last used'), render: (row) => text(row.lastUsedAt) },
			]} />
			<DataTable rows={benchmarkOutcomes} empty={<EmptyState title={t('ui.static.no.benchmark.outcomes.0ddd5c18', 'No benchmark outcomes')} body={t('ui.static.outcome.records.appear.after.benchmark.or.task.result.ingest.76a6d23e', 'Outcome records appear after benchmark or task result ingestion.')} />} columns={[
				{ key: 'provider', label: t('ui.static.provider.7ceee3f3', 'Provider'), render: (row) => text(row.providerId) },
				{ key: 'model', label: t('ui.static.model.68c2cc7f', 'Model'), render: (row) => text(row.model) },
				{ key: 'role', label: t('ui.static.role.c3f104d1', 'Role'), render: (row) => text(row.role) },
				{
					key: 'provenance',
					label: t('ui.static.provenance.benchmark.provenance', 'Provenance'),
					render: (row) => (
						<div className="stack">
							<Badge tone={provenanceTone(row.provenance)}>{provenanceLabel(row.provenance, t)}</Badge>
							<span className="mono">{text(row.provenance, 'operator_reported')}</span>
						</div>
					),
				},
				{ key: 'success', label: t('ui.static.success.42a8f651', 'Success'), render: (row) => boolLabel(row.success) },
				{ key: 'qa', label: t('ui.static.qa.pass.b8cbfae8', 'QA pass'), render: (row) => boolLabel(row.qaPass) },
				{ key: 'rework', label: t('ui.static.rework.0ca516fc', 'Rework'), render: (row) => boolLabel(row.rework) },
				{ key: 'cost', label: t('ui.static.cost.64ae43e8', 'Cost'), render: (row) => money(row.estimatedCostUsd) },
				{ key: 'latency', label: t('ui.static.latency.3e399725', 'Latency'), render: (row) => text(row.latencyMs) },
				{ key: 'time', label: t('ui.static.time.6c82e6dd', 'Time'), render: (row) => text(row.createdAt) },
			]} />
		</PanelShell>
	);
}
