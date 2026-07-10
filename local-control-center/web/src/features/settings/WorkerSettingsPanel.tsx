/**
 * Startup-worker panel (general scope). The autostart switch stays in view because it is
 * the only worker decision most operators ever make; the throughput knobs (poll interval,
 * job concurrency) sit behind a disclosure so the section opens on that choice instead of
 * on three numeric fields. Every row is a wired SettingRow, so each keeps its source chip.
 * @author Rodrigo Mason
 */

import { Disclosure } from '../../components/Disclosure';
import { findSetting, WiredRows } from './SettingsChoiceCards';
import type { SectionContext } from './sections';

/** The one worker setting that stays visible; the rest are tuning knobs. */
const AUTOSTART_KEY = 'worker.autostart';

export function WorkerSettingsPanel({ ctx }: { ctx: SectionContext }) {
	const { t } = ctx;
	if (ctx.resolved.length === 0) {
		return (
			<p className="muted">
				{t('app.settings.section.noSettings', 'No settings found for this section.')}
			</p>
		);
	}

	const autostart = findSetting(ctx.resolved, AUTOSTART_KEY);
	const tuning = ctx.resolved.filter((setting) => setting.key !== AUTOSTART_KEY);

	return (
		<div className="stack">
			{autostart ? <WiredRows ctx={ctx} settings={[autostart]} /> : null}
			{tuning.length > 0 ? (
				<Disclosure
					title={t('app.settings.worker.tuningGroup', 'Throughput tuning')}
					summary={t('app.settings.worker.tuningSummary', 'Poll interval and job concurrency')}
					headingLevel={4}
				>
					<WiredRows ctx={ctx} settings={tuning} />
				</Disclosure>
			) : null}
		</div>
	);
}
