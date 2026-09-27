/**
 * Effective AI team of a thread: per role, the provider the next message will use and where the
 * choice comes from (this thread's own team, the project or general setting, or the automatic
 * split). When the thread already ran, the provider sealed in its latest run is shown next to it if
 * it differs, so a team changed after the run never rewrites what actually executed.
 * @author Rodrigo Mason
 */
import { useEffect, useState } from 'react';

import { getThreadSealedRuntimeTeam, type ThreadSealedRuntimeTeam } from '../../api/client';
import { useI18n } from '../../i18n/I18nProvider';
import { readRuntimeTeam } from '../runtime-team/runtimeTeamModel';
import { useRuntimeTeam } from '../runtime-team/useRuntimeTeam';
import { AI_TEAM_ROLE_LABEL, AI_TEAM_SOURCE_LABEL } from '../settings/AiTeamPanel';

function useSealedTeam(threadId: string | null) {
	const [sealed, setSealed] = useState<ThreadSealedRuntimeTeam | null>(null);
	useEffect(() => {
		setSealed(null);
		if (!threadId) return undefined;
		const controller = new AbortController();
		getThreadSealedRuntimeTeam(threadId, controller.signal)
			.then((response) => {
				if (!controller.signal.aborted) setSealed(response);
			})
			.catch(() => {
				// The sealed view is additive: without it the card still shows the current team.
			});
		return () => controller.abort();
	}, [threadId]);
	return sealed;
}

/** Provider a role ran on: its own entry, else (thread team) the thread PO, as `role_allowlist` does. */
function sealedProviderFor(sealed: ThreadSealedRuntimeTeam | null, role: string): string | null {
	if (!sealed || sealed.source === 'none') return null;
	const roles = sealed.roleRuntimes ?? {};
	if (sealed.source === 'thread') return roles[role] ?? roles.product_owner ?? null;
	return roles[role] ?? null;
}

export function EffectiveTeamCard({
	projectId,
	threadId,
	threadMetadata,
}: {
	projectId: string;
	threadId: string | null;
	threadMetadata: unknown;
}) {
	const { t } = useI18n();
	const { data } = useRuntimeTeam(projectId, true);
	const sealed = useSealedTeam(threadId);
	// A thread with its own team overrides every role: a role it does not assign follows the thread's
	// own Product Owner (`role_allowlist`), never the global team.
	const threadTeam = readRuntimeTeam(threadMetadata);
	const threadRoles: Record<string, string | undefined> = threadTeam?.roleRuntimes ?? {};
	if (!data) return null;
	const labelOf = (id: string | null | undefined) =>
		id
			? (data.candidates.find((candidate) => candidate.providerId === id)?.label ?? id)
			: t('app.aiTeam.unassigned', 'unassigned');
	const title = t('app.aiTeam.effectiveTitle', 'Effective AI team');
	return (
		<section className="card card--static effective-team" aria-label={title}>
			<h4 className="card-title">{title}</h4>
			<dl className="provider-facts">
				{data.roles.map((role) => {
					const next = threadTeam
						? (threadRoles[role.role] ?? threadRoles.product_owner ?? null)
						: (role.assigned ?? null);
					const roleLabel = AI_TEAM_ROLE_LABEL[role.role];
					const sourceLabel = AI_TEAM_SOURCE_LABEL[role.source] ?? AI_TEAM_SOURCE_LABEL.automatic;
					const source = threadTeam
						? t('app.aiTeam.source.thread', 'this thread')
						: t(sourceLabel.key, sourceLabel.fallback);
					const ran = sealedProviderFor(sealed, role.role);
					return (
						<div key={role.role} data-role={role.role}>
							<dt>{roleLabel ? t(roleLabel.key, roleLabel.fallback) : role.role}</dt>
							<dd>
								{labelOf(next)} <span className="muted">· {source}</span>
								{ran && ran !== next ? (
									<span className="effective-team-last-run muted">
										{t('app.aiTeam.lastRun', 'last run: {provider}').replace(
											'{provider}',
											labelOf(ran),
										)}
									</span>
								) : null}
							</dd>
						</div>
					);
				})}
			</dl>
		</section>
	);
}
