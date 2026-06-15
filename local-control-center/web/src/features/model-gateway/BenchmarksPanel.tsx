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
import { PanelShell } from './PanelShell';
import { boolLabel, EXECUTABLE_AGENT_ROLES, money, text } from './utils';

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

function provenanceLabel(provenance: unknown) {
	const value = text(provenance, 'operator_reported');
	if (value === 'automated_run') return 'Automated run';
	if (value === 'release_validation') return 'Release validation';
	return 'Manual/operator-reported';
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

function benchmarkValidity(row: ModelGatewayBenchmark) {
	if (objectiveCount(row) === 0 && operatorReportedCount(row) > 0) {
		return <Badge tone="warn">Manual reports are not objective proof</Badge>;
	}
	if (row.insufficientData) return <Badge tone="warn">insufficient objective data</Badge>;
	return <Badge tone="ok">objective benchmark evidence</Badge>;
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
	return (
		<PanelShell title="Benchmarks">
			<div className="form-grid">
				<div className="grid three">
					<div className="field">
						<label htmlFor="benchmark-provider">Benchmark provider</label>
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
						<label htmlFor="benchmark-model">Benchmark model</label>
						<select id="benchmark-model" className="select" value={form.model} onChange={(event) => onChange('model', event.target.value)}>
							{modelOptions.map((model) => <option key={model} value={model}>{model}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="benchmark-runtime">Benchmark runtime</label>
						<select id="benchmark-runtime" className="select" value={form.runtime} onChange={(event) => onChange('runtime', event.target.value)}>
							{['api', 'cli', 'local', 'gateway', 'manual'].map((runtime) => <option key={runtime} value={runtime}>{runtime}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="benchmark-role">Benchmark role</label>
						<select id="benchmark-role" className="select" value={form.role} onChange={(event) => onChange('role', event.target.value)}>
							{EXECUTABLE_AGENT_ROLES.map((role) => <option key={role} value={role}>{role}</option>)}
						</select>
					</div>
					<div className="field">
						<label htmlFor="benchmark-cost">Benchmark cost USD</label>
						<input id="benchmark-cost" className="input" type="number" min="0" step="0.01" value={form.cost} onChange={(event) => onChange('cost', event.target.value)} />
					</div>
					<div className="field">
						<label htmlFor="benchmark-latency">Benchmark latency ms</label>
						<input id="benchmark-latency" className="input" type="number" min="0" step="1" value={form.latency} onChange={(event) => onChange('latency', event.target.value)} />
					</div>
				</div>
				<div className="inline">
					<Badge tone="warn">{provenanceLabel(form.provenance)}</Badge>
					<span className="muted">Manual reports are not objective proof.</span>
					<label className="checkbox-row" htmlFor="benchmark-success"><input id="benchmark-success" type="checkbox" checked={form.success} onChange={(event) => onChange('success', event.target.checked)} />Benchmark success</label>
					<label className="checkbox-row" htmlFor="benchmark-qa"><input id="benchmark-qa" type="checkbox" checked={form.qaPass} onChange={(event) => onChange('qaPass', event.target.checked)} />Benchmark QA pass</label>
					<label className="checkbox-row" htmlFor="benchmark-rework"><input id="benchmark-rework" type="checkbox" checked={form.rework} onChange={(event) => onChange('rework', event.target.checked)} />Benchmark rework</label>
				</div>
				{error ? <div className="form-error" role="alert">{error}</div> : null}
				<button className="button primary" type="button" disabled={busyAction === 'record-benchmark-outcome'} onClick={() => onSubmit()}>Record benchmark outcome</button>
			</div>
			<DataTable rows={benchmarks} empty={<EmptyState title="insufficient data" body="Benchmarks require repeated task outcomes before success rate, QA pass rate, cost, latency or rework rate can be shown." />} columns={[
				{ key: 'provider', label: 'Provider', render: (row) => text(row.providerId) },
				{ key: 'model', label: 'Model', render: (row) => text(row.model) },
				{ key: 'role', label: 'Role', render: (row) => text(row.role) },
				{ key: 'attempts', label: 'Tasks attempted', render: (row) => text(row.tasksAttempted, '0') },
				{ key: 'evidenceMix', label: 'Evidence mix', render: (row) => `${objectiveCount(row)} objective / ${operatorReportedCount(row)} manual` },
				{ key: 'validity', label: 'Validity', render: (row) => benchmarkValidity(row) },
				{ key: 'success', label: 'Success rate', render: (row) => row.successRate === null || row.successRate === undefined ? 'insufficient data' : `${(Number(row.successRate) * 100).toFixed(2)}%` },
				{ key: 'qa', label: 'QA pass rate', render: (row) => row.qaPassRate === null || row.qaPassRate === undefined ? 'insufficient data' : `${(Number(row.qaPassRate) * 100).toFixed(2)}%` },
				{ key: 'cost', label: 'Avg cost', render: (row) => row.avgCost === null || row.avgCost === undefined ? 'unknown' : money(row.avgCost) },
				{ key: 'latency', label: 'Avg latency', render: (row) => text(row.avgLatencyMs) },
				{ key: 'rework', label: 'Rework rate', render: (row) => row.reworkRate === null || row.reworkRate === undefined ? 'insufficient data' : `${(Number(row.reworkRate) * 100).toFixed(2)}%` },
				{ key: 'last', label: 'Last used', render: (row) => text(row.lastUsedAt) },
			]} />
			<DataTable rows={benchmarkOutcomes} empty={<EmptyState title="No benchmark outcomes" body="Outcome records appear after benchmark or task result ingestion." />} columns={[
				{ key: 'provider', label: 'Provider', render: (row) => text(row.providerId) },
				{ key: 'model', label: 'Model', render: (row) => text(row.model) },
				{ key: 'role', label: 'Role', render: (row) => text(row.role) },
				{
					key: 'provenance',
					label: 'Provenance',
					render: (row) => (
						<div className="stack">
							<Badge tone={provenanceTone(row.provenance)}>{provenanceLabel(row.provenance)}</Badge>
							<span className="mono">{text(row.provenance, 'operator_reported')}</span>
						</div>
					),
				},
				{ key: 'success', label: 'Success', render: (row) => boolLabel(row.success) },
				{ key: 'qa', label: 'QA pass', render: (row) => boolLabel(row.qaPass) },
				{ key: 'rework', label: 'Rework', render: (row) => boolLabel(row.rework) },
				{ key: 'cost', label: 'Cost', render: (row) => row.estimatedCostUsd === null || row.estimatedCostUsd === undefined ? 'unknown' : money(row.estimatedCostUsd) },
				{ key: 'latency', label: 'Latency', render: (row) => text(row.latencyMs) },
				{ key: 'time', label: 'Time', render: (row) => text(row.createdAt) },
			]} />
		</PanelShell>
	);
}
