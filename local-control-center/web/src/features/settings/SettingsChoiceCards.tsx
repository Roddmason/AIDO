/**
 * Reusable settings building blocks shared by the section bodies: `findSetting`
 * pulls a single resolved setting by key, `WiredRows` frames a list of SettingRows
 * inside the shared `.settings-list`, and `SettingChoiceCards` renders an enum
 * setting as a selectable radio-card grid.
 *
 * `SettingChoiceCards` mirrors AutonomyBody's accessibility model: arrow keys move
 * focus only and the value commits on activation (click / Enter / Space), because
 * every write is an audited PUT and selection-follows-focus would log transient
 * values. The inheritance chip and revert affordance stay visible so a card choice
 * never hides where the effective value came from. After a commit the focus returns
 * to the activated card (native `disabled` drops it to <body> during the write), and
 * the roving tab stop always lands on a real card even if the stored value drifts
 * outside the current option set.
 * @author Rodrigo Mason
 */

import type { LucideIcon } from 'lucide-react';
import { Check } from 'lucide-react';
import type { KeyboardEvent } from 'react';
import { useEffect, useId, useRef, useState } from 'react';

import type { JsonValue } from '../../api/generated/openapi';
import { useI18n } from '../../i18n/I18nProvider';
import { ChipLabel, SettingRow } from './SettingRow';
import type { ResolvedSetting, SettingScope } from './useSettings';

/**
 * The editing slice of a section context. `SectionContext` is structurally
 * assignable to this, so a body can pass its whole `ctx` where a `WiredEditing`
 * is expected without importing the full section registry type.
 */
export interface WiredEditing {
	scope: SettingScope;
	scopeId: string | null;
	setValue: (
		key: string,
		scope: SettingScope,
		scopeId: string | null,
		value: JsonValue,
	) => Promise<void>;
	clearValue: (key: string, scope: SettingScope, scopeId: string | null) => Promise<void>;
	enumOptionsFor: (key: string) => Array<{ value: string; label: string }> | undefined;
}

/** Returns the resolved setting for `key`, or undefined when the section lacks it. */
export function findSetting(resolved: ResolvedSetting[], key: string): ResolvedSetting | undefined {
	return resolved.find((entry) => entry.key === key);
}

/** Frames a list of wired settings as SettingRows inside the shared `.settings-list`. */
export function WiredRows({ ctx, settings }: { ctx: WiredEditing; settings: ResolvedSetting[] }) {
	if (settings.length === 0) return null;
	return (
		<div className="settings-list">
			{settings.map((setting) => (
				<SettingRow
					key={setting.key}
					setting={setting}
					enumOptions={ctx.enumOptionsFor(setting.key)}
					onSet={(value) => ctx.setValue(setting.key, ctx.scope, ctx.scopeId, value)}
					onRevert={() => ctx.clearValue(setting.key, ctx.scope, ctx.scopeId)}
				/>
			))}
		</div>
	);
}

/** One selectable card for an enum member: icon, translated title and description. */
export type ChoiceCardOption = {
	value: string;
	icon: LucideIcon;
	titleKey: string;
	titleFallback: string;
	descKey: string;
	descFallback: string;
};

