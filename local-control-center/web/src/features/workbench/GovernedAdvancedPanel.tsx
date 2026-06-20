/**
 * Governed-patch Advanced panel: the runtime/QA/cost/target controls and the two governance
 * checkboxes for an issue_to_patch run, plus the no-executable-runtime blocker. Purely
 * presentational — all state (the controlled `advanced` values) and the run dispatch live on
 * WorkbenchPage, so toggling intake modes never unmounts and resets the user's choices. Only
 * rendered in governed modes, keeping the simple conversation flow free of governance controls.
 */
import { AlertTriangle, SlidersHorizontal } from 'lucide-react';

import type { Project, RuntimeProviders } from '../../api/types';
import { Disclosure } from '../../components/Disclosure';
import { Badge } from '../../components/primitives';
import { Button, Checkbox, TextField } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import type { ComposerDraft } from './composerDraft';

/** One runtime provider row from the discovery response. */
export type RuntimeRow = RuntimeProviders['providers'][number];

/** Controlled governed-advanced values (the persisted slice of the composer draft). */
export type GovernedAdvanced = ComposerDraft['advanced'];

export const issueQaPresets = [
	{
		id: 'python-tests',
		labelKey: 'ui.static.python.tests.3e8b6a1b',
		label: 'Python tests',
		commands: [['uv', 'run', 'pytest', '-q']],
	},
	{
		id: 'web-tests',
		labelKey: 'ui.static.web.tests.fb1b43aa',
		label: 'Web tests',
		commands: [['corepack', 'pnpm@10.24.0', 'run', 'test:web']],
	},
	{
		id: 'quality',
		labelKey: 'ui.static.quality.suite.01a5288b',
		label: 'Quality suite',
		commands: [['corepack', 'pnpm@10.24.0', 'run', 'quality']],
	},
];

/** Auto-selects a QA preset from the detected project stack so a non-expert never has to
 *  reason about test commands. Overridable inside the Advanced disclosure. */
export function autoQaPresetId(project: Project | null): string {
	const template = String(project?.templateId ?? '').toLowerCase();
	if (
		template.startsWith('python') ||
		template.includes('fastapi') ||
		template.includes('django') ||
		template.includes('flask')
	)
		return 'python-tests';
	if (/(node|react|vue|next|vite|web|typescript|javascript|frontend)/.test(template))
		return 'web-tests';
	return 'python-tests';
}

/**
 * Renders the governance controls for a governed run. Stateless: `advanced` holds the controlled
 * values and `onAdvancedChange` patches them; the runtime/QA derivations are computed on the page
 * and passed down so this panel never owns or resets state across mode switches.
 */
