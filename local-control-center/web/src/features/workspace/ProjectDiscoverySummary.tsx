/**
 * Presentational review-step panel for the new-workspace wizard: renders what project
 * discovery found (manifest markers present/absent and detected runtimes) for the chosen
 * folder. Stateless — all data is supplied by the caller via props.
 */
import { Check, Minus } from 'lucide-react';

import { Badge } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import type { DetectionMarker } from './useProjectDiscovery';

/**
 * Read-only summary of what project discovery found for the chosen folder: one
 * card per manifest marker (present/absent) plus the detected runtimes.
 * Presentational — fed by {@link useProjectDiscovery} and rendered in the
 * new-workspace flow's review step.
 */
export function ProjectDiscoverySummary({
	markers,
	runtimeLabels,
}: {
	markers: DetectionMarker[];
	runtimeLabels: string[];
}) {
	const { t } = useI18n();

	return (
		<>
			<div
				className="detection-grid"
				aria-label={t('app.workspace.detection.aria', 'Detected project markers')}
			>
				{markers.map((marker) => (
					<div
						key={marker.file}
						className="card detection-card"
						data-detected={marker.detected ? 'true' : 'false'}
					>
						<span className="detection-card-file">{marker.file}</span>
						<span className="detection-card-state">
							{marker.detected ? (
								<Badge tone="ok">
									<Check size={13} aria-hidden="true" />{' '}
									{t('app.workspace.detection.found', 'Detected')}
								</Badge>
							) : (
								<span className="muted">
									<Minus size={13} aria-hidden="true" />{' '}
									{t('app.workspace.detection.missing', 'Not found')}
								</span>
							)}
						</span>
					</div>
				))}
			</div>

			<div className="field">
				<span className="field-label">
					{t('app.workspace.detection.runtimes', 'Runtimes detected')}
				</span>
				<div className="detection-runtimes">
					{runtimeLabels.length ? (
						runtimeLabels.map((label, index) => (
							<Badge key={`${label}-${index}`} tone="info">
								{label}
							</Badge>
						))
					) : (
						<span className="muted">
							{t('app.workspace.detection.noRuntimes', 'No runtimes detected yet.')}
						</span>
					)}
				</div>
			</div>
		</>
	);
}
