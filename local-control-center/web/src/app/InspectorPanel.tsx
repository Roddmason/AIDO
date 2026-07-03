/**
 * Right-hand context panel summarizing the selected project's key signals.
 *
 * Muestra un EmptyState mientras no hay proyecto seleccionado. En el área de threads el panel
 * se convierte en el {@link ThreadInspector}: la consola de "manager de IAs" con ocho vistas
 * (Goal, Team, Plan, Backlog, Memory, Research, Artifacts, Settings) sobre el hilo activo. En
 * el resto de áreas conserva el resumen del proyecto (aprobaciones, evidencia, workspaces,
 * workflows), y cuando hay un run seleccionado muestra su detalle.
 * @author Rodrigo Mason
 */

import type { Variants } from 'motion/react';
import { m } from 'motion/react';
import type { ReactNode } from 'react';
import { useLayoutEffect, useMemo, useRef } from 'react';

import type { Overview, Project } from '../api/types';
import { Badge, EmptyState } from '../components/primitives';
import { IconButton } from '../components/ui';
import { ThreadInspector } from '../features/shell/ThreadInspector';
import { NEW_SESSION_ID } from '../features/workbench/useWorkbenchData';
import { RunDetail } from '../features/workflows/RunDetail';
import { useI18n } from '../i18n/I18nProvider';
import { toneForStatus } from '../lib/format';
import { EASE_OUT } from '../motion/variants';
import type { Mutate } from './routes';

/**
 * Apertura del Inspector: el panel entra fundiéndose y deslizándose desde su borde derecho
 * (no un corte). En desktop vive en un pane redimensionable del shell; en el layout apilado
 * se monta/desmonta según su estado. Bajo `MotionConfig reducedMotion="user"` el
 * desplazamiento cae a no-op y queda solo el fundido.
 */
const inspectorReveal: Variants = {
	initial: { opacity: 0, x: 16 },
	animate: { opacity: 1, x: 0, transition: { duration: 0.22, ease: EASE_OUT } },
	exit: { opacity: 0, x: 16, transition: { duration: 0.16, ease: 'easeIn' } },
};

/**
 * Shows the selected project's path, status and counts (approvals, evidence,
 * workspaces, workflows) scoped to that project, or an empty state when none is set.
 * In the threads area the body is the {@link ThreadInspector} manager console instead.
 */
export function InspectorPanel({
	overview,
	selectedProject,
	selectedRunId,
	token,
	mutate,
	onClose,
	onClearRun,
	onOpenSettings,
	showLoops = false,
	selectedThreadId = '',
}: {
	overview: Overview;
	selectedProject: Project | null;
	/** When set, the panel shows the run's detail instead of the project summary. */
	selectedRunId: string | null;
	token: string;
	mutate: Mutate;
	onClose: () => void;
	onClearRun: () => void;
	/** Opens a Settings section — the threads inspector's blocker cards route their recovery here. */
	onOpenSettings: (section?: string) => void;
	/** Threads area: replace the project summary with the thread manager console. */
	showLoops?: boolean;
	/** Active thread id from the shell selection ('' or the new-thread sentinel when none). */
	selectedThreadId?: string;
}) {
	const { t } = useI18n();
	const previouslyFocused = useRef<HTMLElement | null>(null);
	useLayoutEffect(() => {
		previouslyFocused.current = (document.activeElement as HTMLElement | null) ?? null;
		return () => {
			const previous = previouslyFocused.current;
			if (previous?.isConnected) previous.focus();
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

	const liveThreadId =
		selectedThreadId && selectedThreadId !== NEW_SESSION_ID ? selectedThreadId : null;

	let body: ReactNode;
	if (selectedRunId) {
		body = <RunDetail overview={overview} runId={selectedRunId} token={token} mutate={mutate} />;
	} else if (showLoops && selectedProject) {
		body = (
			<ThreadInspector
				overview={overview}
				project={selectedProject}
				threadId={liveThreadId}
				mutate={mutate}
				onOpenSettings={onOpenSettings}
			/>
		);
	} else if (selectedProject && stats) {
		body = (
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
		);
	} else {
		body = (
			<EmptyState
				title={t('app.inspector.noProjectSelected', 'No project selected')}
				body={t('app.inspector.pickWorkspaceContext', 'Pick a workspace to see its context here.')}
			/>
		);
	}

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
				<h2 className="surface-title">
					{selectedRunId
						? t('app.runInspector.title', 'Run inspector')
						: t('app.global.inspector', 'Inspector')}
				</h2>
				<IconButton
					aria-label={
						selectedRunId
							? t('app.inspector.closeRun', 'Close run')
							: t('app.inspector.closeInspector', 'Close inspector')
					}
					onClick={selectedRunId ? onClearRun : onClose}
				>
					×
				</IconButton>
			</div>

			{body}
		</m.aside>
	);
}
