/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useEffect, useMemo, useState } from 'react';

import { runIssueToPatch } from '../../api/client';
import type { IssueToPatchResponse } from '../../api/client';
import type { Project, RuntimeProviders } from '../../api/types';
import { Badge } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { objectRecord, runtimeIsExecutableIssueRuntime, runtimeSupportsIssueToPatch } from './workbenchSelectors';

type Mutate = <T>(operation: (token: string) => Promise<T>, options?: { awaitRefresh?: boolean }) => Promise<T>;

const issueQaPresets = [
	{ id: 'none', label: 'No QA command', commands: [] as string[][] },
	{ id: 'python-tests', label: 'Python tests', commands: [['uv', 'run', 'pytest', '-q']] },
	{ id: 'web-tests', label: 'Web tests', commands: [['corepack', 'pnpm@10.24.0', 'run', 'test:web']] },
	{ id: 'quality', label: 'Quality suite', commands: [['corepack', 'pnpm@10.24.0', 'run', 'quality']] },
];

export function TaskComposer({
	project,
	runtimeProviders,
	mutate,
	result,
	busy,
	onResult,
	onBusy,
}: {
	project: Project | null;
	runtimeProviders: RuntimeProviders | null;
	mutate: Mutate;
	result: IssueToPatchResponse | null;
	busy: boolean;
	onResult: (result: IssueToPatchResponse | null) => void;
	onBusy: (busy: boolean) => void;
}) {
	const { t } = useI18n();
	const [issueTitle, setIssueTitle] = useState('');
	const [issueText, setIssueText] = useState('');
	const [targetPath, setTargetPath] = useState('');
	const [preferredRuntime, setPreferredRuntime] = useState('');
	const [qaPreset, setQaPreset] = useState('python-tests');
	const [maxCostUsd, setMaxCostUsd] = useState('');
	const [requireApproval, setRequireApproval] = useState(true);
	const [issueError, setIssueError] = useState('');

	const runtimeRows = runtimeProviders?.providers ?? [];
	const executableRuntimes = useMemo(() => runtimeRows.filter(runtimeIsExecutableIssueRuntime), [runtimeRows]);
	const selectedRuntime = executableRuntimes.find((item) => item.id === preferredRuntime) ?? null;
	const selectedQaPreset = issueQaPresets.find((item) => item.id === qaPreset) ?? issueQaPresets[0];
	const qaMissing = selectedQaPreset.commands.length === 0;
	const hasExecutableRuntime = executableRuntimes.length > 0;
	const unavailableIssueRuntime = runtimeRows.find((runtime) => runtimeSupportsIssueToPatch(runtime) && runtime.executable !== true);
	const runtimeBlockReason = runtimeProviders
		? unavailableIssueRuntime?.reason ?? t('app.workbench.task.runtimeNoExecutable', 'No executable issue_to_patch/code_edit runtime is configured.')
		: t('app.workbench.task.runtimeDiscovery', 'Runtime provider discovery has not completed.');

	const resultRuntime = objectRecord(result?.runtime);
	const resultQa = Array.isArray(result?.qaResults) ? objectRecord(result?.qaResults[0]) : undefined;
	const resultDiff = objectRecord(result?.diffSummary);
	const resultEvidence = objectRecord(result?.evidencePackage);
	const changedFiles = Array.isArray(resultDiff?.changedFiles) ? resultDiff.changedFiles.length : Number(resultDiff?.changedFiles ?? 0);
	const resultStatus = String(result?.status ?? '');
	const executionMode = resultStatus === 'runtime_unavailable' || resultStatus === 'unavailable' || resultRuntime?.executable === false ? 'runtime_unavailable' : 'productive_runtime';

	const runDisabled = !project || busy || qaMissing || !selectedRuntime || !issueTitle.trim() || !issueText.trim();

	useEffect(() => {
		if (!executableRuntimes.length) {
			setPreferredRuntime('');
			return;
		}
		setPreferredRuntime((current) => (executableRuntimes.some((runtime) => runtime.id === current) ? current : executableRuntimes[0].id));
	}, [executableRuntimes]);

	const runPatchWorkflow = async () => {
		const title = issueTitle.trim();
		const bodyText = issueText.trim();
		if (!project) {
			setIssueError(t('app.workbench.task.errorProject', 'A project is required before running issue_to_patch.'));
			return;
		}
		if (!title) {
			setIssueError(t('app.workbench.task.errorTitle', 'Issue title is required.'));
			return;
		}
		if (!bodyText) {
			setIssueError(t('app.workbench.task.errorText', 'Issue text is required.'));
			return;
		}
		if (selectedQaPreset.commands.length === 0) {
			setIssueError(t('app.workbench.task.errorQa', 'Select a QA preset before running issue_to_patch.'));
			return;
		}
		if (!selectedRuntime) {
			setIssueError(runtimeBlockReason);
			return;
		}
		const parsedMaxCost = maxCostUsd.trim() ? Number(maxCostUsd) : undefined;
		if (parsedMaxCost !== undefined && (!Number.isFinite(parsedMaxCost) || parsedMaxCost < 0)) {
			setIssueError(t('app.workbench.task.errorCost', 'Maximum cost must be zero or a positive number.'));
			return;
		}
		onBusy(true);
		setIssueError('');
		onResult(null);
		try {
			const response = await mutate((token) =>
				runIssueToPatch(token, {
					projectId: project.id,
					title,
					issueText: bodyText,
					targetPath: targetPath.trim() || undefined,
					preferredRuntime: selectedRuntime.id,
					qaCommands: selectedQaPreset.commands,
					maxCostUsd: parsedMaxCost,
					requireApproval,
				}),
			);
			onResult(response);
		} catch (submitError) {
			setIssueError(submitError instanceof Error ? submitError.message : t('app.workbench.task.errorRun', 'issue_to_patch failed.'));
		} finally {
			onBusy(false);
		}
	};

	return (
		<div className="form-grid">
			<div className="field">
				<label htmlFor="task-title">{t('app.workbench.task.title', 'Issue title')}</label>
				<input
					id="task-title"
					className="input"
					value={issueTitle}
					maxLength={180}
					autoComplete="off"
					disabled={!project || busy}
					onChange={(event) => setIssueTitle(event.target.value)}
				/>
			</div>
			<div className="field">
				<label htmlFor="task-text">{t('app.workbench.task.text', 'Issue text')}</label>
				<textarea id="task-text" className="textarea" value={issueText} rows={6} disabled={!project || busy} onChange={(event) => setIssueText(event.target.value)} />
			</div>
			<div className="field">
				<label htmlFor="task-target">{t('app.workbench.task.target', 'Target path')}</label>
				<input
					id="task-target"
					className="input"
					value={targetPath}
					disabled={!project || busy}
					placeholder={t('app.workbench.task.targetHint', 'Optional repository-relative path')}
					onChange={(event) => setTargetPath(event.target.value)}
				/>
			</div>
			<div className="field">
				<label htmlFor="task-runtime">{t('app.workbench.task.runtime', 'Preferred runtime')}</label>
				<select id="task-runtime" className="select" value={preferredRuntime} disabled={!project || !hasExecutableRuntime || busy} onChange={(event) => setPreferredRuntime(event.target.value)}>
					{hasExecutableRuntime ? null : <option value="">{t('app.workbench.task.runtimeNone', 'No executable runtime')}</option>}
					{executableRuntimes.map((runtime) => (
						<option key={runtime.id} value={runtime.id}>
							{runtime.id} - {runtime.executable ? 'executable' : runtime.available ? 'available' : 'unavailable'}
						</option>
					))}
				</select>
				{selectedRuntime ? (
					<>
						<div className="inline">
							<Badge tone={selectedRuntime.detected ? 'ok' : 'warn'}>{selectedRuntime.detected ? 'detected' : 'not detected'}</Badge>
							<Badge tone={selectedRuntime.configured ? 'ok' : 'warn'}>{selectedRuntime.configured ? 'configured' : 'not configured'}</Badge>
							<Badge tone={selectedRuntime.available ? 'ok' : 'warn'}>{selectedRuntime.available ? 'available' : 'unavailable'}</Badge>
							<Badge tone={selectedRuntime.executable ? 'ok' : 'warn'}>{selectedRuntime.executable ? 'executable' : 'not executable'}</Badge>
						</div>
						<div className="field-help">{`${selectedRuntime.reason}${selectedRuntime.requiredConfiguration?.length ? ` Required: ${selectedRuntime.requiredConfiguration.join(', ')}` : ''}`}</div>
					</>
				) : (
					<div className="form-error" role="status">
						<Badge tone="danger">runtime_unavailable</Badge> {runtimeBlockReason}
					</div>
				)}
			</div>
			<div className="field">
				<label htmlFor="task-qa">{t('app.workbench.task.qa', 'QA preset')}</label>
				<select id="task-qa" className="select" value={qaPreset} disabled={!project || busy} onChange={(event) => setQaPreset(event.target.value)}>
					{issueQaPresets.map((preset) => (
						<option key={preset.id} value={preset.id}>{preset.label}</option>
					))}
				</select>
				<div className="field-help">{selectedQaPreset.commands.length ? selectedQaPreset.commands.map((command) => command.join(' ')).join(' | ') : t('app.workbench.task.qaNone', 'No QA command selected; issue_to_patch is blocked.')}</div>
			</div>
			<div className="field">
				<label htmlFor="task-cost">{t('app.workbench.task.cost', 'Maximum cost USD')}</label>
				<input id="task-cost" className="input tnum" type="number" min="0" step="0.01" value={maxCostUsd} disabled={!project || busy} onChange={(event) => setMaxCostUsd(event.target.value)} />
			</div>
			<div className="inline">
				<label className="checkbox-row" htmlFor="task-approval">
					<input id="task-approval" type="checkbox" checked={requireApproval} disabled={!project || busy} onChange={(event) => setRequireApproval(event.target.checked)} />
					{t('app.workbench.task.approval', 'Require approval before completion')}
				</label>
			</div>
			<div className="inline">
				<button className="button primary" type="button" disabled={runDisabled} onClick={() => void runPatchWorkflow()}>
					{busy ? t('app.workbench.task.running', 'Running issue_to_patch') : t('app.workbench.task.run', 'Run issue_to_patch')}
				</button>
				{selectedRuntime ? <Badge tone="ok">{selectedRuntime.id}</Badge> : <Badge tone="danger">runtime_unavailable</Badge>}
				{qaMissing ? <Badge tone="danger">qa_not_selected</Badge> : null}
			</div>
			{issueError ? <div className="form-error" role="alert">{issueError}</div> : null}
			{result ? (
				<div className="stack" aria-live="polite">
					<div className="inline">
						<Badge tone={toneForStatus(resultStatus)}>{resultStatus || 'no_status'}</Badge>
						<Badge>{String(resultRuntime?.id ?? 'no_runtime')}</Badge>
						<Badge tone={toneForStatus(executionMode)}>{executionMode}</Badge>
						<Badge tone={resultQa ? toneForStatus(String(resultQa?.status ?? resultQa?.verdict ?? '')) : 'warn'}>{String(resultQa?.status ?? resultQa?.verdict ?? 'qa_not_run')}</Badge>
					</div>
					<div className="mono">{String(result.reason ?? resultRuntime?.reason ?? t('app.workbench.task.noReason', 'No runtime reason recorded.'))}</div>
					<div className="mono">
						{t('app.workbench.task.evidenceLine', 'Evidence')} {String(resultEvidence?.id ?? 'not_created')} / {t('app.workbench.task.changedFiles', 'changed files')}{' '}
						<span className="tnum">{Number.isFinite(changedFiles) ? changedFiles : 0}</span>
					</div>
				</div>
			) : null}
		</div>
	);
}
