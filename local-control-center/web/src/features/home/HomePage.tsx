/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import type { ReactNode } from 'react';
import {
	ArrowRight,
	ClipboardCheck,
	FileCheck2,
	FolderGit2,
	FolderOpen,
	PlugZap,
	Plus,
	Workflow as WorkflowIcon,
} from 'lucide-react';

import type { Overview, Project, RuntimeProviders } from '../../api/types';
import { Badge } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { shortId, toneForStatus } from '../../lib/format';

export type Language = 'en' | 'es';
type Tone = 'ok' | 'warn' | 'danger' | 'info';

function byNewest(a: string, b: string): number {
	if (a === b) return 0;
	return a > b ? -1 : 1;
}

function riskTone(level: string): Tone {
	if (level === 'critical' || level === 'high') return 'danger';
	if (level === 'medium') return 'warn';
	return 'info';
}

function HomeCard({
	onClick,
	selected,
	ariaLabel,
	children,
}: {
	onClick: () => void;
	selected?: boolean;
	ariaLabel: string;
	children: ReactNode;
}) {
	return (
		<button
			type="button"
			className="home-card"
			data-selected={selected ? 'true' : undefined}
			onClick={onClick}
			aria-label={ariaLabel}
		>
			{children}
		</button>
	);
}

function CardHead({ icon, label, badge }: { icon: ReactNode; label: string; badge: ReactNode }) {
	return (
		<span className="home-card-head">
			<span className="home-card-kind">
				{icon}
				<span>{label}</span>
			</span>
			{badge}
		</span>
	);
}

function Band({
	title,
	count,
	actionLabel,
	onAction,
	children,
}: {
	title: string;
	count: number;
	actionLabel: string;
	onAction: () => void;
	children: ReactNode;
}) {
	return (
		<section className="home-band" data-motion-item>
			<header className="home-band-head">
				<h2 className="home-band-title">
					{title}
					<span className="home-band-count">{count}</span>
				</h2>
				<button type="button" className="home-band-link" onClick={onAction}>
					{actionLabel}
					<ArrowRight size={14} aria-hidden="true" />
				</button>
			</header>
			<div className="masonry-grid">{children}</div>
		</section>
	);
}

