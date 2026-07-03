/**
 * Research section body (general scope): the internet-access policy as a selectable
 * radio-card grid, then the remaining research preferences (prefer official docs,
 * maximum sources, trusted domains) as wired rows. Replaces the generic wired list
 * so the headline policy — the choice that most changes agent behaviour — reads as a
 * set of explained options instead of an opaque dropdown.
 * @author Rodrigo Mason
 */

import { findSetting, SettingChoiceCards, WiredRows } from './SettingsChoiceCards';
import type { SectionContext } from './sections';
import { INTERNET_POLICY_OPTIONS } from './settingsChoiceOptions';

export function ResearchBody({ ctx }: { ctx: SectionContext }) {
	const { t } = ctx;
	const policy = findSetting(ctx.resolved, 'research.internetPolicy');
	const rest = ctx.resolved.filter((setting) => setting.key !== 'research.internetPolicy');

	return (
		<div className="stack">
			<p className="settings-section-intro">
				{t(
					'app.settings.intro.research',
					'How agents reach the internet and which sources they prefer.',
				)}
			</p>

			<section className="settings-group">
				<h4 className="settings-group-title">
					{t('app.settings.research.internetGroup', 'Internet access')}
				</h4>
				<SettingChoiceCards
					setting={policy}
					options={INTERNET_POLICY_OPTIONS}
					ariaLabel={t('app.settings.research.internetGroup', 'Internet access')}
					onSelect={(value) =>
						ctx.setValue('research.internetPolicy', ctx.scope, ctx.scopeId, value)
					}
					onRevert={() => ctx.clearValue('research.internetPolicy', ctx.scope, ctx.scopeId)}
				/>
			</section>

			{rest.length > 0 ? (
				<section className="settings-group">
					<h4 className="settings-group-title">
						{t('app.settings.research.sourcesGroup', 'Sources')}
					</h4>
					<WiredRows ctx={ctx} settings={rest} />
				</section>
			) : null}
		</div>
	);
}
