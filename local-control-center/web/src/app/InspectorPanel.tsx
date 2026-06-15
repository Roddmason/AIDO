/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useMemo } from 'react';

import type { Overview, Project } from '../api/types';
import { Badge, EmptyState } from '../components/primitives';
import { toneForStatus } from '../lib/format';

export function InspectorPanel({
	overview,
	selectedProject,
	language,
	t,
	onClose,
}: {
	overview: Overview;
	selectedProject: Project | null;
	language: string;
	t: (key: string, fallback?: string) => string;
	onClose: () => void;
}) {
	const lang = (en: string, es: string) => (language === 'es' ? es : en);
	const stats = useMemo(() => {
		if (!selectedProject) return null;
		const id = selectedProject.id;
		return {
			approvals: overview.actionRequests.filter((item) => item.projectId === id && item.status === 'pending').length,
			evidence: overview.evidencePackages.filter((item) => item.projectId === id).length,
			workspaces: overview.runtimeWorkspaces.filter((item) => item.projectId === id).length,
			workflows: overview.workflows.filter((item) => item.projectId === id).length,
		};
	}, [overview.actionRequests, overview.evidencePackages, overview.runtimeWorkspaces, overview.workflows, selectedProject]);

	return (
		<aside className="inspector-panel" aria-label={t('app.global.inspector', 'Inspector')}>
			<div className="inspector-header">
				<h2 className="surface-title">{t('app.global.inspector', 'Inspector')}</h2>
				<button className="icon-button" type="button" aria-label={lang('Close inspector', 'Cerrar inspector')} onClick={onClose}>
					×
				</button>
			</div>

			{selectedProject && stats ? (
				<>
					<div className="workspace-root-card">
						<span>{lang('Project', 'Proyecto')}</span>
						<strong>{selectedProject.name}</strong>
						<strong className="mono">{String(selectedProject.path ?? selectedProject.id)}</strong>
						<div className="workspace-root-meta">
							<Badge tone={toneForStatus(String(selectedProject.status ?? 'active'))}>{String(selectedProject.status ?? 'active')}</Badge>
						</div>
					</div>
					<div className="signal-grid">
						<div className="signal-card">
							<span>{lang('Pending approvals', 'Aprobaciones pendientes')}</span>
							<strong>{stats.approvals}</strong>
						</div>
						<div className="signal-card">
							<span>{lang('Evidence', 'Evidencia')}</span>
							<strong>{stats.evidence}</strong>
						</div>
						<div className="signal-card">
							<span>{lang('Workspaces', 'Workspaces')}</span>
							<strong>{stats.workspaces}</strong>
						</div>
						<div className="signal-card">
							<span>{lang('Workflows', 'Flujos')}</span>
							<strong>{stats.workflows}</strong>
						</div>
					</div>
				</>
			) : (
				<EmptyState
					title={lang('No project selected', 'Sin proyecto seleccionado')}
					body={lang('Pick a workspace to see its context here.', 'Elige un workspace para ver su contexto aquí.')}
				/>
			)}
		</aside>
	);
}
