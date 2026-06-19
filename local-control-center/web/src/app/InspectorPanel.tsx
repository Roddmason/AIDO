/**
 * Right-hand context panel summarizing the selected project's key signals.
 */

import type { Variants } from 'motion/react';
import { m } from 'motion/react';
import { useLayoutEffect, useMemo, useRef } from 'react';

import type { Overview, Project } from '../api/types';
import { Badge, EmptyState } from '../components/primitives';
import { useI18n } from '../i18n/I18nProvider';
import { toneForStatus } from '../lib/format';
import { EASE_OUT } from '../motion/variants';

/**
 * Apertura/cierre del Inspector: el panel entra/sale fundiéndose y deslizándose desde su
 * borde derecho (no un corte). Vive dentro del `AnimatePresence` del AppShell, que conserva
 * el montaje durante la salida; la columna del grid la sigue gobernando `data-inspector`.
 * Bajo `MotionConfig reducedMotion="user"` el desplazamiento cae a no-op y queda el fundido.
 */
const inspectorReveal: Variants = {
	initial: { opacity: 0, x: 16 },
	animate: { opacity: 1, x: 0, transition: { duration: 0.22, ease: EASE_OUT } },
	exit: { opacity: 0, x: 16, transition: { duration: 0.16, ease: 'easeIn' } },
};

/**
 * Shows the selected project's path, status and counts (approvals, evidence,
 * workspaces, workflows) scoped to that project, or an empty state when none is set.
 */
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
	// Restore focus to whatever opened the inspector (the header toggle) when the panel
	// closes. AppShell keeps the panel mounted during its exit animation, so without this
	// the focused close button would unmount under the user and strand keyboard focus.
	const previouslyFocused = useRef<HTMLElement | null>(null);
	useLayoutEffect(() => {
		previouslyFocused.current = (document.activeElement as HTMLElement | null) ?? null;
		return () => {
			const previous = previouslyFocused.current;
			if (previous && previous.isConnected) previous.focus();
		};
	}, []);
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
		<m.aside
			className="inspector-panel"
			aria-label={t('app.global.inspector', 'Inspector')}
			variants={inspectorReveal}
			initial="initial"
			animate="animate"
			exit="exit"
			style={{ boxShadow: 'var(--shadow-panel-edge, 0 0 1.25rem rgb(0 0 0 / 0.18))' }}
		>
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
		</m.aside>
	);
}
