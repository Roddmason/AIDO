/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useMemo } from 'react';

import type { Overview, Project } from '../api/types';
import { Badge, EmptyState } from '../components/primitives';
import { useI18n } from '../i18n/I18nProvider';
import { toneForStatus } from '../lib/format';

export function InspectorPanel({
	overview,
	selectedProject,
	onClose,
}: {
	overview: Overview;
	selectedProject: Project | null;
	onClose: () => void;
}) {
	const { t } = useI18n();
	const stats = useMemo(() => {
		if (!selectedProject) return null;
		const id = selectedProject.id;
		return {
			approvals: overview.actionRequests.filter(
				(item) => item.projectId === id && item.status === 'pending',
			).length,
			evidence: overview.evidencePackages.filter((item) => item.projectId === id).length,
			workspaces: overview.runtimeWorkspaces.filter((item) => item.projectId === id).length,
			workflows: overview.workflows.filter((item) => item.projectId === id).length,
		};
	}, [
		overview.actionRequests,
		overview.evidencePackages,
		overview.runtimeWorkspaces,
		overview.workflows,
		selectedProject,
	]);

	return (
		<aside className="inspector-panel" aria-label={t('app.global.inspector', 'Inspector')}>
			<div className="inspector-header">
				<h2 className="surface-title">{t('app.global.inspector', 'Inspector')}</h2>
				<button
					className="icon-button"
					type="button"
					aria-label={t('app.inspector.closeInspector', 'Close inspector')}
					onClick={onClose}
				>
					×
				</button>
			</div>

			{selectedProject && stats ? (
				<>
					<div className="workspace-root-card">
						<span>{t('app.inspector.project', 'Project')}</span>
						<strong>{selectedProject.name}</strong>
						<strong className="mono">{String(selectedProject.path ?? selectedProject.id)}</strong>
						<div className="workspace-root-meta">
							<Badge tone={toneForStatus(String(selectedProject.status ?? 'active'))}>
								{String(selectedProject.status ?? 'active')}
							</Badge>
						</div>
					</div>
					<div className="signal-grid">
						<div className="signal-card">
							<span>{t('app.inspector.pendingApprovals', 'Pending approvals')}</span>
							<strong>{stats.approvals}</strong>
						</div>
						<div className="signal-card">
							<span>{t('app.inspector.evidence', 'Evidence')}</span>
							<strong>{stats.evidence}</strong>
						</div>
						<div className="signal-card">
							<span>{t('app.nav.workspaces', 'Workspaces')}</span>
							<strong>{stats.workspaces}</strong>
						</div>
						<div className="signal-card">
							<span>{t('app.inspector.workflows', 'Workflows')}</span>
							<strong>{stats.workflows}</strong>
						</div>
					</div>
				</>
			) : (
				<EmptyState
					title={t('app.inspector.noProjectSelected', 'No project selected')}
					body={t(
						'app.inspector.pickWorkspaceContext',
						'Pick a workspace to see its context here.',
					)}
				/>
			)}
		</aside>
	);
}
