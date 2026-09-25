/**
 * Platform-wide runtime transport switches for the Providers & CLI settings section.
 *
 * These flags (`runtime.cli|remote|local|ollama|nvidia.enabled`) are declared in the backend
 * settings registry but had no rendering surface, so an operator could neither see nor change
 * them — the control plane silently vetoed transports with no way to find out why. The runtime
 * policy is a conjunction of the global flag AND the project flag, so a project can still veto a
 * transport locally but can never enable what is switched off here.
 *
 * Turning one off is a platform-wide kill switch and a wired boolean commits on click, so the
 * off-transition is confirmed first; turning one back on stays a single click. The ceiling of one
 * local model call (`runtime.local.maxCallSeconds`) sits under the switches: it is a number that
 * commits with Save like any setting, so it never goes through the kill-switch confirmation.
 * @author Rodrigo Mason
 */

import { useEffect, useRef, useState } from 'react';

import type { JsonValue } from '../../api/generated/openapi';
import { Button } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { type WiredEditing, WiredRows } from './SettingsChoiceCards';
import type { ResolvedSetting, SettingScope } from './useSettings';

/** The section context this panel needs: wired editing plus the settings resolved for the section. */
type RuntimeAccessContext = WiredEditing & { resolved: ResolvedSetting[] };

/**
 * Filtered by explicit key, never by `section`: many descriptors share `section="runtime"` and
 * several of them are project-scoped, so a section filter would render the wrong rows here.
 */
const RUNTIME_FLAG_KEYS = [
	'runtime.cli.enabled',
	'runtime.remote.enabled',
	'runtime.local.enabled',
	'runtime.ollama.enabled',
	'runtime.nvidia.enabled',
];

/** Platform-wide numeric limits of the transports above; plain settings, never kill switches. */
const RUNTIME_LIMIT_KEYS = ['runtime.local.maxCallSeconds'];

type PendingDisable = {
	key: string;
	scope: SettingScope;
	scopeId: string | null;
	value: JsonValue;
};

export function RuntimeAccessPanel({ ctx }: { ctx: RuntimeAccessContext }) {
	const { t } = useI18n();
	const [pending, setPending] = useState<PendingDisable | null>(null);
	const confirmationRef = useRef<HTMLElement>(null);
	const triggerRef = useRef<HTMLElement | null>(null);
	useEffect(() => {
		if (pending) confirmationRef.current?.focus();
	}, [pending]);
	const flags = ctx.resolved.filter((setting: ResolvedSetting) =>
		RUNTIME_FLAG_KEYS.includes(setting.key),
	);
	const limits = ctx.resolved.filter((setting: ResolvedSetting) =>
		RUNTIME_LIMIT_KEYS.includes(setting.key),
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
				triggerRef.current = document.activeElement as HTMLElement;
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
			<WiredRows ctx={ctx} settings={limits} />
			{pending ? (
				<section
					ref={confirmationRef}
					tabIndex={-1}
					aria-label={t(
						'app.settings.runtime.access.confirmTitle',
						'Disable this transport platform-wide?',
					)}
				>
					<div className="stack">
						<h4>
							{t(
								'app.settings.runtime.access.confirmTitle',
								'Disable this transport platform-wide?',
							)}
						</h4>
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
							<Button
								onClick={() => {
									setPending(null);
									triggerRef.current?.focus();
								}}
							>
								{t('app.settings.runtime.access.confirmCancel', 'Keep it enabled')}
							</Button>
						</div>
					</div>
				</section>
			) : null}
		</section>
	);
}