export function HomePage({
	overview,
	runtimeProviders,
	selectedProject,
	language = 'en',
	onSelectProject,
	onCreateProject,
	onOpenFolder,
	onOpenWorkbench,
	onOpenProjects,
	onOpenReview,
	onOpenRuns,
	onOpenEvidence,
	onOpenRuntimes,
}: {
	overview: Overview;
	runtimeProviders: RuntimeProviders | null;
	selectedProject: Project | null;
	language?: Language;
	onSelectProject: (projectId: string) => void;
	onCreateProject: () => void;
	onOpenFolder: () => void;
	onOpenWorkbench: () => void;
	onOpenProjects: () => void;
	onOpenReview: () => void;
	onOpenRuns: () => void;
	onOpenEvidence: () => void;
	onOpenRuntimes: () => void;
}) {
	const { t } = useI18n();

	const projects = overview.projects;
	const activeProjects = projects.filter((project) => project.status === 'active');
	const pendingReviews = overview.actionRequests.filter((request) => request.status === 'pending');
	const blockers = (runtimeProviders?.providers ?? []).filter((provider) => !provider.executable);
	const recentRuns = [...overview.workflows].sort((a, b) => byNewest(a.updatedAt, b.updatedAt)).slice(0, 6);
	const recentEvidence = [...overview.evidencePackages].sort((a, b) => byNewest(a.createdAt, b.createdAt)).slice(0, 6);

	return (
		<div className="home">
			<section className="home-hero" data-motion-item>
				<div className="home-hero-text">
					{/* Brand wordmark — identical in every language, intentionally not translated. */}
					<span className="home-hero-kicker">{'AIDO Studio'}</span>
					<h1 className="home-hero-title">{t('app.home.title', 'Open or continue a project')}</h1>
					<p className="home-hero-summary">
						{t(
							'app.home.summary',
							'Point AIDO at a local folder. It detects the project, prepares the workspace, and takes you straight to work — review the diff and evidence, then approve.',
						)}
					</p>
				</div>
				<div className="home-hero-actions">
					<button type="button" className="button primary home-open" onClick={onOpenFolder}>
						<FolderOpen size={18} aria-hidden="true" />
						{t('app.home.openFolder', 'Open folder')}
					</button>
					<button type="button" className="button" onClick={onCreateProject}>
						<Plus size={16} aria-hidden="true" />
						{t('app.home.createWorkspace', 'Create workspace')}
					</button>
				</div>
			</section>

			{projects.length === 0 ? (
				<section className="home-empty" data-motion-item>
					<span className="home-empty-icon" aria-hidden="true">
						<FolderOpen size={26} />
					</span>
					<h2>{t('app.home.emptyTitle', 'Open a folder to start')}</h2>
					<p>
						{t(
							'app.home.emptyBody',
							'AIDO works on a real local folder. Open one and it detects the project type and prepares the workspace for you.',
						)}
					</p>
					<button type="button" className="button primary home-open" onClick={onOpenFolder}>
						<FolderOpen size={18} aria-hidden="true" />
						{t('app.home.openFolder', 'Open folder')}
					</button>
				</section>
			) : (
				<>
					{activeProjects.length ? (
						<Band
							title={t('app.home.continueTitle', 'Continue')}
							count={activeProjects.length}
							actionLabel={t('app.home.allProjects', 'All projects')}
							onAction={onOpenProjects}
						>
							{activeProjects.map((project) => {
								const reviews = pendingReviews.filter((request) => request.projectId === project.id).length;
								const jobs = overview.jobs.filter(
									(job) => job.projectId === project.id && (job.status === 'running' || job.status === 'queued'),
								).length;
								return (
									<HomeCard
										key={project.id}
										onClick={() => {
											onSelectProject(project.id);
											onOpenWorkbench();
										}}
										selected={selectedProject?.id === project.id}
										ariaLabel={`${t('app.home.openInWorkbench', 'Open in workbench')}: ${project.name}`}
									>
										<CardHead
											icon={<FolderGit2 size={15} aria-hidden="true" />}
											label={t('app.home.kindProject', 'Project')}
											badge={<Badge tone="ok">{project.status}</Badge>}
										/>
										<span className="home-card-title">{project.name}</span>
										<span className="home-card-path mono">{project.path}</span>
										<span className="home-card-meta">
											{selectedProject?.id === project.id ? (
												<Badge tone="info">{t('app.home.selected', 'Selected')}</Badge>
											) : null}
											<span className="home-chip">
												{jobs} {t('app.home.openJobs', 'active jobs')}
											</span>
											{reviews ? (
												<span className="home-chip" data-tone="warn">
													{reviews} {t('app.home.pending', 'pending')}
												</span>
											) : null}
										</span>
										<span className="home-card-cta">
											{t('app.home.openInWorkbench', 'Open in workbench')}
											<ArrowRight size={14} aria-hidden="true" />
										</span>
									</HomeCard>
								);
							})}
						</Band>
					) : null}

					{pendingReviews.length ? (
						<Band
							title={t('app.home.reviewsTitle', 'Pending reviews')}
							count={pendingReviews.length}
							actionLabel={t('app.home.allReviews', 'All reviews')}
							onAction={onOpenReview}
						>
							{pendingReviews.slice(0, 8).map((request) => (
								<HomeCard
									key={request.id}
									onClick={onOpenReview}
									ariaLabel={`${t('app.home.review', 'Review')}: ${request.actionType}`}
								>
									<CardHead
										icon={<ClipboardCheck size={15} aria-hidden="true" />}
										label={t('app.home.kindReview', 'Review')}
										badge={<Badge tone={riskTone(request.riskLevel)}>{request.riskLevel}</Badge>}
									/>
									<span className="home-card-title">{request.actionType}</span>
									{request.command ? <span className="home-card-path mono">{request.command}</span> : null}
									<span className="home-card-meta">
										<span className="home-chip mono">{shortId(request.projectId)}</span>
									</span>
									<span className="home-card-cta">
										{t('app.home.review', 'Review')}
										<ArrowRight size={14} aria-hidden="true" />
									</span>
								</HomeCard>
							))}
						</Band>
					) : null}

					{blockers.length ? (
						<Band
							title={t('app.home.blockersTitle', 'Runtime blockers')}
							count={blockers.length}
							actionLabel={t('ui.static.runtimes.8fb69b37', 'Runtimes')}
							onAction={onOpenRuntimes}
						>
							{blockers.slice(0, 8).map((provider) => (
								<HomeCard
									key={provider.id}
									onClick={onOpenRuntimes}
									ariaLabel={`${t('app.home.configure', 'Configure')}: ${provider.displayName}`}
								>
									<CardHead
										icon={<PlugZap size={15} aria-hidden="true" />}
										label={t('ui.static.runtime.c4740e4c', 'Runtime')}
										badge={
											<Badge tone={provider.configured ? 'warn' : 'danger'}>
												{provider.configured
													? t('app.home.notReady', 'Not ready')
													: t('app.home.notConfigured', 'Not configured')}
											</Badge>
										}
									/>
									<span className="home-card-title">{provider.displayName}</span>
									<span className="home-card-body">{provider.lastError || provider.reason}</span>
									<span className="home-card-cta">
										{t('app.home.configure', 'Configure')}
										<ArrowRight size={14} aria-hidden="true" />
									</span>
								</HomeCard>
							))}
						</Band>
					) : null}

					{recentRuns.length ? (
						<Band
							title={t('app.home.runsTitle', 'Recent runs')}
							count={overview.workflows.length}
							actionLabel={t('app.home.allRuns', 'All runs')}
							onAction={onOpenRuns}
						>
							{recentRuns.map((run) => (
								<HomeCard
									key={run.id}
									onClick={onOpenRuns}
									ariaLabel={`${t('app.home.openRun', 'Open run')}: ${run.title}`}
								>
									<CardHead
										icon={<WorkflowIcon size={15} aria-hidden="true" />}
										label={t('app.home.kindRun', 'Run')}
										badge={<Badge tone={toneForStatus(run.status)}>{run.status}</Badge>}
									/>
									<span className="home-card-title">{run.title}</span>
									<span className="home-card-meta">
										<span className="home-chip mono">{run.kind}</span>
									</span>
									<span className="home-card-cta">
										{t('app.home.openRun', 'Open run')}
										<ArrowRight size={14} aria-hidden="true" />
									</span>
								</HomeCard>
							))}
						</Band>
					) : null}

					{recentEvidence.length ? (
						<Band
							title={t('app.home.evidenceTitle', 'Recent evidence')}
							count={overview.evidencePackages.length}
							actionLabel={t('app.home.allEvidence', 'All evidence')}
							onAction={onOpenEvidence}
						>
							{recentEvidence.map((evidence) => (
								<HomeCard
									key={evidence.id}
									onClick={onOpenEvidence}
									ariaLabel={`${t('app.home.openEvidence', 'Open evidence')}: ${shortId(evidence.id)}`}
								>
									<CardHead
										icon={<FileCheck2 size={15} aria-hidden="true" />}
										label={t('app.home.kindEvidence', 'Evidence')}
										badge={<Badge tone={toneForStatus(evidence.qaVerdict)}>{evidence.qaVerdict}</Badge>}
									/>
									<span className="home-card-title">{evidence.evidenceSource}</span>
									<span className="home-card-meta">
										<span className="home-chip mono">{shortId(evidence.projectId)}</span>
									</span>
									<span className="home-card-cta">
										{t('app.home.openEvidence', 'Open evidence')}
										<ArrowRight size={14} aria-hidden="true" />
									</span>
								</HomeCard>
							))}
						</Band>
					) : null}
				</>
			)}
		</div>
	);
}
