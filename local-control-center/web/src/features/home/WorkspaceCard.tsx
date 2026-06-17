/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { ArrowRight, FolderGit2 } from 'lucide-react';

import { Badge } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { CardHead, HomeCard } from './HomeCardShell';
import type { HomeProject } from './homeModel';

/**
 * Gallery card for an active project — the primary "continue work" entry. Shows
 * the project name and path, in-progress and pending-review counts, and opens
 * the workbench on click.
 */
export function WorkspaceCard({
	project,
	inProgress,
	pendingReviews,
	selected,
	onOpen,
}: {
	project: HomeProject;
	inProgress: number;
	pendingReviews: number;
	selected: boolean;
	onOpen: () => void;
}) {
	const { t } = useI18n();
	const openLabel = t('app.home.openInWorkbench', 'Open in workbench');

	return (
		<HomeCard kind="workspace" onClick={onOpen} selected={selected} ariaLabel={`${openLabel}: ${project.name}`}>
			<CardHead
				icon={<FolderGit2 size={15} aria-hidden="true" />}
				label={t('app.home.kindProject', 'Project')}
				badge={<Badge tone="ok">{project.status}</Badge>}
			/>
			<span className="home-card-title">{project.name}</span>
			<span className="home-card-path mono">{project.path}</span>
			<span className="home-card-meta">
				{selected ? <Badge tone="info">{t('app.home.selected', 'Selected')}</Badge> : null}
				<span className="home-chip">
					{inProgress} {t('app.home.inProgress', 'in progress')}
				</span>
				{pendingReviews ? (
					<span className="home-chip" data-tone="warn">
						{pendingReviews} {t('app.home.pending', 'pending')}
					</span>
				) : null}
			</span>
			<span className="home-card-cta">
				{openLabel}
				<ArrowRight size={14} aria-hidden="true" />
			</span>
		</HomeCard>
	);
}
