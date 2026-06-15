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
import { shortId, toneForStatus } from '../../lib/format';

export type Language = 'en' | 'es';
type Tone = 'ok' | 'warn' | 'danger' | 'info';

type HomeCopy = {
	studio: string;
	title: string;
	summary: string;
	openFolder: string;
	opening: string;
	createWorkspace: string;
	pickerTitle: string;
	openFailed: string;
	emptyTitle: string;
	emptyBody: string;
	continueTitle: string;
	allProjects: string;
	openInWorkbench: string;
	openJobs: string;
	pending: string;
	selected: string;
	kindProject: string;
	reviewsTitle: string;
	allReviews: string;
	review: string;
	kindReview: string;
	blockersTitle: string;
	allRuntimes: string;
	configure: string;
	kindRuntime: string;
	notReady: string;
	notConfigured: string;
	runsTitle: string;
	allRuns: string;
	openRun: string;
	kindRun: string;
	evidenceTitle: string;
	allEvidence: string;
	openEvidence: string;
	kindEvidence: string;
};

const COPY: Record<Language, HomeCopy> = {
	en: {
		studio: 'AIDO Studio',
		title: 'Open or continue a project',
		summary:
			'Point AIDO at a local folder. It detects the project, prepares the workspace, and takes you straight to work — review the diff and evidence, then approve.',
		openFolder: 'Open folder',
		opening: 'Opening…',
		createWorkspace: 'Create workspace',
		pickerTitle: 'Open project folder',
		openFailed: 'Could not open that folder.',
		emptyTitle: 'Open a folder to start',
		emptyBody:
			'AIDO works on a real local folder. Open one and it detects the project type and prepares the workspace for you.',
		continueTitle: 'Continue',
		allProjects: 'All projects',
		openInWorkbench: 'Open in workbench',
		openJobs: 'active jobs',
		pending: 'pending',
		selected: 'Selected',
		kindProject: 'Project',
		reviewsTitle: 'Pending reviews',
		allReviews: 'All reviews',
		review: 'Review',
		kindReview: 'Review',
		blockersTitle: 'Runtime blockers',
		allRuntimes: 'Runtimes',
		configure: 'Configure',
		kindRuntime: 'Runtime',
		notReady: 'Not ready',
		notConfigured: 'Not configured',
		runsTitle: 'Recent runs',
		allRuns: 'All runs',
		openRun: 'Open run',
		kindRun: 'Run',
		evidenceTitle: 'Recent evidence',
		allEvidence: 'All evidence',
		openEvidence: 'Open evidence',
		kindEvidence: 'Evidence',
	},
	es: {
		studio: 'AIDO Studio',
		title: 'Abre o continúa un proyecto',
		summary:
			'Apunta AIDO a una carpeta local. Detecta el proyecto, prepara el workspace y te lleva directo a trabajar — revisa el diff y la evidencia, y aprueba.',
		openFolder: 'Abrir carpeta',
		opening: 'Abriendo…',
		createWorkspace: 'Crear workspace',
		pickerTitle: 'Abrir carpeta del proyecto',
		openFailed: 'No se pudo abrir esa carpeta.',
		emptyTitle: 'Abre una carpeta para empezar',
		emptyBody:
			'AIDO trabaja sobre una carpeta local real. Abre una y detectará el tipo de proyecto y preparará el workspace por ti.',
		continueTitle: 'Continuar',
		allProjects: 'Todos los proyectos',
		openInWorkbench: 'Abrir en workbench',
		openJobs: 'trabajos activos',
		pending: 'pendientes',
		selected: 'Seleccionado',
		kindProject: 'Proyecto',
		reviewsTitle: 'Revisiones pendientes',
		allReviews: 'Todas las revisiones',
		review: 'Revisar',
		kindReview: 'Revisión',
		blockersTitle: 'Bloqueos de runtime',
		allRuntimes: 'Runtimes',
		configure: 'Configurar',
		kindRuntime: 'Runtime',
		notReady: 'No listo',
		notConfigured: 'Sin configurar',
		runsTitle: 'Ejecuciones recientes',
		allRuns: 'Todas las ejecuciones',
		openRun: 'Abrir ejecución',
		kindRun: 'Ejecución',
		evidenceTitle: 'Evidencia reciente',
		allEvidence: 'Toda la evidencia',
		openEvidence: 'Abrir evidencia',
		kindEvidence: 'Evidencia',
	},
};

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
	const copy = COPY[language];

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
					<span className="home-hero-kicker">{copy.studio}</span>
					<h1 className="home-hero-title">{copy.title}</h1>
					<p className="home-hero-summary">{copy.summary}</p>
				</div>
				<div className="home-hero-actions">
					<button type="button" className="button primary home-open" onClick={onOpenFolder}>
						<FolderOpen size={18} aria-hidden="true" />
						{copy.openFolder}
					</button>
					<button type="button" className="button" onClick={onCreateProject}>
						<Plus size={16} aria-hidden="true" />
						{copy.createWorkspace}
					</button>
				</div>
			</section>

			{projects.length === 0 ? (
				<section className="home-empty" data-motion-item>
					<span className="home-empty-icon" aria-hidden="true">
						<FolderOpen size={26} />
					</span>
					<h2>{copy.emptyTitle}</h2>
					<p>{copy.emptyBody}</p>
					<button type="button" className="button primary home-open" onClick={onOpenFolder}>
						<FolderOpen size={18} aria-hidden="true" />
						{copy.openFolder}
					</button>
				</section>
			) : (
				<>
					{activeProjects.length ? (
						<Band
							title={copy.continueTitle}
							count={activeProjects.length}
							actionLabel={copy.allProjects}
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
										ariaLabel={`${copy.openInWorkbench}: ${project.name}`}
									>
										<CardHead
											icon={<FolderGit2 size={15} aria-hidden="true" />}
											label={copy.kindProject}
											badge={<Badge tone="ok">{project.status}</Badge>}
										/>
										<span className="home-card-title">{project.name}</span>
										<span className="home-card-path mono">{project.path}</span>
										<span className="home-card-meta">
											{selectedProject?.id === project.id ? <Badge tone="info">{copy.selected}</Badge> : null}
											<span className="home-chip">
												{jobs} {copy.openJobs}
											</span>
											{reviews ? (
												<span className="home-chip" data-tone="warn">
													{reviews} {copy.pending}
												</span>
											) : null}
										</span>
										<span className="home-card-cta">
											{copy.openInWorkbench}
											<ArrowRight size={14} aria-hidden="true" />
										</span>
									</HomeCard>
								);
							})}
						</Band>
					) : null}

					{pendingReviews.length ? (
						<Band
							title={copy.reviewsTitle}
							count={pendingReviews.length}
							actionLabel={copy.allReviews}
							onAction={onOpenReview}
						>
							{pendingReviews.slice(0, 8).map((request) => (
								<HomeCard
									key={request.id}
									onClick={onOpenReview}
									ariaLabel={`${copy.review}: ${request.actionType}`}
								>
									<CardHead
										icon={<ClipboardCheck size={15} aria-hidden="true" />}
										label={copy.kindReview}
										badge={<Badge tone={riskTone(request.riskLevel)}>{request.riskLevel}</Badge>}
									/>
									<span className="home-card-title">{request.actionType}</span>
									{request.command ? <span className="home-card-path mono">{request.command}</span> : null}
									<span className="home-card-meta">
										<span className="home-chip mono">{shortId(request.projectId)}</span>
									</span>
									<span className="home-card-cta">
										{copy.review}
										<ArrowRight size={14} aria-hidden="true" />
									</span>
								</HomeCard>
							))}
						</Band>
					) : null}

					{blockers.length ? (
						<Band
							title={copy.blockersTitle}
							count={blockers.length}
							actionLabel={copy.allRuntimes}
							onAction={onOpenRuntimes}
						>
							{blockers.slice(0, 8).map((provider) => (
								<HomeCard
									key={provider.id}
									onClick={onOpenRuntimes}
									ariaLabel={`${copy.configure}: ${provider.displayName}`}
								>
									<CardHead
										icon={<PlugZap size={15} aria-hidden="true" />}
										label={copy.kindRuntime}
										badge={
											<Badge tone={provider.configured ? 'warn' : 'danger'}>
												{provider.configured ? copy.notReady : copy.notConfigured}
											</Badge>
										}
									/>
									<span className="home-card-title">{provider.displayName}</span>
									<span className="home-card-body">{provider.lastError || provider.reason}</span>
									<span className="home-card-cta">
										{copy.configure}
										<ArrowRight size={14} aria-hidden="true" />
									</span>
								</HomeCard>
							))}
						</Band>
					) : null}

					{recentRuns.length ? (
						<Band
							title={copy.runsTitle}
							count={overview.workflows.length}
							actionLabel={copy.allRuns}
							onAction={onOpenRuns}
						>
							{recentRuns.map((run) => (
								<HomeCard key={run.id} onClick={onOpenRuns} ariaLabel={`${copy.openRun}: ${run.title}`}>
									<CardHead
										icon={<WorkflowIcon size={15} aria-hidden="true" />}
										label={copy.kindRun}
										badge={<Badge tone={toneForStatus(run.status)}>{run.status}</Badge>}
									/>
									<span className="home-card-title">{run.title}</span>
									<span className="home-card-meta">
										<span className="home-chip mono">{run.kind}</span>
									</span>
									<span className="home-card-cta">
										{copy.openRun}
										<ArrowRight size={14} aria-hidden="true" />
									</span>
								</HomeCard>
							))}
						</Band>
					) : null}

					{recentEvidence.length ? (
						<Band
							title={copy.evidenceTitle}
							count={overview.evidencePackages.length}
							actionLabel={copy.allEvidence}
							onAction={onOpenEvidence}
						>
							{recentEvidence.map((evidence) => (
								<HomeCard
									key={evidence.id}
									onClick={onOpenEvidence}
									ariaLabel={`${copy.openEvidence}: ${shortId(evidence.id)}`}
								>
									<CardHead
										icon={<FileCheck2 size={15} aria-hidden="true" />}
										label={copy.kindEvidence}
										badge={<Badge tone={toneForStatus(evidence.qaVerdict)}>{evidence.qaVerdict}</Badge>}
									/>
									<span className="home-card-title">{evidence.evidenceSource}</span>
									<span className="home-card-meta">
										<span className="home-chip mono">{shortId(evidence.projectId)}</span>
									</span>
									<span className="home-card-cta">
										{copy.openEvidence}
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
