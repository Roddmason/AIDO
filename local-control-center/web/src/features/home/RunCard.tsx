/**
 * Home gallery card for a recent run, surfacing its title and live status so the
 * user can jump back into in-flight work. One of the four masonry card kinds.
 */
import { ArrowRight, Workflow as WorkflowIcon } from 'lucide-react';

import { Badge } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { CardHead, HomeCard } from './HomeCardShell';
import type { HomeRun } from './homeModel';

/**
 * Gallery card for a recent run. Shows the run title and its status, and opens
 * the runs view on click.
 */
export function RunCard({ run, onOpen }: { run: HomeRun; onOpen: () => void }) {
	const { t } = useI18n();
	const openLabel = t('app.home.openRun', 'Open run');

	return (
		<HomeCard kind="run" onClick={onOpen} ariaLabel={`${openLabel}: ${run.title}`}>
			<CardHead
				icon={<WorkflowIcon size={15} aria-hidden="true" />}
				label={t('app.home.kindRun', 'Run')}
				badge={<Badge tone={toneForStatus(run.status)}>{run.status}</Badge>}
			/>
			<span className="home-card-title">{run.title}</span>
			<span className="home-card-cta">
				{openLabel}
				<ArrowRight size={14} aria-hidden="true" />
			</span>
		</HomeCard>
	);
}
