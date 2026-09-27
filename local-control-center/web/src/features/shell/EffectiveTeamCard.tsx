/**
 * Effective AI team of a thread: per role, the provider that will run it and where the choice
 * comes from (this thread's own team, the project or general setting, or the automatic split).
 * @author Rodrigo Mason
 */
import { useI18n } from '../../i18n/I18nProvider';
import { readRuntimeTeam } from '../runtime-team/runtimeTeamModel';
import { useRuntimeTeam } from '../runtime-team/useRuntimeTeam';
import { AI_TEAM_ROLE_LABEL, AI_TEAM_SOURCE_LABEL } from '../settings/AiTeamPanel';

export function EffectiveTeamCard({
	projectId,
	threadMetadata,
}: {
	projectId: string;
	threadMetadata: unknown;
}) {
	const { t } = useI18n();
	const { data } = useRuntimeTeam(projectId, true);
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
					const own = threadTeam
						? (threadRoles[role.role] ?? threadRoles.product_owner ?? null)
						: undefined;
					const roleLabel = AI_TEAM_ROLE_LABEL[role.role];
					const sourceLabel = AI_TEAM_SOURCE_LABEL[role.source] ?? AI_TEAM_SOURCE_LABEL.automatic;
					const source = threadTeam
						? t('app.aiTeam.source.thread', 'this thread')
						: t(sourceLabel.key, sourceLabel.fallback);
					return (
						<div key={role.role}>
							<dt>{roleLabel ? t(roleLabel.key, roleLabel.fallback) : role.role}</dt>
							<dd>
								{labelOf(threadTeam ? own : role.assigned)}{' '}
								<span className="muted">· {source}</span>
							</dd>
						</div>
					);
				})}
			</dl>
		</section>
	);
}
