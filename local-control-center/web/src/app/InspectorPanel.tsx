/**
 * Right-hand context panel summarizing the selected project's key signals.
 * @author Rodrigo Mason
 */

import type { Variants } from 'motion/react';
import { m } from 'motion/react';
import { useLayoutEffect, useMemo, useRef, useState } from 'react';

import type { Overview, Project } from '../api/types';
import { Badge, EmptyState } from '../components/primitives';
import { IconButton, Tabs } from '../components/ui';
import { LoopList } from '../features/shell/LoopList';
import { RunDetail } from '../features/workflows/RunDetail';
import { useI18n } from '../i18n/I18nProvider';
import { shortId, toneForStatus } from '../lib/format';
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
 */
export function InspectorPanel({
	overview,
	selectedProject,
	selectedRunId,
	token,
	mutate,
	onClose,
	onClearRun,
	showLoops = false,
}: {
	overview: Overview;
	selectedProject: Project | null;
	/** When set, the panel shows the run's detail instead of the project summary. */
	selectedRunId: string | null;
	token: string;
	mutate: Mutate;
	onClose: () => void;
	onClearRun: () => void;
	/** Show the project's product-loop list (phase + status) under the summary — the thread/loop shell's
	 *  Plan surface, since the sidebar no longer carries a Loops tab. */
	showLoops?: boolean;
}) {
	const { t } = useI18n();
	const [inspectorTab, setInspectorTab] = useState<'team' | 'plan' | 'artifacts'>('plan');
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
	const projectArtifacts = selectedProject
		? overview.artifacts.filter((artifact) => artifact.projectId === selectedProject.id)
		: [];

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

			{selectedRunId ? (
				<RunDetail overview={overview} runId={selectedRunId} token={token} mutate={mutate} />
			) : selectedProject && stats ? (
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
			{showLoops && !selectedRunId ? (
				<div className="inspector-plan">
					<Tabs
						label={t('app.inspector.tabsLabel', 'Inspector views')}
						activeTab={inspectorTab}
						onChange={(id) => setInspectorTab(id as 'team' | 'plan' | 'artifacts')}
						idBase="inspector"
						tabs={[
							{ id: 'team', label: t('app.inspector.team', 'Team') },
							{ id: 'plan', label: t('app.inspector.plan', 'Plan') },
							{ id: 'artifacts', label: t('app.inspector.artifacts', 'Artifacts') },
						]}
					>
						{inspectorTab === 'plan' ? (
							<LoopList projectId={selectedProject?.id} filter="" />
						) : inspectorTab === 'team' ? (
							overview.agentProfiles.length ? (
								<ul className="inspector-list">
									{overview.agentProfiles.map((profile) => (
										<li key={profile.id} className="inspector-row">
											<strong>{profile.name}</strong>
											<Badge>{String(profile.role)}</Badge>
										</li>
									))}
								</ul>
							) : (
								<EmptyState
									title={t('app.inspector.teamEmpty', 'No agents configured')}
									body={t(
										'app.inspector.teamEmptyBody',
										'Configure the AI team in Settings → Agents.',
									)}
								/>
							)
						) : projectArtifacts.length ? (
							<ul className="inspector-list">
								{projectArtifacts.slice(0, 20).map((artifact) => (
									<li key={artifact.id} className="inspector-row">
										<span className="mono">{shortId(artifact.id)}</span>
										<Badge>{String(artifact.kind)}</Badge>
									</li>
								))}
							</ul>
						) : (
							<EmptyState
								title={t('app.inspector.artifactsEmpty', 'No artifacts yet')}
								body={t(
									'app.inspector.artifactsEmptyBody',
									'Artifacts produced by runs appear here.',
								)}
							/>
						)}
					</Tabs>
				</div>
			) : null}
		</m.aside>
	);
}
