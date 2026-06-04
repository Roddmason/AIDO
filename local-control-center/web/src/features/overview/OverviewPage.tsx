import { AlertTriangle, CheckCircle2, Gauge, Workflow } from 'lucide-react';

import type { Overview, RuntimeProviders } from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, StatusDot, Surface } from '../../components/primitives';
import { countByStatus, toneForStatus } from '../../lib/format';

export function OverviewPage({ overview, runtimeProviders }: { overview: Overview; runtimeProviders: RuntimeProviders | null }) {
	const running = countByStatus(overview.jobs, 'running');
	const queued = countByStatus(overview.jobs, 'queued');
	const pendingApprovals = overview.actionRequests.filter((item) => item.status === 'pending').length;
	const openRiskStatuses = new Set(['open', 'monitoring', 'mitigating']);
	const openRisks = overview.riskRegister.filter((risk) => openRiskStatuses.has(risk.status)).length;
	const executableRuntimes = runtimeProviders?.providers.filter((provider) => provider.executable).length ?? 0;
	const runtimeCount = runtimeProviders?.providers.length ?? 0;

	return (
		<>
			<PageHeader
				kicker="Operational state"
				title="AIDO control plane"
				summary="Live control over workflows, approvals, agent runtime posture, evidence and model gateways. The console reads only FastAPI v1 state."
			/>
			<div className="grid metrics">
				<Surface>
					<div className="inline"><StatusDot tone={queued ? 'warn' : 'ok'} /> Queued jobs</div>
					<div className="metric-value">{queued}</div>
					<div className="metric-label">{running} running</div>
				</Surface>
				<Surface>
					<div className="inline"><StatusDot tone={pendingApprovals ? 'warn' : 'ok'} /> Pending approvals</div>
					<div className="metric-value">{pendingApprovals}</div>
					<div className="metric-label">granular action requests</div>
				</Surface>
				<Surface>
					<div className="inline"><StatusDot tone={executableRuntimes ? 'ok' : 'warn'} /> Runtime providers</div>
					<div className="metric-value">{executableRuntimes}</div>
					<div className="metric-label">{runtimeCount} providers reported</div>
				</Surface>
				<Surface>
					<div className="inline"><StatusDot tone={openRisks ? 'danger' : 'ok'} /> Open risks</div>
					<div className="metric-value">{openRisks}</div>
					<div className="metric-label">governance records</div>
				</Surface>
			</div>
			<div className="grid two">
				<Surface title="Runtime posture">
					<div className="stack">
						<div className="inline"><CheckCircle2 size={18} /> FastAPI v1 only <Badge tone="ok">active</Badge></div>
						<div className="inline"><Workflow size={18} /> Workflows <Badge tone={overview.workflows.length ? 'ok' : 'warn'}>{overview.workflows.length}</Badge></div>
						<div className="inline"><Gauge size={18} /> Model providers <Badge>{overview.modelProviders.length}</Badge></div>
						<div className="inline"><AlertTriangle size={18} /> Policy decisions <Badge>{overview.permissionDecisions.length}</Badge></div>
					</div>
				</Surface>
				<Surface title="Recent events">
					<DataTable
						rows={overview.events.slice(0, 6)}
						empty={<EmptyState title="No events" body="Events appear when workflows, jobs, policy or evidence mutate state." />}
						columns={[
							{ key: 'type', label: 'Type', render: (row) => <span className="mono">{row.type}</span> },
							{ key: 'status', label: 'Severity', render: (row) => <Badge tone={toneForStatus(row.severity)}>{row.severity ?? 'info'}</Badge> },
							{ key: 'created', label: 'Created', render: (row) => <span className="muted">{row.createdAt ?? 'n/a'}</span> },
						]}
					/>
				</Surface>
			</div>
		</>
	);
}
