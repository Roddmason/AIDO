/**
 * Renders a single resolved setting: its value control, an inheritance/source chip,
 * and the affordance to set an override or revert to the inherited value.
 * Chip uses full border-color (no side-stripe). Chip semantics follow the plan:
 * project scope: Inherited·General / Overridden·Project;
 * general scope: Default / Custom.
 *
 * Control per descriptor type: enum → select, number → numeric input, boolean → switch
 * (immediate save in general scope), string_list → textarea (one item per line, shown as
 * chips when read-only), string → text input.
 * @author Rodrigo Mason
 */

import { useCallback, useEffect, useId, useState } from 'react';

import type { JsonValue } from '../../api/generated/openapi';
import { useI18n } from '../../i18n/I18nProvider';
import type { ResolvedSetting } from './useSettings';

export interface SettingRowProps {
	setting: ResolvedSetting;
	/** Called when the user commits a new value for this setting. */
	onSet: (value: JsonValue) => Promise<void>;
	/** Called when the user wants to revert the override. */
	onRevert: () => Promise<void>;
	/** For enum settings whose options come from a live source (e.g. sandbox profiles). */
	enumOptions?: Array<{ value: string; label: string }>;
	/** Optional help text rendered under the label. */
	help?: string;
}

type ScopeCtx = 'general' | 'project';

function resolveScope(setting: ResolvedSetting): ScopeCtx {
	return setting.editableScopes.includes('project') ? 'project' : 'general';
}

function isStringList(setting: ResolvedSetting): boolean {
	return setting.type === 'string_list';
}

