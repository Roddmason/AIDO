/**
 * Global AI team: per role, the ordered providers a thread uses (first = assigned, rest = fallback).
 * The backend resolves eligibility, the automatic split and the effective order; this panel only
 * edits the `team.role.<role>` string lists of the current scope (general or project) and shows
 * where the effective order comes from. "Automatic" clears the scope's override. When every provider
 * the operator chose is switched off, the role falls back to the automatic split and says so.
 * @author Rodrigo Mason
 */
import { useState } from 'react';

import type { RuntimeTeamRole } from '../../api/types';
import { Button, ErrorState, Skeleton, StatusChip } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { useRuntimeTeam } from '../runtime-team/useRuntimeTeam';
import type { SectionContext } from './sections';

export const AI_TEAM_ROLE_LABEL: Record<string, { key: string; fallback: string }> = {
	product_owner: { key: 'app.aiTeam.role.product_owner', fallback: 'Product Owner' },
	developer: { key: 'app.aiTeam.role.developer', fallback: 'Developer' },
	architect: { key: 'app.aiTeam.role.architect', fallback: 'Architect' },
	security: { key: 'app.aiTeam.role.security', fallback: 'Security' },
	technical_lead: { key: 'app.aiTeam.role.technical_lead', fallback: 'Technical Lead' },
	researcher: { key: 'app.aiTeam.role.researcher', fallback: 'Researcher' },
};

export const AI_TEAM_SOURCE_LABEL: Record<string, { key: string; fallback: string }> = {
	project: { key: 'app.aiTeam.source.project', fallback: 'project' },
	general: { key: 'app.aiTeam.source.general', fallback: 'general' },
	automatic: { key: 'app.aiTeam.source.automatic', fallback: 'automatic' },
	inherited: { key: 'app.aiTeam.source.inherited', fallback: 'inherits the Product Owner' },
	automatic_fallback: {
		key: 'app.aiTeam.source.automatic_fallback',
		fallback: 'automatic (your selection is off)',
	},
};

export function AiTeamPanel({ ctx }: { ctx: SectionContext }) {
	const { t } = useI18n();
	const projectId = ctx.scope === 'project' ? ctx.scopeId : null;
	const { data, failed, reload } = useRuntimeTeam(projectId, true);
	// Per-role busy flags: concurrent writes to two roles must not unlock each other's row.
	const [busyRoles, setBusyRoles] = useState<ReadonlySet<string>>(() => new Set());

	const write = async (role: string, ids: string[] | null) => {
		setBusyRoles((current) => new Set(current).add(role));
		try {
			if (ids === null) await ctx.clearValue(`team.role.${role}`, ctx.scope, ctx.scopeId);
			else await ctx.setValue(`team.role.${role}`, ctx.scope, ctx.scopeId, ids);
			reload();
		} finally {
			setBusyRoles((current) => {
				const next = new Set(current);
				next.delete(role);
				return next;
			});
		}
	};

	if (failed) {
		return (
			<ErrorState
				title={t('app.aiTeam.loadFailed', 'The AI team could not be loaded')}
				body={t('app.aiTeam.loadFailedBody', 'Check the control plane connection and try again.')}
				action={<Button onClick={reload}>{t('app.global.retry', 'Retry')}</Button>}
			/>
		);
	}
	if (!data) return <Skeleton label={t('app.aiTeam.loading', 'Loading the AI team')} />;

	const labelOf = (providerId: string) =>
		data.candidates.find((candidate) => candidate.providerId === providerId)?.label ?? providerId;

	return (
		<section className="ai-team-panel" aria-label={t('app.aiTeam.title', 'AI team')}>
			<p className="field-help">
				{t(
					'app.aiTeam.help',
					'Every thread uses this team unless it sets its own. The first provider of a role is used; the next ones are fallbacks when it is inactive or fails.',
				)}
			</p>
			{data.roles.map((role) => (
				<RoleRow
					key={role.role}
					role={role}
					scope={ctx.scope}
					busy={busyRoles.has(role.role)}
					labelOf={labelOf}
					onWrite={(ids) => void write(role.role, ids)}
				/>
			))}
		</section>
	);
}

