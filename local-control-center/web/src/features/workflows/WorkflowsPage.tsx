/**
 * Workflows launcher: the workflow catalog plus the durable run ledger. Each entry opens a
 * concrete run in the shell Inspector (deep-linkable as `#workflows?run=<id>`); the run's full
 * detail — timeline, agents, evidence, artifacts, policy — lives there (see RunDetail), not in a
 * page-local drawer or an artificial step graph.
 * @author Rodrigo Mason
 */
import { useMemo } from 'react';

import type { Overview } from '../../api/types';
import {
	StatusChip as Badge,
	DataTable,
	EmptyState,
	PageHeader,
	Surface,
} from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { shortId, toneForStatus } from '../../lib/format';

/**
 * Workflows route page. A thin launcher over the catalog and run ledger: it owns no run state,
 * delegating the run detail to the shell Inspector via `onOpenRun`.
 */
export function WorkflowsPage({
	overview,
	onOpenRun,
}: {
	overview: Overview;
	onOpenRun: (runId: string) => void;
}) {
	const { t } = useI18n();

	const latestRunByWorkflow = useMemo(() => {
		const latest = new Map<string, string>();
		const newestStartedAt = new Map<string, number>();
		for (const run of overview.workflowRuns) {
			const workflowId = String(run.workflowId ?? '');
			if (!workflowId) continue;
			const startedAt = Date.parse(String(run.startedAt ?? '')) || 0;
			if (!latest.has(workflowId) || startedAt >= (newestStartedAt.get(workflowId) ?? 0)) {
				latest.set(workflowId, String(run.id ?? ''));
				newestStartedAt.set(workflowId, startedAt);
			}
		}
		return latest;
	}, [overview.workflowRuns]);

	const workflowTitleById = useMemo(() => {
		const map = new Map<string, string>();
		for (const workflow of overview.workflows) map.set(workflow.id, workflow.title);
		return map;
	}, [overview.workflows]);

	return (
		<>
			<PageHeader
				kicker={t('app.workflows.kicker', 'Durable runs')}
				title={t('app.nav.workflows', 'Workflows')}
				summary={t(
					'ui.static.durable.workflow.runs.with.steps.evidence.and.permission.che.37de4524',
					'Durable workflow runs with steps, evidence and permission checkpoints represented as operational state.',
				)}
			/>
			<div className="grid two">
				<Surface title={t('ui.static.workflow.catalog', 'Workflow catalog')}>
					<DataTable
						rows={overview.workflows}
						empty={
							<EmptyState
								title={t('ui.static.no.workflows.52b4f780', 'No workflows')}
								body={t(
									'ui.static.command.center.can.create.a.workflow.when.a.project.is.selec.7f8ad419',
									'Command Center can create a workflow when a project is selected.',
								)}
							/>
						}
						columns={[
							{
								key: 'title',
								label: t('ui.static.title.768e0c1c', 'Title'),
								render: (row) => row.title,
							},
							{
								key: 'status',
								label: t('ui.static.status.bae7d5be', 'Status'),
								render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge>,
							},
							{
								key: 'inspect',
								label: t('ui.static.inspect.18ca87af', 'Inspect'),
								render: (row) => {
									const runId = latestRunByWorkflow.get(row.id);
									return (
										<button
											className="button"
											type="button"
											aria-label={`${t('app.workflows.ariaInspectWorkflow', 'Inspect workflow')} ${row.title}`}
											disabled={!runId}
											onClick={() => runId && onOpenRun(runId)}
										>
											{t('ui.static.inspect.18ca87af', 'Inspect')}
										</button>
									);
								},
							},
						]}
					/>
				</Surface>
				<Surface title={t('app.workbench.timeline.runsTitle', 'Workflow runs')}>
					<DataTable
						rows={overview.workflowRuns}
						empty={
							<EmptyState
								title={t('app.workbench.timeline.runsEmptyTitle', 'No workflow runs')}
								body={t(
									'ui.static.starting.workflow.creates.run.record',
									'Starting a workflow creates a run record.',
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
								key: 'workflow',
								label: t('app.workbench.timeline.colWorkflow', 'Workflow'),
								render: (row) =>
									workflowTitleById.get(String(row.workflowId ?? '')) ?? (
										<span className="mono">{shortId(String(row.workflowId ?? ''))}</span>
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
							{
								key: 'open',
								label: t('app.workflows.openRun', 'Open'),
								render: (row) => (
									<button
										className="button"
										type="button"
										aria-label={`${t('app.workflows.ariaOpenRun', 'Open run')} ${shortId(String(row.id ?? ''))}`}
										onClick={() => onOpenRun(String(row.id ?? ''))}
									>
										{t('app.workflows.openRun', 'Open')}
									</button>
								),
							},
						]}
					/>
				</Surface>
			</div>
		</>
	);
}
