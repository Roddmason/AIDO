/**
 * Governed-patch intake form: turns a plain-language change request into an issue_to_patch run,
 * auto-selecting the runtime and QA preset so a non-expert can dispatch a safe, evidence-backed
 * change. Blocks submission when no executable runtime exists and renders the run result inline.
 */
import { AlertTriangle, Rocket, SlidersHorizontal } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { IssueToPatchResponse } from '../../api/client';
import { runIssueToPatch } from '../../api/client';
import type { Project, RuntimeProviders } from '../../api/types';
import { Disclosure } from '../../components/Disclosure';
import { Badge } from '../../components/primitives';
import { Button, Checkbox, TextArea, TextField } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import {
	objectRecord,
	runtimeIsExecutableIssueRuntime,
	runtimeSupportsIssueToPatch,
} from './workbenchSelectors';

type Mutate = <T>(
	operation: (token: string) => Promise<T>,
	options?: { awaitRefresh?: boolean },
) => Promise<T>;
type TaskModeId = 'fix' | 'feature' | 'refactor' | 'tests';

const issueQaPresets = [
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

const taskModes: { id: TaskModeId; labelKey: string; label: string }[] = [
	{ id: 'fix', labelKey: 'app.workbench.task.modeFix', label: 'Fix bug' },
	{ id: 'feature', labelKey: 'app.workbench.task.modeFeature', label: 'Add feature' },
	{ id: 'refactor', labelKey: 'app.workbench.task.modeRefactor', label: 'Refactor' },
	{ id: 'tests', labelKey: 'app.workbench.task.modeTests', label: 'Write tests' },
];

/** Auto-selects a QA preset from the detected project stack so a non-expert never
 *  has to reason about test commands. Overridable inside the Advanced disclosure. */
function autoQaPresetId(project: Project | null): string {
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
 * Governed change request form; `busy`/`result` are lifted to the page so the run survives tab
 * switches, and `onConfigureRuntime` escapes to runtime setup when nothing executable is available.
 */
export function TaskComposer({
	project,
	runtimeProviders,
	mutate,
	result,
	busy,
	onResult,
	onBusy,
	onConfigureRuntime,
}: {
	project: Project | null;
	runtimeProviders: RuntimeProviders | null;
	mutate: Mutate;
	result: IssueToPatchResponse | null;
	busy: boolean;
	onResult: (result: IssueToPatchResponse | null) => void;
	onBusy: (busy: boolean) => void;
	onConfigureRuntime: () => void;
}) {
	const { t } = useI18n();
	const [description, setDescription] = useState('');
	const [mode, setMode] = useState<TaskModeId>('fix');
	const [targetPath, setTargetPath] = useState('');
	const [runChecks, setRunChecks] = useState(true);
	const [requireReview, setRequireReview] = useState(true);
	const [preferredRuntime, setPreferredRuntime] = useState('');
	const [qaPreset, setQaPreset] = useState(() => autoQaPresetId(project));
	const [maxCostUsd, setMaxCostUsd] = useState('');
	const [issueError, setIssueError] = useState('');

	const runtimeRows = runtimeProviders?.providers ?? [];
	const executableRuntimes = useMemo(
		() => runtimeRows.filter(runtimeIsExecutableIssueRuntime),
		[runtimeRows],
	);
	const selectedRuntime = executableRuntimes.find((item) => item.id === preferredRuntime) ?? null;
	const selectedQaPreset = issueQaPresets.find((item) => item.id === qaPreset) ?? issueQaPresets[0];
	const hasExecutableRuntime = executableRuntimes.length > 0;
	const unavailableIssueRuntime = runtimeRows.find(
		(runtime) => runtimeSupportsIssueToPatch(runtime) && runtime.executable !== true,
	);
	const runtimeBlockerReason = runtimeProviders
		? (unavailableIssueRuntime?.reason ??
			t(
				'app.workbench.task.runtimeNoExecutable',
				'No executable issue_to_patch/code_edit runtime is configured.',
			))
		: t('app.workbench.task.runtimeDiscovery', 'Runtime provider discovery has not completed.');

	const activeMode = taskModes.find((item) => item.id === mode) ?? taskModes[0];
	const modeLabel = t(activeMode.labelKey, activeMode.label);
	const firstLineText = description.trim().split(/\r?\n/)[0]?.slice(0, 140) ?? '';
	const derivedTitle = firstLineText ? `${modeLabel}: ${firstLineText}`.slice(0, 180) : '';
	const effectiveQaCommands = runChecks ? selectedQaPreset.commands : [];

	const resultRuntime = objectRecord(result?.runtime);
	const resultQa = Array.isArray(result?.qaResults)
		? objectRecord(result?.qaResults[0])
		: undefined;
	const resultDiff = objectRecord(result?.diffSummary);
	const resultEvidence = objectRecord(result?.evidencePackage);
	const changedFiles = Array.isArray(resultDiff?.changedFiles)
		? resultDiff.changedFiles.length
		: Number(resultDiff?.changedFiles ?? 0);
	const resultStatus = String(result?.status ?? '');
	const executionMode =
		resultStatus === 'runtime_unavailable' ||
		resultStatus === 'unavailable' ||
		resultRuntime?.executable === false
			? 'runtime_unavailable'
			: 'productive_runtime';

	const runDisabled = !project || busy || !description.trim() || !selectedRuntime;

	// Auto-select the best executable runtime (prefer a detected one); preserve a
	// valid manual override from the Advanced disclosure.
	useEffect(() => {
		if (!executableRuntimes.length) {
			setPreferredRuntime('');
			return;
		}
		setPreferredRuntime((current) =>
			executableRuntimes.some((runtime) => runtime.id === current)
				? current
				: (executableRuntimes.find((runtime) => runtime.detected) ?? executableRuntimes[0]).id,
		);
	}, [executableRuntimes]);

	// Re-derive the QA preset from the project stack when the project changes; a
	// manual override inside Advanced persists until the project switches.
	useEffect(() => {
		setQaPreset(autoQaPresetId(project));
	}, [project?.id]);

	const runPatchWorkflow = async () => {
		const bodyText = description.trim();
		if (!project) {
			setIssueError(
				t(
					'app.workbench.task.errorProject',
					'A project is required before running issue_to_patch.',
				),
			);
			return;
		}
		if (!bodyText) {
			setIssueError(
				t('app.workbench.task.errorDescription', 'Describe the change before requesting it.'),
			);
			return;
		}
		if (!selectedRuntime) {
			setIssueError(runtimeBlockerReason);
			return;
		}
		const parsedMaxCost = maxCostUsd.trim() ? Number(maxCostUsd) : undefined;
		if (parsedMaxCost !== undefined && (!Number.isFinite(parsedMaxCost) || parsedMaxCost < 0)) {
			setIssueError(
				t('app.workbench.task.errorCost', 'Maximum cost must be zero or a positive number.'),
			);
			return;
		}
		onBusy(true);
		setIssueError('');
		onResult(null);
		try {
			const response = await mutate((token) =>
				runIssueToPatch(token, {
					projectId: project.id,
					title: derivedTitle,
					issueText: bodyText,
					targetPath: targetPath.trim() || undefined,
					preferredRuntime: selectedRuntime.id,
					qaCommands: effectiveQaCommands.length ? effectiveQaCommands : undefined,
					maxCostUsd: parsedMaxCost,
					requireApproval: requireReview,
				}),
			);
			onResult(response);
		} catch (submitError) {
			setIssueError(
				submitError instanceof Error
					? submitError.message
					: t('app.workbench.task.errorRun', 'issue_to_patch failed.'),
			);
		} finally {
			onBusy(false);
		}
	};

	return (
		<div className="form-grid">
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

			<TextArea
				label={t('app.workbench.task.changePrompt', 'What should AIDO change?')}
				value={description}
				rows={6}
				disabled={!project || busy}
				placeholder={t(
					'app.workbench.task.changePlaceholder',
					'Describe the change in plain language. The AI team plans, implements and tests it inside this workspace.',
				)}
				onChange={(event) => setDescription(event.target.value)}
			/>

			<div
				className="task-mode-chips"
				role="group"
				aria-label={t('app.workbench.task.modeGroup', 'Change type')}
			>
				{taskModes.map((item) => (
					<button
						key={item.id}
						className="button chip"
						type="button"
						aria-pressed={mode === item.id}
						disabled={!project || busy}
						onClick={() => setMode(item.id)}
					>
						{t(item.labelKey, item.label)}
					</button>
				))}
			</div>

			<div className="stack compact">
				<Checkbox
					label={t('app.workbench.task.runChecks', 'Run project checks')}
					checked={runChecks}
					disabled={!project || busy}
					onChange={(event) => setRunChecks(event.target.checked)}
				/>
				<Checkbox
					label={t('app.workbench.task.requireReview', 'Require review before applying')}
					checked={requireReview}
					disabled={!project || busy}
					onChange={(event) => setRequireReview(event.target.checked)}
				/>
				{!runChecks ? (
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
						value={preferredRuntime}
						disabled={!project || !hasExecutableRuntime || busy}
						onChange={(event) => setPreferredRuntime(event.target.value)}
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
						value={qaPreset}
						disabled={!project || busy}
						onChange={(event) => setQaPreset(event.target.value)}
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
					value={maxCostUsd}
					disabled={!project || busy}
					onChange={(event) => setMaxCostUsd(event.target.value)}
				/>
				<TextField
					label={t('app.workbench.task.target', 'Target path')}
					value={targetPath}
					disabled={!project || busy}
					placeholder={t('app.workbench.task.targetHint', 'Optional repository-relative path')}
					onChange={(event) => setTargetPath(event.target.value)}
				/>
			</Disclosure>

			<div className="inline">
				<Button
					variant="primary"
					icon={<Rocket aria-hidden="true" size={16} />}
					disabled={runDisabled}
					onClick={() => void runPatchWorkflow()}
				>
					{busy
						? t('app.workbench.task.submitting', 'Requesting change')
						: t('app.workbench.task.submit', 'Request change')}
				</Button>
				{selectedRuntime ? (
					<Badge tone="ok">{selectedRuntime.displayName}</Badge>
				) : (
					<Badge tone="danger">runtime_unavailable</Badge>
				)}
			</div>
			{issueError ? (
				<div className="form-error" role="alert">
					{issueError}
				</div>
			) : null}
			{result ? (
				<div className="stack" aria-live="polite">
					<div className="inline">
						<Badge tone={toneForStatus(resultStatus)}>{resultStatus || 'no_status'}</Badge>
						<Badge>{String(resultRuntime?.id ?? 'no_runtime')}</Badge>
						<Badge tone={toneForStatus(executionMode)}>{executionMode}</Badge>
						<Badge
							tone={
								resultQa
									? toneForStatus(String(resultQa?.status ?? resultQa?.verdict ?? ''))
									: 'warn'
							}
						>
							{String(resultQa?.status ?? resultQa?.verdict ?? 'qa_not_run')}
						</Badge>
					</div>
					<div className="mono">
						{String(
							result.reason ??
								resultRuntime?.reason ??
								t('app.workbench.task.noReason', 'No runtime reason recorded.'),
						)}
					</div>
					<div className="mono">
						{t('app.workbench.task.evidenceLine', 'Evidence')}{' '}
						{String(resultEvidence?.id ?? 'not_created')} /{' '}
						{t('app.workbench.task.changedFiles', 'changed files')}{' '}
						<span className="tnum">{Number.isFinite(changedFiles) ? changedFiles : 0}</span>
					</div>
				</div>
			) : null}
		</div>
	);
}
