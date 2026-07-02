/**
 * Autonomy section body: the three action-approval levels (guided / recommended /
 * autonomous) as an accessible radio-card group wired to the real `autonomy.level`
 * setting. Selection commits immediately; the selected card is marked by full
 * border + tint + check flag (never color alone). Mirrors the composer's
 * PERMISSION_OPTIONS semantics so both surfaces speak the same language.
 * @author Rodrigo Mason
 */

import type { LucideIcon } from 'lucide-react';
import { Check, Shield, ShieldAlert, ShieldCheck } from 'lucide-react';
import type { KeyboardEvent } from 'react';
import { useRef, useState } from 'react';

import { useI18n } from '../../i18n/I18nProvider';
import type { ResolvedSetting } from './useSettings';

type AutonomyLevel = 'guided' | 'recommended' | 'autonomous';

const LEVELS: ReadonlyArray<{
	level: AutonomyLevel;
	icon: LucideIcon;
	titleKey: string;
	titleFallback: string;
	descKey: string;
	descFallback: string;
}> = [
	{
		level: 'guided',
		icon: ShieldCheck,
		titleKey: 'app.composer.permissions.guided',
		titleFallback: 'Ask for approval',
		descKey: 'app.settings.autonomy.guidedDesc',
		descFallback: 'AIDO pauses for your approval before any state-changing action.',
	},
	{
		level: 'recommended',
		icon: Shield,
		titleKey: 'app.composer.permissions.recommended',
		titleFallback: 'Approve for me',
		descKey: 'app.settings.autonomy.recommendedDesc',
		descFallback: 'AIDO proceeds on low-risk steps and asks before risky ones.',
	},
	{
		level: 'autonomous',
		icon: ShieldAlert,
		titleKey: 'app.composer.permissions.autonomous',
		titleFallback: 'Full access',
		descKey: 'app.settings.autonomy.autonomousDesc',
		descFallback: 'AIDO runs end-to-end without pausing; every action stays auditable.',
	},
];

export function AutonomyBody({
	setting,
	onSelect,
	onRevert,
}: {
	/** The resolved `autonomy.level` setting for the active scope. */
	setting: ResolvedSetting | undefined;
	onSelect: (level: string) => Promise<void>;
	onRevert: () => Promise<void>;
}) {
	const { t } = useI18n();
	const [pending, setPending] = useState(false);
	const cardRefs = useRef<Record<string, HTMLButtonElement | null>>({});
	// Synchronous in-flight guard: `pending` state lags one render behind, so fast
	// arrow-key repeats could otherwise fire concurrent PUTs for the same setting.
	const inFlightRef = useRef(false);

	const current: AutonomyLevel =
		setting &&
		typeof setting.value === 'string' &&
		['guided', 'recommended', 'autonomous'].includes(setting.value)
			? (setting.value as AutonomyLevel)
			: 'guided';

	const commit = async (level: AutonomyLevel) => {
		if (inFlightRef.current || level === current) return;
		inFlightRef.current = true;
		setPending(true);
		try {
			await onSelect(level);
		} finally {
			inFlightRef.current = false;
			setPending(false);
		}
	};

	const moveTo = (index: number) => {
		const target = LEVELS[(index + LEVELS.length) % LEVELS.length];
		cardRefs.current[target.level]?.focus();
		void commit(target.level);
	};

	const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
		if (pending) return;
		const index = LEVELS.findIndex((entry) => entry.level === current);
		if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
			event.preventDefault();
			moveTo(index + 1);
		} else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
			event.preventDefault();
			moveTo(index - 1);
		} else if (event.key === 'Home') {
			event.preventDefault();
			moveTo(0);
		} else if (event.key === 'End') {
			event.preventDefault();
			moveTo(LEVELS.length - 1);
		}
	};

	const handleRevert = async () => {
		if (inFlightRef.current) return;
		inFlightRef.current = true;
		setPending(true);
		try {
			await onRevert();
		} finally {
			inFlightRef.current = false;
			setPending(false);
		}
	};

	return (
		<div className="stack">
			<p className="settings-section-intro">
				{t(
					'app.settings.autonomy.intro',
					'How much AIDO may do before asking you. Every level keeps the same audit trail.',
				)}
			</p>
			<div
				className="settings-choice-grid"
				role="radiogroup"
				aria-label={t('app.settings.section.autonomy', 'Autonomy')}
				onKeyDown={onKeyDown}
			>
				{LEVELS.map((entry) => {
					const selected = entry.level === current;
					const Icon = entry.icon;
					return (
						<button
							key={entry.level}
							ref={(node) => {
								cardRefs.current[entry.level] = node;
							}}
							type="button"
							role="radio"
							aria-checked={selected}
							tabIndex={selected ? 0 : -1}
							className="settings-choice-card"
							data-selected={selected || undefined}
							disabled={pending}
							onClick={() => void commit(entry.level)}
						>
							<span className="settings-choice-head">
								<Icon aria-hidden="true" size={18} />
								<span className="settings-choice-title">
									{t(entry.titleKey, entry.titleFallback)}
								</span>
								{selected ? (
									/* aria-hidden: the radio already announces its checked state. */
									<span className="settings-choice-flag" aria-hidden="true">
										<Check aria-hidden="true" size={14} />
										{t('app.settings.autonomy.selected', 'Selected')}
									</span>
								) : null}
							</span>
							<span className="settings-choice-desc">{t(entry.descKey, entry.descFallback)}</span>
						</button>
					);
				})}
			</div>
			{setting?.origin === 'general' ? (
				<div className="setting-row-actions">
					<button
						type="button"
						className="button setting-action-revert"
						disabled={pending}
						onClick={() => void handleRevert()}
					>
						{t('app.settings.action.revertToDefault', 'Revert to Default')}
					</button>
				</div>
			) : null}
		</div>
	);
}
