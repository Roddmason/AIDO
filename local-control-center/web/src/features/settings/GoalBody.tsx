/**
 * Project Goal section body (project scope): the goal statement as a wired row, then
 * the active loop policy — team mode and risk posture — as two selectable radio-card
 * grids. The loop policy is what seeds every run for this project, so each mode and
 * risk level is shown as an explained option rather than a bare dropdown. Card commits
 * write project overrides; the chip shows inherited-vs-overridden per selector.
 * @author Rodrigo Mason
 */

import { findSetting, SettingChoiceCards, WiredRows } from './SettingsChoiceCards';
import type { SectionContext } from './sections';
import { RISK_OPTIONS, TEAM_MODE_OPTIONS } from './settingsChoiceOptions';

export function GoalBody({ ctx }: { ctx: SectionContext }) {
	const { t } = ctx;
	const statement = findSetting(ctx.resolved, 'project.goal.statement');
	const teamMode = findSetting(ctx.resolved, 'project.loop.teamMode');
	const risk = findSetting(ctx.resolved, 'project.loop.risk');

	return (
		<div className="stack">
			<p className="settings-section-intro">
				{t(
					'app.settings.intro.goal',
					'The goal statement and loop defaults that seed new runs for this project.',
				)}
			</p>

			{statement ? (
				<section className="settings-group">
					<h4 className="settings-group-title">
						{t('app.settings.goal.objectiveGroup', 'Objective')}
					</h4>
					<WiredRows ctx={ctx} settings={[statement]} />
				</section>
			) : null}

			<section className="settings-group">
				<h4 className="settings-group-title">
					{t('app.settings.goal.loopGroup', 'Active loop policy')}
				</h4>
				<SettingChoiceCards
					setting={teamMode}
					options={TEAM_MODE_OPTIONS}
					label={t('app.settings.goal.teamMode', 'Team mode')}
					onSelect={(value) => ctx.setValue('project.loop.teamMode', ctx.scope, ctx.scopeId, value)}
					onRevert={() => ctx.clearValue('project.loop.teamMode', ctx.scope, ctx.scopeId)}
				/>
				<SettingChoiceCards
					setting={risk}
					options={RISK_OPTIONS}
					label={t('app.settings.goal.risk', 'Risk posture')}
					onSelect={(value) => ctx.setValue('project.loop.risk', ctx.scope, ctx.scopeId, value)}
					onRevert={() => ctx.clearValue('project.loop.risk', ctx.scope, ctx.scopeId)}
				/>
			</section>
		</div>
	);
}
