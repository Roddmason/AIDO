/**
 * Platform-wide runtime transport switches for the Providers & CLI settings section.
 *
 * These four flags (`runtime.cli|remote|ollama|nvidia.enabled`) are declared in the backend
 * settings registry but had no rendering surface, so an operator could neither see nor change
 * them — the control plane silently vetoed transports with no way to find out why. The runtime
 * policy is a conjunction of the global flag AND the project flag, so a project can still veto a
 * transport locally but can never enable what is switched off here.
 *
 * Turning one off is a platform-wide kill switch and a wired boolean commits on click, so the
 * off-transition is confirmed first; turning one back on stays a single click.
 * @author Rodrigo Mason
 */

import { useState } from 'react';

import type { JsonValue } from '../../api/generated/openapi';
import { Button, Dialog } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { type WiredEditing, WiredRows } from './SettingsChoiceCards';
import type { ResolvedSetting, SettingScope } from './useSettings';

/** The section context this panel needs: wired editing plus the settings resolved for the section. */
type RuntimeAccessContext = WiredEditing & { resolved: ResolvedSetting[] };

/**
 * Filtered by explicit key, never by `section`: nine descriptors share `section="runtime"` and
 * five of them are project-scoped, so a section filter would render the wrong rows here.
 */
const RUNTIME_FLAG_KEYS = [
	'runtime.cli.enabled',
	'runtime.remote.enabled',
	'runtime.ollama.enabled',
	'runtime.nvidia.enabled',
];

type PendingDisable = {
	key: string;
	scope: SettingScope;
	scopeId: string | null;
	value: JsonValue;
};

export function RuntimeAccessPanel({ ctx }: { ctx: RuntimeAccessContext }) {
	const { t } = useI18n();
	const [pending, setPending] = useState<PendingDisable | null>(null);
	const flags = ctx.resolved.filter((setting: ResolvedSetting) =>
		RUNTIME_FLAG_KEYS.includes(setting.key),
	);

	if (flags.length === 0) return null;

	/**
	 * Wrapping the context rather than `SettingRow` keeps the confirmation local: `SettingRow` is
	 * shared by every settings section and must stay free of transport-specific behaviour.
	 */
	const guarded: WiredEditing = {
		...ctx,
		setValue: async (key, scope, scopeId, value) => {
			if (RUNTIME_FLAG_KEYS.includes(key) && value === false) {
				setPending({ key, scope, scopeId, value });
				return;
			}
			await ctx.setValue(key, scope, scopeId, value);
		},
	};

	const confirmDisable = async () => {
		if (!pending) return;
		const target = pending;
		setPending(null);
		await ctx.setValue(target.key, target.scope, target.scopeId, target.value);
	};

	return (
		<section className="settings-group">
			<p className="settings-section-intro">
				{t(
					'app.settings.runtime.access.intro',
					'Transports allowed across every project. A project can still veto one, but never enable what is off here.',
				)}
			</p>
			<WiredRows ctx={guarded} settings={flags} />
			<Dialog
				open={pending !== null}
				onClose={() => setPending(null)}
				label={t(
					'app.settings.runtime.access.confirmTitle',
					'Disable this transport platform-wide?',
				)}
			>
				<div className="stack">
					<p>
						{t(
							'app.settings.runtime.access.confirmBody',
							'Every project loses this transport until you turn it back on, including runs already queued.',
						)}
					</p>
					<div className="inline">
						<Button variant="primary" onClick={() => void confirmDisable()}>
							{t('app.settings.runtime.access.confirmAccept', 'Disable transport')}
						</Button>
						<Button onClick={() => setPending(null)}>
							{t('app.settings.runtime.access.confirmCancel', 'Keep it enabled')}
						</Button>
					</div>
				</div>
			</Dialog>
		</section>
	);
}