function RoleRow({
	role,
	scope,
	busy,
	labelOf,
	onWrite,
}: {
	role: RuntimeTeamRole;
	scope: 'general' | 'project';
	busy: boolean;
	labelOf: (providerId: string) => string;
	onWrite: (ids: string[] | null) => void;
}) {
	const { t } = useI18n();
	const [pick, setPick] = useState('');
	const roleLabel = AI_TEAM_ROLE_LABEL[role.role];
	const name = roleLabel ? t(roleLabel.key, roleLabel.fallback) : role.role;
	const source = AI_TEAM_SOURCE_LABEL[role.source] ?? AI_TEAM_SOURCE_LABEL.automatic;
	const effective = role.effective ?? [];
	const invalid = role.invalid ?? [];
	// The editable list is the operator's own order at this scope; automatic/inherited roles start empty.
	const own = role.source === scope ? (role.configured ?? []) : [];
	const addable = (role.candidates ?? []).filter((id) => !own.includes(id));
	const commit = (ids: string[]) => onWrite(ids.length ? ids : null);
	const move = (index: number, delta: number) => {
		const next = [...own];
		const [item] = next.splice(index, 1);
		next.splice(index + delta, 0, item);
		commit(next);
	};
	// `automatic_fallback`: every provider the operator chose is switched off or not eligible, so the
	// backend assigns the role automatically instead of leaving it empty.
	const fallback = role.source === 'automatic_fallback';
	const automatic = role.source === 'automatic' || role.source === 'inherited';
	// Last own entry that is actually rendered: ids dropped as invalid never show a row to swap with.
	const lastVisibleOwn = own.reduce(
		(last, id, index) => (effective.includes(id) ? index : last),
		-1,
	);
	return (
		<fieldset className="card card--static ai-team-role" aria-label={name} disabled={busy}>
			<legend className="inline">
				<span>{name}</span>
				{role.required ? (
					<StatusChip tone="info">{t('app.aiTeam.required', 'required')}</StatusChip>
				) : null}
				<StatusChip tone={fallback ? 'warn' : automatic ? 'pending' : 'ok'}>
					{t(source.key, source.fallback)}
				</StatusChip>
			</legend>
			<ol className="ai-team-order">
				{effective.map((id, index) => {
					const ownIndex = own.indexOf(id);
					return (
						<li key={id} className="inline" data-assigned={index === 0 ? 'true' : undefined}>
							<span>{labelOf(id)}</span>
							{index === 0 ? (
								<StatusChip tone="ok">{t('app.aiTeam.assigned', 'assigned')}</StatusChip>
							) : null}
							{ownIndex >= 0 ? (
								<span className="inline">
									<Button
										disabled={ownIndex === 0}
										onClick={() => move(ownIndex, -1)}
										aria-label={t('app.aiTeam.moveUp', 'Move up')}
									>
										↑
									</Button>
									<Button
										disabled={ownIndex >= lastVisibleOwn}
										onClick={() => move(ownIndex, 1)}
										aria-label={t('app.aiTeam.moveDown', 'Move down')}
									>
										↓
									</Button>
									<Button
										onClick={() => commit(own.filter((item) => item !== id))}
										aria-label={t('app.aiTeam.remove', 'Remove')}
									>
										×
									</Button>
								</span>
							) : null}
						</li>
					);
				})}
				{effective.length === 0 ? (
					<li className="muted">
						{t('app.aiTeam.noCandidates', 'No active provider can take this role')}
					</li>
				) : null}
			</ol>
			{fallback ? (
				<p className="field-help ai-team-fallback" role="status">
					{t('app.aiTeam.fallbackNote', 'Your selection is switched off; using automatic.')}
				</p>
			) : null}
			{invalid.length ? (
				<p className="field-help" role="status">
					{t('app.aiTeam.invalid', 'Ignored (inactive or not eligible): {ids}').replace(
						'{ids}',
						invalid.join(', '),
					)}
				</p>
			) : null}
			<div className="inline">
				<select
					aria-label={t('app.aiTeam.addProvider', 'Add provider')}
					value={pick}
					onChange={(event) => setPick(event.target.value)}
				>
					<option value="">{t('app.aiTeam.pickProvider', 'Choose a provider…')}</option>
					{addable.map((id) => (
						<option key={id} value={id}>
							{labelOf(id)}
						</option>
					))}
				</select>
				<Button
					disabled={!pick}
					onClick={() => {
						commit([...own, pick]);
						setPick('');
					}}
				>
					{t('app.aiTeam.add', 'Add')}
				</Button>
				<Button disabled={own.length === 0 && !fallback} onClick={() => onWrite(null)}>
					{t('app.aiTeam.automatic', 'Automatic')}
				</Button>
			</div>
		</fieldset>
	);
}
