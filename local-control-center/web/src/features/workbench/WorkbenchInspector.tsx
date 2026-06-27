/**
 * Right rail of the Workbench: surfaces the at-a-glance governance state of the active workspace —
 * runtime health, pending approvals, QA verdict and recorded blockers — with deep-link buttons.
 * @author Rodrigo Mason
 */
import { ClipboardCheck, FileCheck2, SlidersHorizontal } from 'lucide-react';

import type { Overview, RuntimeProviderConfiguration, RuntimeProviders } from '../../api/types';
import { Badge, DataTable, EmptyState, StatusDot, Surface } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { RuntimeSetupInspectorCard } from '../runtime-setup/RuntimeSetupInspectorCard';
import type { Blocker } from './workbenchSelectors';

/** Read-only governance sidebar; the on* callbacks open the deeper Jobs/Evidence/Settings views. */
export function WorkbenchInspector({
	runtimeProviders,
	runtimeProviderConfiguration,
	token,
	pendingApprovals,
	latestEvidence,
	testResults,
	blockers,
	onOpenJobs,
	onOpenEvidence,
	onOpenSettings,
	onOpenRuntimeSetup,
	onRefresh,
}: {
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	token: string;
	pendingApprovals: Overview['actionRequests'];
	latestEvidence: Overview['evidencePackages'][number] | null;
	testResults: Overview['testResultRecords'];
	blockers: Blocker[];
	onOpenJobs: () => void;
	onOpenEvidence: () => void;
	onOpenSettings: () => void;
	onOpenRuntimeSetup: () => void;
	onRefresh: () => Promise<unknown> | void;
}) {
	const { t } = useI18n();
	const passedTests = testResults.filter((result) => result.status === 'passed').length;
	const failedTests = testResults.filter((result) => result.status === 'failed').length;

	return (
		<aside
			className="workbench-side"
			aria-label={t('app.workbench.inspector.region', 'Run inspector')}
		>
			<Surface title={t('app.workbench.inspector.runtime', 'Runtime status')}>
				<RuntimeSetupInspectorCard
					runtimeProviders={runtimeProviders}
					runtimeProviderConfiguration={runtimeProviderConfiguration}
					token={token}
					onRefresh={onRefresh}
					onOpenRuntimeSetup={onOpenRuntimeSetup}
				/>
			</Surface>

			<Surface title={t('app.workbench.inspector.approvals', 'Approvals')}>
				<div className="signal-grid">
					<div className="signal-card">
						<span>{t('app.workbench.inspector.pending', 'Pending')}</span>
						<strong className="tnum">{pendingApprovals.length}</strong>
					</div>
				</div>
				<DataTable
					rows={pendingApprovals.slice(0, 3)}
					empty={
						<EmptyState
							title={t('app.workbench.inspector.noApprovals', 'No pending approvals')}
							body={t(
								'app.workbench.inspector.noApprovalsBody',
								'Risky actions stop here until a human records a reason.',
							)}
						/>
					}
					columns={[
						{
							key: 'action',
							label: t('app.workbench.inspector.colAction', 'Action'),
							render: (row) => <span className="mono">{row.actionType}</span>,
						},
						{
							key: 'risk',
							label: t('app.workbench.inspector.colRisk', 'Risk'),
							render: (row) => <Badge tone={toneForStatus(row.riskLevel)}>{row.riskLevel}</Badge>,
						},
					]}
				/>
				<button className="button" type="button" onClick={onOpenJobs}>
					<ClipboardCheck aria-hidden="true" size={15} />{' '}
					{t('app.workbench.inspector.reviewApprovals', 'Review approvals')}
				</button>
			</Surface>

			<Surface title={t('app.workbench.inspector.qa', 'QA')}>
				<div className="inline">
					<Badge tone={latestEvidence ? toneForStatus(String(latestEvidence.qaVerdict)) : 'warn'}>
						{latestEvidence
							? String(latestEvidence.qaVerdict)
							: t('app.workbench.inspector.noVerdict', 'no_evidence')}
					</Badge>
				</div>
				<div className="signal-grid">
					<div className="signal-card">
						<span>{t('app.workbench.inspector.passed', 'Passed')}</span>
						<strong className="tnum">{passedTests}</strong>
					</div>
					<div className="signal-card">
						<span>{t('app.workbench.inspector.failed', 'Failed')}</span>
						<strong className="tnum">{failedTests}</strong>
					</div>
				</div>
				<button className="button" type="button" onClick={onOpenEvidence}>
					<FileCheck2 aria-hidden="true" size={15} />{' '}
					{t('app.workbench.inspector.inspectEvidence', 'Full evidence audit')}
				</button>
			</Surface>

			<Surface title={t('app.workbench.inspector.blockers', 'Blockers')}>
				{blockers.length ? (
					<div className="stack compact">
						{blockers.map((blocker) => (
							<div className="delivery-step" key={blocker.id}>
								<Badge tone={blocker.tone}>{blocker.label}</Badge>
								<div>
									<strong>{blocker.detail}</strong>
								</div>
							</div>
						))}
					</div>
				) : (
					<div className="inline">
						<StatusDot tone="ok" />
						<EmptyState
							title={t('app.workbench.inspector.noBlockers', 'No blockers')}
							body={t(
								'app.workbench.inspector.noBlockersBody',
								'No runtime, QA or risk blocker is recorded for this workspace.',
							)}
						/>
					</div>
				)}
			</Surface>

			<button className="command-item" type="button" onClick={onOpenSettings}>
				<span>
					<SlidersHorizontal aria-hidden="true" size={15} />{' '}
					{t('app.workbench.inspector.projectSettings', 'Project settings')}
				</span>
				<small>
					{t(
						'app.workbench.inspector.projectSettingsHint',
						'Selection, templates and workspace configuration',
					)}
				</small>
			</button>
		</aside>
	);
}
