/**
 * Renders a single resolved setting: its value control, an inheritance/source chip,
 * and the affordance to set an override or revert to the inherited value.
 * Chip uses full border-color (no side-stripe). Chip semantics follow the plan:
 * project scope: Inherited·General / Overridden·Project;
 * general scope: Default / Custom.
 */

import { useCallback, useId, useState } from 'react';

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
}

type ScopeCtx = 'general' | 'project';

function resolveScope(setting: ResolvedSetting): ScopeCtx {
	return setting.editableScopes.includes('project') ? 'project' : 'general';
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
	// general scope
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
	// general scope: revert available when origin is 'general' (has a stored override)
	return setting.origin === 'general';
}

export function SettingRow({ setting, onSet, onRevert, enumOptions }: SettingRowProps) {
	const { t } = useI18n();
	const controlId = useId();
	const scope = resolveScope(setting);
	const showRevert = canRevert(setting, scope);

	// For project scope, reveal the control inline only when the user clicks "Set for this project"
	const [revealControl, setRevealControl] = useState(false);
	const [pending, setPending] = useState(false);
	const [localValue, setLocalValue] = useState<string>(String(setting.value ?? ''));

	const handleSet = useCallback(async () => {
		let coerced: JsonValue = localValue;
		if (setting.type === 'number') {
			const n = Number(localValue);
			if (Number.isFinite(n)) coerced = n;
		}
		setPending(true);
		try {
			await onSet(coerced);
			setRevealControl(false);
		} finally {
			setPending(false);
		}
	}, [localValue, setting.type, onSet]);

	const handleRevert = useCallback(async () => {
		setPending(true);
		try {
			await onRevert();
			setRevealControl(false);
		} finally {
			setPending(false);
		}
	}, [onRevert]);

	const showControl = scope === 'general' || revealControl;

	return (
		<div className="setting-row">
			<div className="setting-row-meta">
				<label className="setting-row-label" htmlFor={controlId}>
					{t(setting.key, setting.key)}
				</label>
				<ChipLabel setting={setting} scope={scope} t={t} />
			</div>

			{showControl ? (
				<div className="setting-control">
					{setting.type === 'enum' ? (
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
					) : setting.type === 'number' ? (
						<input
							id={controlId}
							type="number"
							className="input"
							value={localValue}
							disabled={pending}
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
					<button
						type="button"
						className="button primary setting-action"
						disabled={pending}
						onClick={handleSet}
					>
						{t('app.settings.action.save', 'Save')}
					</button>
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
					<span className="setting-value-display">{String(setting.value ?? '')}</span>
				</div>
			)}

			<div className="setting-row-actions">
				{scope === 'project' && !revealControl && (
					<button
						type="button"
						className="button setting-action"
						disabled={pending}
						onClick={() => {
							setLocalValue(String(setting.value ?? ''));
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
		</div>
	);
}