export function SettingChoiceCards({
	setting,
	options,
	ariaLabel,
	label,
	onSelect,
	onRevert,
}: {
	/** The resolved enum setting this grid edits, or undefined if the section lacks it. */
	setting: ResolvedSetting | undefined;
	options: ReadonlyArray<ChoiceCardOption>;
	/** Screen-reader name for the radiogroup; used only when no visible `label` is given. */
	ariaLabel?: string;
	/** Optional visible label rendered above the grid and used as its accessible name. */
	label?: string;
	onSelect: (value: string) => Promise<void>;
	onRevert: () => Promise<void>;
}) {
	const { t } = useI18n();
	const labelId = useId();
	const [pending, setPending] = useState(false);
	const cardRefs = useRef<Record<string, HTMLButtonElement | null>>({});
	// Synchronous in-flight guard: `pending` lags one render behind, so fast repeats
	// could otherwise fire concurrent PUTs for the same setting.
	const inFlightRef = useRef(false);
	// The card to refocus once a commit re-enables the grid (native `disabled` drops
	// focus to <body> mid-write); null when no refocus is pending.
	const refocusValueRef = useRef<string | null>(null);

	// Restore keyboard focus to the activated card after the write completes. Runs on
	// every pending -> false transition; the ref gates it to real commits only.
	useEffect(() => {
		if (pending || refocusValueRef.current === null) return;
		const target = cardRefs.current[refocusValueRef.current];
		refocusValueRef.current = null;
		target?.focus();
	}, [pending]);

	if (!setting) return null;

	const scope: SettingScope = setting.editableScopes.includes('project') ? 'project' : 'general';
	const current = typeof setting.value === 'string' ? setting.value : (options[0]?.value ?? '');
	const canRevert = scope === 'project' ? !setting.inherited : setting.origin === 'general';
	// Roving tab stop: the selected card owns it, but if the stored value has drifted
	// outside the current option set the first card owns it — never leave the group
	// with no reachable tab stop.
	const currentIndex = options.findIndex((option) => option.value === current);
	const activeIndex = currentIndex >= 0 ? currentIndex : 0;

	const commit = async (value: string) => {
		if (inFlightRef.current || value === current) return;
		inFlightRef.current = true;
		refocusValueRef.current = value;
		setPending(true);
		try {
			await onSelect(value);
		} finally {
			inFlightRef.current = false;
			setPending(false);
		}
	};

	/** Moves focus only: committing happens on activation, so arrow browsing never
	 *  fires transient audited PUTs. */
	const moveFocusTo = (index: number) => {
		const target = options[(index + options.length) % options.length];
		cardRefs.current[target.value]?.focus();
	};

	const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
		if (pending) return;
		const focused = options.findIndex(
			(entry) => cardRefs.current[entry.value] === document.activeElement,
		);
		const index = focused >= 0 ? focused : activeIndex;
		if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
			event.preventDefault();
			moveFocusTo(index + 1);
		} else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
			event.preventDefault();
			moveFocusTo(index - 1);
		} else if (event.key === 'Home') {
			event.preventDefault();
			moveFocusTo(0);
		} else if (event.key === 'End') {
			event.preventDefault();
			moveFocusTo(options.length - 1);
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
			{label ? (
				<span id={labelId} className="setting-row-label">
					{label}
				</span>
			) : null}
			<div
				className="settings-choice-grid"
				role="radiogroup"
				aria-labelledby={label ? labelId : undefined}
				aria-label={label ? undefined : ariaLabel}
				onKeyDown={onKeyDown}
			>
				{options.map((option, index) => {
					const selected = option.value === current;
					const Icon = option.icon;
					return (
						<button
							key={option.value}
							ref={(node) => {
								cardRefs.current[option.value] = node;
							}}
							type="button"
							role="radio"
							aria-checked={selected}
							tabIndex={index === activeIndex ? 0 : -1}
							className="settings-choice-card"
							data-selected={selected || undefined}
							disabled={pending}
							onClick={() => void commit(option.value)}
						>
							<span className="settings-choice-head">
								<Icon aria-hidden="true" size={18} />
								<span className="settings-choice-title">
									{t(option.titleKey, option.titleFallback)}
								</span>
								{selected ? (
									/* aria-hidden: the radio already announces its checked state. */
									<span className="settings-choice-flag" aria-hidden="true">
										<Check aria-hidden="true" size={14} />
										{t('app.settings.autonomy.selected', 'Selected')}
									</span>
								) : null}
							</span>
							<span className="settings-choice-desc">{t(option.descKey, option.descFallback)}</span>
						</button>
					);
				})}
			</div>
			<div className="setting-row-actions">
				<ChipLabel setting={setting} scope={scope} t={t} />
				{canRevert ? (
					<button
						type="button"
						className="button setting-action-revert"
						disabled={pending}
						onClick={() => void handleRevert()}
					>
						{scope === 'project'
							? t('app.settings.action.revertToGeneral', 'Revert to General')
							: t('app.settings.action.revertToDefault', 'Revert to Default')}
					</button>
				) : null}
			</div>
			{/* Save progress announced to screen readers; visual state is the disabled cards. */}
			<span className="sr-only" role="status">
				{pending ? t('app.settings.status.saving', 'Saving...') : ''}
			</span>
		</div>
	);
}
