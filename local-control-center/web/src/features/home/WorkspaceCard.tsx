/**
 * Home gallery card for an active project — the primary "continue work" entry. Name and path lead,
 * then the in-progress and pending-review counts that signal where attention is needed, and a
 * footer with the last activity and the open action. Every card keeps the same three bands, so a
 * row of projects lines up whatever each one holds.
 * @author Rodrigo Mason
 */
import { Activity, ArrowRight, ClipboardCheck, FolderGit2 } from 'lucide-react';

import { useI18n } from '../../i18n/I18nProvider';
import { HomeCard } from './HomeCardShell';
import { compactPath, type HomeProject } from './homeModel';
import { useRelativeTime } from './useRelativeTime';

/**
 * Gallery card for an active project. Shows the project name and a tail-first path (full path in
 * the tooltip), in-progress and pending-review counts, the last activity, and opens the workbench
 * on click.
 */
export function WorkspaceCard({
	project,
	inProgress,
	pendingReviews,
	lastActivity,
	selected,
	onOpen,
}: {
	project: HomeProject;
	inProgress: number;
	pendingReviews: number;
	lastActivity: string;
	selected: boolean;
	onOpen: () => void;
}) {
	const { t } = useI18n();
	const openLabel = t('app.home.openInWorkbench', 'Open in workbench');
	const relative = useRelativeTime(lastActivity);

	return (
		<HomeCard
			kind="workspace"
			onClick={onOpen}
			selected={selected}
			ariaLabel={`${openLabel}: ${project.name}`}
		>
			<span className="home-project-top">
				<span className="home-project-glyph" aria-hidden="true">
					<FolderGit2 size={16} />
				</span>
				<span className="home-project-heading">
					<span className="home-card-title home-project-name" title={project.name}>
						{project.name}
					</span>
					<span className="home-project-path mono" title={project.path}>
						{compactPath(project.path)}
					</span>
				</span>
			</span>
			<span className="home-project-stats">
				<span className="home-stat" data-active={inProgress > 0 ? 'true' : undefined}>
					<Activity size={13} aria-hidden="true" />
					{inProgress} {t('app.home.inProgress', 'in progress')}
				</span>
				{pendingReviews ? (
					<span className="home-stat" data-tone="warn">
						<ClipboardCheck size={13} aria-hidden="true" />
						{pendingReviews} {t('app.home.pending', 'pending')}
					</span>
				) : null}
				{/* The current project is tagged in the stats row, so the name keeps the full head width. */}
				{selected ? (
					<span className="home-project-current">{t('app.home.selected', 'Selected')}</span>
				) : null}
			</span>
			<span className="home-card-foot">
				<span className="home-card-time">
					{relative ? `${t('app.home.lastActivity', 'Active')} ${relative}` : null}
				</span>
				<span className="home-card-cta">
					{t('app.home.open', 'Open')}
					<ArrowRight size={14} aria-hidden="true" />
				</span>
			</span>
		</HomeCard>
	);
}
