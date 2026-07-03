/**
 * Project Internet section body (project scope): the per-project internet policy as a
 * selectable radio-card grid — a card commit writes a project override, and the chip
 * shows whether the effective value is inherited from General or overridden here —
 * followed by the allowlist domains and the prefer-official-docs preference as wired
 * rows. The allowlist is the project-level counterpart to the general trusted domains.
 * @author Rodrigo Mason
 */

import { findSetting, SettingChoiceCards, WiredRows } from './SettingsChoiceCards';
import type { SectionContext } from './sections';
import { INTERNET_POLICY_OPTIONS } from './settingsChoiceOptions';

export function InternetBody({ ctx }: { ctx: SectionContext }) {
	const { t } = ctx;
	const policy = findSetting(ctx.resolved, 'research.internetPolicy');
	const rest = ctx.resolved.filter((setting) => setting.key !== 'research.internetPolicy');

	return (
		<div className="stack">
			<p className="settings-section-intro">
				{t('app.settings.intro.internet', 'Web access policy for this project.')}
			</p>

			<section className="settings-group">
				<h4 className="settings-group-title">
					{t('app.settings.internet.policyGroup', 'Project internet policy')}
				</h4>
				<SettingChoiceCards
					setting={policy}
					options={INTERNET_POLICY_OPTIONS}
					ariaLabel={t('app.settings.internet.policyGroup', 'Project internet policy')}
					onSelect={(value) =>
						ctx.setValue('research.internetPolicy', ctx.scope, ctx.scopeId, value)
					}
					onRevert={() => ctx.clearValue('research.internetPolicy', ctx.scope, ctx.scopeId)}
				/>
			</section>

			{rest.length > 0 ? (
				<section className="settings-group">
					<h4 className="settings-group-title">
						{t('app.settings.internet.allowlistGroup', 'Allowlist domains')}
					</h4>
					<WiredRows ctx={ctx} settings={rest} />
				</section>
			) : null}
		</div>
	);
}
