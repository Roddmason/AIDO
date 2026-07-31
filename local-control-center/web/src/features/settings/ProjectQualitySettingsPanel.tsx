/**
 * Quality section panel (project scope): the quality-gate commands that must pass before
 * work is accepted, edited as a wired string-list row, with a worked-example hint so the
 * field reads as a purpose-built panel rather than a bare list. Dedicated to quality gates
 * so it never reuses the Team roster panel.
 * @author Rodrigo Mason
 */

import { findSetting, WiredRows } from './SettingsChoiceCards';
import type { SectionContext } from './sections';

export function ProjectQualitySettingsPanel({ ctx }: { ctx: SectionContext }) {
	const { t } = ctx;
	const gateCommands = findSetting(ctx.resolved, 'project.quality.gateCommands');
	const devopsChecks = findSetting(ctx.resolved, 'project.quality.devopsChecksEnabled');
	const commandCount = Array.isArray(gateCommands?.value) ? gateCommands.value.length : 0;

	return (
		<div className="stack">
			<p className="settings-section-intro">
				{t('app.settings.intro.quality', 'Commands that gate evidence before work is accepted.')}
			</p>

			<section className="settings-group">
				<h4 className="settings-group-title">
					{t('app.settings.quality.commandsGroup', 'Gate commands')}
				</h4>
				<p className="settings-section-intro">
					{commandCount > 0
						? t(
								'app.settings.quality.activeHint',
								'Every listed command must pass before evidence is accepted for this project.',
							)
						: t(
								'app.settings.quality.emptyHint',
								'Add commands like tests, lint or build that must pass before evidence is accepted.',
							)}
				</p>
				{gateCommands ? <WiredRows ctx={ctx} settings={[gateCommands]} /> : null}
			</section>

			<section className="settings-group">
				<h4 className="settings-group-title">
					{t('app.settings.quality.devopsGroup', 'DevOps checks')}
				</h4>
				<p className="settings-section-intro">
					{t(
						'app.settings.quality.devopsHint',
						'Runs the DevOps agent over the gate commands after security review; advisory, never blocking.',
					)}
				</p>
				{devopsChecks ? <WiredRows ctx={ctx} settings={[devopsChecks]} /> : null}
			</section>
		</div>
	);
}
