/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { FolderOpen, Plus } from 'lucide-react';

import type { Overview, Project, RuntimeProviders } from '../../api/types';
import { useI18n } from '../../i18n/I18nProvider';
import { buildHomeGallery } from './homeModel';
import { ReviewCard } from './ReviewCard';
import { RunCard } from './RunCard';
import { RuntimeBlockerCard } from './RuntimeBlockerCard';
import { WorkspaceCard } from './WorkspaceCard';

export type Language = 'en' | 'es';

/**
 * Landing gallery. A single responsive masonry wall of cards (active projects,
 * runtime blockers, pending reviews, recent runs) that leads the user to open a
 * folder or continue work. Card-first by design — no tables, no control-plane
 * jargon. Count/ordering logic lives in the pure {@link buildHomeGallery} helper.
 */
export function HomePage({
	overview,
	runtimeProviders,
	selectedProject,
	onSelectProject,
	onCreateProject,
	onOpenFolder,
	onOpenWorkbench,
	onOpenProjects,
	onOpenReview,
	onOpenRuns,
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
	onOpenRuntimes: () => void;
}) {
	const { t } = useI18n();
	const hasProjects = overview.projects.length > 0;
	const gallery = buildHomeGallery({
		projects: overview.projects,
		actionRequests: overview.actionRequests,
		jobs: overview.jobs,
		workflows: overview.workflows,
		runtimeProviders,
	});

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

			{!hasProjects ? (
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
				<section className="home-band" data-motion-item>
					<header className="home-band-head">
						<h2 className="home-band-title">{t('app.home.galleryTitle', 'Pick up where you left off')}</h2>
						<div className="home-quick-links">
							<button type="button" className="home-band-link" onClick={onOpenProjects}>
								{t('app.home.allProjects', 'All projects')}
							</button>
							<button type="button" className="home-band-link" onClick={onOpenRuns}>
								{t('app.home.allRuns', 'All runs')}
							</button>
							<button type="button" className="home-band-link" onClick={onOpenReview}>
								{t('app.home.allReviews', 'All reviews')}
							</button>
							<button type="button" className="home-band-link" onClick={onOpenRuntimes}>
								{t('ui.static.runtimes.8fb69b37', 'Runtimes')}
							</button>
						</div>
					</header>
					<div className="masonry-grid">
						{gallery.map((item) => {
							switch (item.kind) {
								case 'workspace':
									return (
										<WorkspaceCard
											key={item.key}
											project={item.project}
											inProgress={item.inProgress}
											pendingReviews={item.pendingReviews}
											selected={selectedProject?.id === item.project.id}
											onOpen={() => {
												onSelectProject(item.project.id);
												onOpenWorkbench();
											}}
										/>
									);
								case 'blocker':
									return <RuntimeBlockerCard key={item.key} provider={item.provider} onOpen={onOpenRuntimes} />;
								case 'review':
									return <ReviewCard key={item.key} request={item.request} onOpen={onOpenReview} />;
								case 'run':
									return <RunCard key={item.key} run={item.run} onOpen={onOpenRuns} />;
								default:
									return null;
							}
						})}
					</div>
				</section>
			)}
		</div>
	);
}