export function GovernedAdvancedPanel({
	project,
	busy,
	advanced,
	onAdvancedChange,
	executableRuntimes,
	selectedRuntime,
	selectedQaPreset,
	hasExecutableRuntime,
	runtimeBlockerReason,
	onConfigureRuntime,
}: {
	project: Project | null;
	busy: boolean;
	advanced: GovernedAdvanced;
	onAdvancedChange: (patch: Partial<GovernedAdvanced>) => void;
	executableRuntimes: RuntimeRow[];
	selectedRuntime: RuntimeRow | null;
	selectedQaPreset: (typeof issueQaPresets)[number];
	hasExecutableRuntime: boolean;
	runtimeBlockerReason: string;
	onConfigureRuntime: () => void;
}) {
	const { t } = useI18n();
	const disabled = !project || busy;

	return (
		<div className="stack">
			{!hasExecutableRuntime ? (
				<div className="card" role="status">
					<div className="inline">
						<AlertTriangle aria-hidden="true" size={16} />
						<strong className="card-title">
							{t('app.workbench.task.blockerTitle', 'No executable runtime')}
						</strong>
						<Badge tone="danger">runtime_unavailable</Badge>
					</div>
					<p className="card-body">{runtimeBlockerReason}</p>
					<p className="field-help">
						{t(
							'app.workbench.task.blockerSolution',
							'AIDO will not run changes until a runtime is executable. Configure one to unblock this workspace.',
						)}
					</p>
					<Button
						variant="primary"
						icon={<SlidersHorizontal aria-hidden="true" size={15} />}
						onClick={onConfigureRuntime}
					>
						{t('app.workbench.task.configureRuntime', 'Configure runtime')}
					</Button>
				</div>
			) : null}

			<div className="stack compact">
				<Checkbox
					label={t('app.workbench.task.runChecks', 'Run project checks')}
					checked={advanced.runChecks}
					disabled={disabled}
					onChange={(event) => onAdvancedChange({ runChecks: event.target.checked })}
				/>
				<Checkbox
					label={t('app.workbench.task.requireReview', 'Require review before applying')}
					checked={advanced.requireReview}
					disabled={disabled}
					onChange={(event) => onAdvancedChange({ requireReview: event.target.checked })}
				/>
				{!advanced.runChecks ? (
					<p className="form-error" role="status">
						{t(
							'app.workbench.task.checksOffWarning',
							'Without checks, AIDO auto-detects them; if none exist the change stops for QA evidence before it can be approved.',
						)}
					</p>
				) : null}
				<p className="field-help">
					{t(
						'app.workbench.task.autoLine',
						'AIDO auto-selects the best runtime; open Advanced to override runtime, checks or cost.',
					)}
				</p>
			</div>

			<Disclosure
				title={t('app.workbench.task.advanced', 'Advanced')}
				summary={t('app.workbench.task.advancedSummary', 'Runtime, checks, cost and path')}
			>
				<div className="field">
					<label htmlFor="task-runtime">
						{t('app.workbench.task.runtime', 'Preferred runtime')}
					</label>
					<select
						id="task-runtime"
						className="select"
						value={advanced.preferredRuntime}
						disabled={!project || !hasExecutableRuntime || busy}
						onChange={(event) => onAdvancedChange({ preferredRuntime: event.target.value })}
					>
						{hasExecutableRuntime ? null : (
							<option value="">
								{t('app.workbench.task.runtimeNone', 'No executable runtime')}
							</option>
						)}
						{executableRuntimes.map((runtime) => (
							<option key={runtime.id} value={runtime.id}>
								{runtime.id} -{' '}
								{runtime.executable
									? t('app.runtime.executable', 'executable')
									: runtime.available
										? t('app.modelGateway.runtime.available', 'available')
										: t('app.taskComposer.unavailable', 'unavailable')}
							</option>
						))}
					</select>
					{selectedRuntime ? (
						<div className="inline">
							<Badge tone={selectedRuntime.detected ? 'ok' : 'warn'}>
								{selectedRuntime.detected
									? t('app.modelGateway.runtime.detected', 'detected')
									: t('app.taskComposer.notDetected', 'not detected')}
							</Badge>
							<Badge tone={selectedRuntime.executable ? 'ok' : 'warn'}>
								{selectedRuntime.executable
									? t('app.runtime.executable', 'executable')
									: t('app.modelGateway.runtime.notExecutable', 'not executable')}
							</Badge>
						</div>
					) : null}
				</div>
				<div className="field">
					<label htmlFor="task-qa">{t('app.workbench.task.qa', 'QA preset')}</label>
					<select
						id="task-qa"
						className="select"
						value={advanced.qaPreset}
						disabled={disabled}
						onChange={(event) => onAdvancedChange({ qaPreset: event.target.value })}
					>
						{issueQaPresets.map((preset) => (
							<option key={preset.id} value={preset.id}>
								{t(preset.labelKey, preset.label)}
							</option>
						))}
					</select>
					<div className="field-help">
						{selectedQaPreset.commands.map((command) => command.join(' ')).join(' | ')}
					</div>
				</div>
				<TextField
					label={t('app.workbench.task.cost', 'Maximum cost USD')}
					className="tnum"
					type="number"
					min="0"
					step="0.01"
					value={advanced.maxCostUsd}
					disabled={disabled}
					onChange={(event) => onAdvancedChange({ maxCostUsd: event.target.value })}
				/>
				<TextField
					label={t('app.workbench.task.target', 'Target path')}
					value={advanced.targetPath}
					disabled={disabled}
					placeholder={t('app.workbench.task.targetHint', 'Optional repository-relative path')}
					onChange={(event) => onAdvancedChange({ targetPath: event.target.value })}
				/>
			</Disclosure>
		</div>
	);
}
