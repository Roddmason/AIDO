/**
 * Project Routing section body (project scope): the preferred runtime as a selectable
 * radio-card grid, then the runtime-access toggles and allowed-providers list as wired
 * rows. Dedicated to routing so it never reuses the Team roster body — this surface
 * governs which runtimes and providers a project may use, not who is on the team.
 * @author Rodrigo Mason
 */

import { findSetting, SettingChoiceCards, WiredRows } from './SettingsChoiceCards';
import type { SectionContext } from './sections';
import { RUNTIME_MODE_OPTIONS } from './settingsChoiceOptions';

export function RoutingSettings({ ctx }: { ctx: SectionContext }) {
	const { t } = ctx;
	const defaultMode = findSetting(ctx.resolved, 'project.runtime.defaultMode');
	const rest = ctx.resolved.filter((setting) => setting.key !== 'project.runtime.defaultMode');

	return (
		<div className="stack">
			<p className="settings-section-intro">
				{t('app.settings.intro.routing', 'Which providers and runtimes this project may use.')}
			</p>

			<section className="settings-group">
				<h4 className="settings-group-title">
					{t('app.settings.routing.preferredGroup', 'Preferred runtime')}
				</h4>
				<SettingChoiceCards
					setting={defaultMode}
					options={RUNTIME_MODE_OPTIONS}
					ariaLabel={t('app.settings.routing.preferredGroup', 'Preferred runtime')}
					onSelect={(value) =>
						ctx.setValue('project.runtime.defaultMode', ctx.scope, ctx.scopeId, value)
					}
					onRevert={() => ctx.clearValue('project.runtime.defaultMode', ctx.scope, ctx.scopeId)}
				/>
			</section>

			{rest.length > 0 ? (
				<section className="settings-group">
					<h4 className="settings-group-title">
						{t('app.settings.routing.accessGroup', 'Runtime access')}
					</h4>
					<WiredRows ctx={ctx} settings={rest} />
				</section>
			) : null}
		</div>
	);
}