function listValue(value: JsonValue | undefined): string[] {
	return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

/** Serializes the current value into the local editing buffer (lists become one-per-line). */
function toLocalValue(setting: ResolvedSetting): string {
	if (isStringList(setting)) return listValue(setting.value).join('\n');
	return String(setting.value ?? '');
}

/** Parses the textarea buffer into a clean string list (split on newlines/commas). */
function parseListValue(raw: string): string[] {
	return raw
		.split(/[\n,]/)
		.map((item) => item.trim())
		.filter((item) => item.length > 0);
}

function ChipLabel({
	setting,
	scope,
	t,
}: {
	setting: ResolvedSetting;
	scope: ScopeCtx;
	t: (key: string, fallback: string) => string;
}) {
	if (scope === 'project') {
		if (setting.inherited) {
			return (
				<span className="setting-chip" data-origin="inherited">
					{t('app.settings.chip.inherited', 'Inherited')}
					{' · '}
					<span className="setting-chip-source">
						{t('app.settings.chip.source.general', 'General')}
					</span>
				</span>
			);
		}
		return (
			<span className="setting-chip" data-origin="project">
				{t('app.settings.chip.overridden', 'Overridden')}
				{' · '}
				<span className="setting-chip-source">
					{t('app.settings.chip.source.project', 'Project')}
				</span>
			</span>
		);
	}
	if (setting.origin === 'default') {
		return (
			<span className="setting-chip" data-origin="default">
				{t('app.settings.chip.default', 'Default')}
			</span>
		);
	}
	return (
		<span className="setting-chip" data-origin="custom">
			{t('app.settings.chip.custom', 'Custom')}
		</span>
	);
}

/** Determines whether the revert affordance should be available for this setting+scope combo. */
function canRevert(setting: ResolvedSetting, scope: ScopeCtx): boolean {
	if (scope === 'project') return !setting.inherited;
	return setting.origin === 'general';
}

/** Read-only rendering of the current value: On/Off for booleans, chips for lists, text otherwise. */
function ValueDisplay({
	setting,
	t,
}: {
	setting: ResolvedSetting;
	t: (key: string, fallback: string) => string;
}) {
	if (setting.type === 'boolean') {
		return (
			<span className="setting-value-display" data-boolean={setting.value ? 'on' : 'off'}>
				{setting.value ? t('app.settings.boolean.on', 'On') : t('app.settings.boolean.off', 'Off')}
			</span>
		);
	}
	if (isStringList(setting)) {
		const items = listValue(setting.value);
		if (items.length === 0) {
			return (
				<span className="setting-value-display muted">
					{t('app.settings.list.empty', 'None configured')}
				</span>
			);
		}
		return (
			<span className="setting-list-chips">
				{items.map((item) => (
					<span key={item} className="setting-list-chip mono">
						{item}
					</span>
				))}
			</span>
		);
	}
	return <span className="setting-value-display">{String(setting.value ?? '')}</span>;
}

export function SettingRow({ setting, onSet, onRevert, enumOptions, help }: SettingRowProps) {
	const { t } = useI18n();
	const controlId = useId();
	const scope = resolveScope(setting);
	const showRevert = canRevert(setting, scope);

	const [revealControl, setRevealControl] = useState(false);
	const [pending, setPending] = useState(false);
	const [localValue, setLocalValue] = useState<string>(() => toLocalValue(setting));
	const [localChecked, setLocalChecked] = useState<boolean>(Boolean(setting.value));

	useEffect(() => {
		setLocalValue(toLocalValue(setting));
		setLocalChecked(Boolean(setting.value));
	}, [setting]);

	const isNumberEmpty = setting.type === 'number' && localValue.trim() === '';
	// Belt-and-suspenders: type="number" inputs sanitize most malformed text to '',
	// so this guard mainly covers paste and programmatic values, not keystrokes.
	const isNumberInvalid =
		setting.type === 'number' && localValue.trim() !== '' && !Number.isFinite(Number(localValue));

	const handleSet = useCallback(async () => {
		if (setting.type === 'number' && localValue.trim() === '') {
			setPending(true);
			try {
				await onRevert();
				setRevealControl(false);
			} finally {
				setPending(false);
			}
			return;
		}
		// Never PUT a non-numeric string into a number-typed setting.
		if (setting.type === 'number' && !Number.isFinite(Number(localValue))) return;
		let coerced: JsonValue = localValue;
		if (setting.type === 'number') {
			const n = Number(localValue);
			if (Number.isFinite(n)) coerced = n;
		}
		if (setting.type === 'boolean') coerced = localChecked;
		if (isStringList(setting)) coerced = parseListValue(localValue);
		setPending(true);
		try {
			await onSet(coerced);
			setRevealControl(false);
		} finally {
			setPending(false);
		}
	}, [localValue, localChecked, setting, onSet, onRevert]);

	const handleRevert = useCallback(async () => {
		setPending(true);
		try {
			await onRevert();
			setRevealControl(false);
		} finally {
			setPending(false);
		}
	}, [onRevert]);

	/** General-scope booleans commit on toggle: a switch with a separate Save is legacy noise. */
	const handleImmediateToggle = useCallback(
		async (checked: boolean) => {
			setLocalChecked(checked);
			setPending(true);
			try {
				await onSet(checked);
			} finally {
				setPending(false);
			}
		},
		[onSet],
	);

	const showControl = scope === 'general' || revealControl;
	const booleanImmediate = setting.type === 'boolean' && scope === 'general';

	return (
		<div className="setting-row">
			<div className="setting-row-meta">
				<label className="setting-row-label" htmlFor={controlId}>
					{t(setting.labelKey ?? setting.key, setting.key)}
				</label>
				<ChipLabel setting={setting} scope={scope} t={t} />
			</div>
			{help ? <p className="setting-row-help">{help}</p> : null}

			{showControl ? (
				<div
					className="setting-control"
					data-type={setting.type}
					data-editing={scope === 'project' ? 'true' : undefined}
				>
					{setting.type === 'boolean' ? (
						<label className="setting-switch" data-disabled={pending ? 'true' : undefined}>
							<input
								id={controlId}
								type="checkbox"
								checked={booleanImmediate ? Boolean(setting.value) : localChecked}
								disabled={pending}
								onChange={(e) =>
									booleanImmediate
										? handleImmediateToggle(e.target.checked)
										: setLocalChecked(e.target.checked)
								}
							/>
							<span className="setting-switch-track" aria-hidden="true">
								<span className="setting-switch-thumb" />
							</span>
							{/* aria-hidden: the checkbox already announces its checked state. */}
							<span className="setting-switch-state" aria-hidden="true">
								{(booleanImmediate ? Boolean(setting.value) : localChecked)
									? t('app.settings.boolean.on', 'On')
									: t('app.settings.boolean.off', 'Off')}
							</span>
						</label>
					) : (enumOptions && enumOptions.length > 0) || setting.type === 'enum' ? (
						<select
							id={controlId}
							className="select"
							value={localValue}
							disabled={pending}
							onChange={(e) => setLocalValue(e.target.value)}
						>
							{(enumOptions ?? (setting.enum ?? []).map((v) => ({ value: v, label: v }))).map(
								(opt) => (
									<option key={opt.value} value={opt.value}>
										{opt.label}
									</option>
								),
							)}
						</select>
					) : isStringList(setting) ? (
						<textarea
							id={controlId}
							className="input setting-list-editor"
							rows={Math.min(6, Math.max(2, parseListValue(localValue).length + 1))}
							value={localValue}
							disabled={pending}
							placeholder={t('app.settings.list.placeholder', 'One entry per line')}
							onChange={(e) => setLocalValue(e.target.value)}
						/>
					) : setting.type === 'number' ? (
						<input
							id={controlId}
							type="number"
							className="input"
							value={localValue}
							disabled={pending}
							aria-invalid={isNumberInvalid || undefined}
							aria-describedby={isNumberInvalid ? `${controlId}-error` : undefined}
							onChange={(e) => setLocalValue(e.target.value)}
						/>
					) : (
						<input
							id={controlId}
							type="text"
							className="input"
							value={localValue}
							disabled={pending}
							onChange={(e) => setLocalValue(e.target.value)}
						/>
					)}
					{!booleanImmediate && (
						<button
							type="button"
							className="button primary setting-action"
							disabled={pending || isNumberInvalid}
							onClick={handleSet}
						>
							{isNumberEmpty
								? t('app.settings.action.revertToDefault', 'Revert to Default')
								: t('app.settings.action.save', 'Save')}
						</button>
					)}
					{scope === 'project' && (
						<button
							type="button"
							className="button setting-action"
							disabled={pending}
							onClick={() => setRevealControl(false)}
						>
							{t('app.settings.action.cancel', 'Cancel')}
						</button>
					)}
				</div>
			) : (
				<div className="setting-control setting-control--readonly">
					<ValueDisplay setting={setting} t={t} />
				</div>
			)}
			{isNumberInvalid && showControl ? (
				<p className="setting-row-error" id={`${controlId}-error`} role="alert">
					{t('app.settings.error.invalidNumber', 'Enter a valid number.')}
				</p>
			) : null}

			<div className="setting-row-actions">
				{scope === 'project' && !revealControl && (
					<button
						type="button"
						className="button setting-action"
						disabled={pending}
						aria-expanded={revealControl}
						onClick={() => {
							setLocalValue(toLocalValue(setting));
							setLocalChecked(Boolean(setting.value));
							setRevealControl(true);
						}}
					>
						{t('app.settings.action.setForProject', 'Set for this project')}
					</button>
				)}
				{showRevert && (
					<button
						type="button"
						className="button setting-action-revert"
						disabled={pending}
						onClick={handleRevert}
					>
						{scope === 'project'
							? t('app.settings.action.revertToGeneral', 'Revert to General')
							: t('app.settings.action.revertToDefault', 'Revert to Default')}
					</button>
				)}
			</div>
			{/* Save/revert progress announced to screen readers; visual state is the disabled control. */}
			<span className="sr-only" role="status">
				{pending ? t('app.settings.status.saving', 'Saving...') : ''}
			</span>
		</div>
	);
}
