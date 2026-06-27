/**
 * Project Assessment page: runs a policy-gated static assessment on the selected project
 * and surfaces the summary (stack, risk/gap counts, quality signals, debt markers) plus a
 * filterable findings table. Owns its own fetch lifecycle; does NOT touch client.ts or the
 * generated OpenAPI layer — consumes the three assessment endpoints via apiRequest directly.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { apiRequest } from '../../api/client';
import type { Overview, Project } from '../../api/types';
import { DataTable, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { Button, ErrorState, Skeleton, StatusChip } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { shortId, toneForStatus } from '../../lib/format';

interface AssessmentSummary {
	stack: string[];
	countsByCategory: Record<string, number>;
	totalEndpoints: number;
	debtMarkers: string[];
	riskCount: number;
	gapCount: number;
	hasTests: boolean;
	hasCoverage: boolean;
	hasSecurityTooling: boolean;
	hasGitHistory: boolean;
}

interface AssessmentRecord {
	id: string;
	projectId: string;
	rootPath: string;
	status: string;
	source: string;
	summary: AssessmentSummary | null;
	findingsCount: number;
	riskCount: number;
	gapCount: number;
	createdAt: string;
	updatedAt: string;
}

interface FindingRecord {
	id: string;
	assessmentId: string;
	projectId: string;
	category: string;
	title: string;
	detail: string;
	severity: string;
	evidence: string | null;
	confidence: string | null;
	metadata: Record<string, unknown> | null;
	createdAt: string;
}

interface RunAssessmentResponse {
	status: string;
	reason: string | null;
	assessment: AssessmentRecord;
	findings: FindingRecord[];
}

interface ListAssessmentsResponse {
	assessments: AssessmentRecord[];
}

interface ListFindingsResponse {
	findings: FindingRecord[];
}

function severityTone(severity: string): 'ok' | 'warn' | 'danger' | 'info' | 'pending' {
	const s = severity.toLowerCase();
	if (s === 'critical' || s === 'high') return 'danger';
	if (s === 'medium') return 'warn';
	if (s === 'low') return 'ok';
	return 'info';
}

function formatDate(iso: string): string {
	try {
		return new Date(iso).toLocaleString();
	} catch {
		return iso;
	}
}

function SummaryGrid({
	summary,
	t,
}: {
	summary: AssessmentSummary;
	t: (k: string, fb: string) => string;
}) {
	const boolChip = (
		value: boolean,
		trueKey: string,
		trueFallback: string,
		falseKey: string,
		falseFallback: string,
	) => (
		<StatusChip tone={value ? 'ok' : 'warn'}>
			{value ? t(trueKey, trueFallback) : t(falseKey, falseFallback)}
		</StatusChip>
	);

	return (
		<div className="grid two">
			<Surface title={t('app.assessment.summaryStack', 'Stack')}>
				{summary.stack.length > 0 ? (
					<ul
						className="tag-list"
						aria-label={t('app.assessment.summaryStackLabel', 'Detected stack technologies')}
					>
						{summary.stack.map((tech) => (
							<li key={tech}>
								<StatusChip tone="info">{tech}</StatusChip>
							</li>
						))}
					</ul>
				) : (
					<span className="metric-label">
						{t('app.assessment.summaryStackEmpty', 'No stack detected')}
					</span>
				)}
			</Surface>

			<Surface title={t('app.assessment.summarySignals', 'Quality signals')}>
				<ul
					className="tag-list"
					aria-label={t('app.assessment.summarySignalsLabel', 'Quality signal indicators')}
				>
					<li>
						{boolChip(
							summary.hasTests,
							'app.assessment.signalHasTests',
							'Tests',
							'app.assessment.signalNoTests',
							'No tests',
						)}
					</li>
					<li>
						{boolChip(
							summary.hasCoverage,
							'app.assessment.signalHasCoverage',
							'Coverage',
							'app.assessment.signalNoCoverage',
							'No coverage',
						)}
					</li>
					<li>
						{boolChip(
							summary.hasSecurityTooling,
							'app.assessment.signalHasSecurity',
							'Security tooling',
							'app.assessment.signalNoSecurity',
							'No security tooling',
						)}
					</li>
					<li>
						{boolChip(
							summary.hasGitHistory,
							'app.assessment.signalHasGit',
							'Git history',
							'app.assessment.signalNoGit',
							'No git history',
						)}
					</li>
				</ul>
			</Surface>

			<Surface title={t('app.assessment.summaryCounts', 'Risk and gap counts')}>
				<div className="metric-value">{summary.riskCount}</div>
				<div className="metric-label">{t('app.assessment.riskCount', 'risks')}</div>
				<div className="metric-value" style={{ marginTop: 'var(--space-3)' }}>
					{summary.gapCount}
				</div>
				<div className="metric-label">{t('app.assessment.gapCount', 'gaps')}</div>
			</Surface>

			{summary.debtMarkers.length > 0 && (
				<Surface title={t('app.assessment.summaryDebt', 'Debt markers')}>
					<ul
						className="tag-list"
						aria-label={t('app.assessment.summaryDebtLabel', 'Technical debt markers')}
					>
						{summary.debtMarkers.map((marker) => (
							<li key={marker}>
								<StatusChip tone="warn">{marker}</StatusChip>
							</li>
						))}
					</ul>
				</Surface>
			)}
		</div>
	);
}

function FindingsTable({
	findings,
	t,
}: {
	findings: FindingRecord[];
	t: (k: string, fb: string) => string;
}) {
	return (
		<DataTable
			caption={t('app.assessment.findingsTableCaption', 'Assessment findings')}
			rows={findings}
			empty={
				<EmptyState
					title={t('app.assessment.findingsEmpty', 'No findings')}
					body={t(
						'app.assessment.findingsEmptyBody',
						'The assessment did not produce any findings for this project.',
					)}
				/>
			}
			columns={[
				{
					key: 'severity',
					label: t('app.assessment.colSeverity', 'Severity'),
					render: (row) => (
						<StatusChip tone={severityTone(row.severity)}>{row.severity}</StatusChip>
					),
				},
				{
					key: 'category',
					label: t('app.assessment.colCategory', 'Category'),
					render: (row) => row.category,
				},
				{
					key: 'title',
					label: t('app.assessment.colTitle', 'Title'),
					render: (row) => row.title,
				},
				{
					key: 'detail',
					label: t('app.assessment.colDetail', 'Detail'),
					render: (row) => <span className="mono">{row.detail}</span>,
				},
				{
					key: 'confidence',
					label: t('app.assessment.colConfidence', 'Confidence'),
					render: (row) => row.confidence ?? '—',
				},
			]}
		/>
	);
}

export interface AssessmentPageProps {
	overview: Overview;
	selectedProject: Project | null;
	token: string;
}

export function AssessmentPage({
	overview: _overview,
	selectedProject,
	token,
}: AssessmentPageProps) {
	const { t } = useI18n();

	const [assessment, setAssessment] = useState<AssessmentRecord | null>(null);
	const [findings, setFindings] = useState<FindingRecord[]>([]);
	const [loadError, setLoadError] = useState('');
	const [runError, setRunError] = useState('');
	const [loading, setLoading] = useState(false);
	const [running, setRunning] = useState(false);
	const abortRef = useRef<AbortController | null>(null);

	const projectId = selectedProject?.id ?? null;

	const loadLatest = useCallback(
		async (signal?: AbortSignal) => {
			if (!projectId) return;
			setLoading(true);
			setLoadError('');
			try {
				const [assessmentsRes, findingsRes] = await Promise.all([
					apiRequest<ListAssessmentsResponse>(
						`/api/v1/projects/${encodeURIComponent(projectId)}/assessments`,
						{ signal },
					),
					apiRequest<ListFindingsResponse>(
						`/api/v1/projects/${encodeURIComponent(projectId)}/findings`,
						{ signal },
					),
				]);
				const latest = assessmentsRes.assessments[0] ?? null;
				setAssessment(latest);
				setFindings(findingsRes.findings);
			} catch (err) {
				if ((err as Error).name !== 'AbortError') {
					setLoadError(
						err instanceof Error
							? err.message
							: t('app.assessment.loadFailed', 'Failed to load assessment data.'),
					);
				}
			} finally {
				setLoading(false);
			}
		},
		[projectId, t],
	);

	const runAssessment = useCallback(async () => {
		if (!projectId) return;
		setRunning(true);
		setRunError('');
		try {
			const result = await apiRequest<RunAssessmentResponse>(
				`/api/v1/projects/${encodeURIComponent(projectId)}/assessment`,
				{ method: 'POST', token },
			);
			setAssessment(result.assessment);
			setFindings(result.findings);
		} catch (err) {
			setRunError(
				err instanceof Error
					? err.message
					: t('app.assessment.runFailed', 'Assessment run failed.'),
			);
		} finally {
			setRunning(false);
		}
	}, [projectId, token, t]);

	useEffect(() => {
		const controller = new AbortController();
		abortRef.current?.abort();
		abortRef.current = controller;
		void loadLatest(controller.signal);
		return () => {
			controller.abort();
		};
	}, [loadLatest]);

	const noProject = !projectId;

	return (
		<>
			<PageHeader
				kicker={t('app.assessment.kicker', 'Project health')}
				title={t('app.assessment.title', 'Assessment')}
				summary={t(
					'app.assessment.headerSummary',
					'Run a static assessment on the selected project to surface stack, risks, gaps and technical debt.',
				)}
			/>

			{/* Run action bar */}
			<Surface flat>
				<div className="toolbar">
					<Button
						variant="primary"
						loading={running}
						disabled={noProject || loading}
						onClick={runAssessment}
						aria-label={t('app.assessment.runAriaLabel', 'Run assessment for selected project')}
					>
						{t('app.assessment.runButton', 'Run assessment')}
					</Button>

					{noProject && (
						<span className="metric-label">
							{t('app.assessment.noProjectHint', 'Select a project to run an assessment.')}
						</span>
					)}

					{assessment && !running && (
						<span className="metric-label">
							{t('app.assessment.lastRunLabel', 'Last run:')} {formatDate(assessment.updatedAt)}
						</span>
					)}
				</div>
			</Surface>

			{/* Run error */}
			{runError && (
				<ErrorState
					title={t('app.assessment.runErrorTitle', 'Assessment failed')}
					body={runError}
					action={
						<Button onClick={runAssessment} disabled={running}>
							{t('app.assessment.retryButton', 'Retry')}
						</Button>
					}
				/>
			)}

			{/* Load error */}
			{loadError && (
				<ErrorState
					title={t('app.assessment.loadErrorTitle', 'Could not load assessment')}
					body={loadError}
					action={
						<Button onClick={() => void loadLatest()}>
							{t('app.assessment.retryButton', 'Retry')}
						</Button>
					}
				/>
			)}

			{/* Loading skeleton */}
			{loading && !assessment && (
				<>
					<Skeleton
						className="h-24"
						label={t('app.assessment.loadingLabel', 'Loading assessment data')}
					/>
					<Skeleton className="h-12" />
					<Skeleton className="h-32" />
				</>
			)}

			{/* Empty state: no project selected */}
			{!loading && noProject && !loadError && (
				<Surface flat>
					<EmptyState
						title={t('app.assessment.emptyNoProject', 'No project selected')}
						body={t(
							'app.assessment.emptyNoProjectBody',
							'Choose a project from the Projects panel to run an assessment.',
						)}
					/>
				</Surface>
			)}

			{/* Empty state: project selected but no assessments yet */}
			{!loading && !noProject && !assessment && !loadError && (
				<Surface flat>
					<EmptyState
						title={t('app.assessment.emptyNoAssessment', 'No assessments yet')}
						body={t(
							'app.assessment.emptyNoAssessmentBody',
							'Click "Run assessment" above to generate the first assessment for this project.',
						)}
					/>
				</Surface>
			)}

			{/* Assessment summary */}
			{assessment && !loading && (
				<>
					<Surface title={t('app.assessment.summarySection', 'Latest assessment')}>
						<div className="toolbar">
							<StatusChip tone={toneForStatus(assessment.status)}>{assessment.status}</StatusChip>
							<span className="metric-label">
								{t('app.assessment.idLabel', 'ID:')} {shortId(assessment.id)}
							</span>
							<span className="metric-label">
								{t('app.assessment.rootPathLabel', 'Root:')} {assessment.rootPath}
							</span>
						</div>

						{assessment.summary ? (
							<SummaryGrid summary={assessment.summary} t={t} />
						) : (
							<EmptyState
								title={t('app.assessment.summaryUnavailable', 'Summary unavailable')}
								body={t(
									'app.assessment.summaryUnavailableBody',
									'The assessment completed but did not produce a summary.',
								)}
							/>
						)}
					</Surface>

					<Surface title={t('app.assessment.findingsSection', 'Findings')}>
						<FindingsTable findings={findings} t={t} />
					</Surface>
				</>
			)}
		</>
	);
}
