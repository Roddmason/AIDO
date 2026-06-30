/**
 * Three-step wizard (source -> review -> open) for opening an existing folder or
 * creating a new workspace, then turning it into a project.
 * Pure presentation/step orchestration: all detection and the create call live in the
 * `useProjectDiscovery` hook; this component only sequences the steps, gates Next on
 * validation, and reports the created project id back to the caller.
 * @author Rodrigo Mason
 */

import { FolderOpen, FolderPlus, Search } from 'lucide-react';
import { useEffect, useState } from 'react';

import type { Overview } from '../../api/types';
import { Modal } from '../../components/primitives';
import { useI18n } from '../../i18n/I18nProvider';
import { ProjectDiscoverySummary } from './ProjectDiscoverySummary';
import type { WorkspaceMode } from './useProjectDiscovery';
import { useProjectDiscovery } from './useProjectDiscovery';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;
type DialogStep = 'source' | 'review' | 'open';

const STEP_ORDER: DialogStep[] = ['source', 'review', 'open'];

/**
 * New-workspace wizard modal. Re-seeds its step and the hook's state each time it
 * (re)opens, and calls `onCreated` with the new project id once the open step succeeds.
 */
export function NewWorkspaceDialog({
	open,
	overview,
	mutate,
	initialMode = 'open_folder',
	onClose,
	onCreated,
}: {
	open: boolean;
	overview: Overview;
	mutate: Mutate;
	initialMode?: WorkspaceMode;
	onClose: () => void;
	onCreated: (projectId: string) => void;
}) {
	const { t } = useI18n();
	const workspace = useProjectDiscovery(overview, mutate);
	const [step, setStep] = useState<DialogStep>('source');

	// biome-ignore lint/correctness/useExhaustiveDependencies: re-seed once per open; `workspace.reset`/`setMode` are re-created every render, so depending on them would reset the form on every keystroke.
	useEffect(() => {
		if (!open) return;
		workspace.reset();
		workspace.setMode(initialMode);
		setStep('source');
	}, [open, initialMode]);

	if (!open) return null;

	const stepIndex = STEP_ORDER.indexOf(step);
	const stepLabels: Record<DialogStep, string> = {
		source: t('app.workspace.step.source', 'Choose source'),
		review: t('app.workspace.step.review', 'Review detection'),
		open: t('app.workspace.step.open', 'Open'),
	};

	const goReview = () => {
		const message = workspace.validate();
		if (message) {
			workspace.setError(message);
			return;
		}
		workspace.setError('');
		setStep('review');
	};

	const handleOpen = async () => {
		const result = await workspace.submit();
		if (result) onCreated(result.id);
	};

	return (
		<Modal open={open} label={t('app.workspace.dialog.title', 'New workspace')} onClose={onClose}>
			<div className="form-grid">
				<ol
					className="wizard-steps"
					aria-label={t('app.workspace.steps.aria', 'Workspace setup steps')}
				>
					{STEP_ORDER.map((item, index) => (
						<li key={item} className={item === step ? 'current' : ''}>
							<span className="mono">0{index + 1}</span>
							<strong>{stepLabels[item]}</strong>
						</li>
					))}
				</ol>

				{step === 'source' ? (
					<>
						<fieldset
							className="workspace-mode-grid"
							aria-label={t('app.workspace.mode.aria', 'Workspace source')}
							// Reset the <fieldset> user-agent chrome so the grid box matches the prior <div>.
							style={{ margin: 0, padding: 0, border: 0, minInlineSize: 0 }}
						>
							<button
								type="button"
								className="card workspace-mode-card"
								data-selected={workspace.mode === 'open_folder' ? 'true' : undefined}
								aria-pressed={workspace.mode === 'open_folder'}
								onClick={() => workspace.setMode('open_folder')}
							>
								<span className="card-header">
									<FolderOpen size={18} aria-hidden="true" />
								</span>
								<strong className="card-title">
									{t('app.workspace.mode.openFolder', 'Open folder')}
								</strong>
								<span className="card-body">
									{t(
										'app.workspace.mode.openFolderHint',
										'Use a local folder that already contains a project.',
									)}
								</span>
							</button>
							<button
								type="button"
								className="card workspace-mode-card"
								data-selected={workspace.mode === 'create_workspace' ? 'true' : undefined}
								aria-pressed={workspace.mode === 'create_workspace'}
								onClick={() => workspace.setMode('create_workspace')}
							>
								<span className="card-header">
									<FolderPlus size={18} aria-hidden="true" />
								</span>
								<strong className="card-title">
									{t('app.workspace.mode.create', 'Create workspace')}
								</strong>
								<span className="card-body">
									{t(
										'app.workspace.mode.createHint',
										'Create a new folder and start an empty workspace.',
									)}
								</span>
							</button>
						</fieldset>

						{workspace.mode === 'open_folder' ? (
							<div className="field">
								<label htmlFor="workspace-folder">
									{t('app.workspace.field.folder', 'Workspace folder')}
								</label>
								<div className="inline">
									<input
										id="workspace-folder"
										className="input"
										value={workspace.workspaceFolder}
										autoComplete="off"
										onChange={(event) => workspace.setWorkspaceFolder(event.target.value)}
									/>
									<button
										className="button primary"
										type="button"
										onClick={() => void workspace.browseDirectory('workspaceFolder')}
										disabled={workspace.pickerBusy}
									>
										<FolderOpen size={16} aria-hidden="true" />
										{t('app.workspace.action.openFolder', 'Open folder')}
									</button>
								</div>
								<span className="field-help">
									{t(
										'app.workspace.field.folderHelp',
										'Pick a folder and AIDO detects the project automatically.',
									)}
								</span>
							</div>
						) : (
							<>
								<div className="field">
									<label htmlFor="workspace-base-path">
										{t('app.workspace.field.basePath', 'Workspace base path')}
									</label>
									<div className="inline">
										<input
											id="workspace-base-path"
											className="input"
											value={workspace.workspaceBasePath}
											autoComplete="off"
											onChange={(event) => workspace.setWorkspaceBasePath(event.target.value)}
										/>
										<button
											className="button"
											type="button"
											onClick={() => void workspace.browseDirectory('workspaceBasePath')}
											disabled={workspace.pickerBusy}
										>
											<FolderOpen size={16} aria-hidden="true" />
											{t('app.workspace.action.openFolder', 'Open folder')}
										</button>
									</div>
								</div>
								<div className="field">
									<label htmlFor="workspace-name">
										{t('app.workspace.field.workspaceName', 'Workspace name')}
									</label>
									<input
										id="workspace-name"
										className="input"
										value={workspace.workspaceName}
										autoComplete="off"
										onChange={(event) => workspace.updateWorkspaceName(event.target.value)}
									/>
									<span className="field-help">
										{t('app.workspace.field.finalPath', 'Final path:')}{' '}
										<span className="mono">
											{workspace.finalPath || 'workspace/workspace-name'}
										</span>
									</span>
									{workspace.workspaceNameConflict ? (
										<span className="form-error" role="alert">
											{t('app.workspace.error.nameExists', 'Workspace name already exists.')}
										</span>
									) : null}
								</div>
							</>
						)}

						<button
							className="button"
							type="button"
							onClick={() => void workspace.runDiscovery()}
							disabled={workspace.discoveryBusy || workspace.pickerBusy}
						>
							<Search size={16} aria-hidden="true" />
							{t('app.workspace.action.detect', 'Detect project')}
						</button>
					</>
				) : null}

				{step === 'review' ? (
					<>
						<ProjectDiscoverySummary
							markers={workspace.detectionMarkers}
							runtimeLabels={workspace.runtimeLabels}
						/>

						<div className="field">
							<label htmlFor="workspace-project-name">
								{t('app.workspace.field.projectName', 'Project name')}
							</label>
							<input
								id="workspace-project-name"
								className="input"
								value={workspace.name}
								maxLength={140}
								autoComplete="off"
								onChange={(event) => workspace.setName(event.target.value)}
							/>
						</div>
						<div className="field">
							<label htmlFor="workspace-template">
								{t('app.workspace.field.template', 'Project template')}
							</label>
							<select
								id="workspace-template"
								className="select"
								value={workspace.templateId}
								onChange={(event) => workspace.setTemplateId(event.target.value)}
							>
								{overview.projectTemplates.map((template) => (
									<option key={template.id} value={template.id}>
										{template.name}
									</option>
								))}
							</select>
						</div>
						<button
							className="button"
							type="button"
							onClick={() => void workspace.runDiscovery()}
							disabled={workspace.discoveryBusy || workspace.pickerBusy}
						>
							<Search size={16} aria-hidden="true" />
							{t('app.workspace.action.detect', 'Detect project')}
						</button>
					</>
				) : null}

				{step === 'open' ? (
					<div className="review-grid">
						<div>
							<span className="muted">{t('app.workspace.summary.name', 'Name')}</span>
							<strong>{workspace.name.trim()}</strong>
						</div>
						<div>
							<span className="muted">{t('app.workspace.summary.source', 'Source')}</span>
							<strong>
								{workspace.mode === 'create_workspace'
									? t('app.workspace.mode.create', 'Create workspace')
									: t('app.workspace.mode.openFolder', 'Open folder')}
							</strong>
						</div>
						<div>
							<span className="muted">{t('app.workspace.summary.path', 'Path')}</span>
							<strong className="mono">{workspace.finalPath}</strong>
						</div>
						<div>
							<span className="muted">{t('app.workspace.summary.template', 'Template')}</span>
							<strong className="mono">{workspace.templateId}</strong>
						</div>
						<div>
							<span className="muted">
								{t('app.workspace.detection.runtimes', 'Runtimes detected')}
							</span>
							<strong>
								{workspace.runtimeLabels.length || t('app.workspace.detection.none', 'none')}
							</strong>
						</div>
					</div>
				) : null}

				{workspace.error ? (
					<div className="form-error" role="alert">
						{workspace.error}
					</div>
				) : null}

				<div className="wizard-actions">
					<button
						className="button"
						type="button"
						onClick={
							stepIndex === 0
								? onClose
								: () => {
										workspace.setError('');
										setStep(STEP_ORDER[Math.max(stepIndex - 1, 0)]);
									}
						}
						disabled={workspace.busy}
					>
						{stepIndex === 0
							? t('app.workspace.action.cancel', 'Cancel')
							: t('app.workspace.action.back', 'Back')}
					</button>
					{step === 'open' ? (
						<button
							className="button primary"
							type="button"
							onClick={() => void handleOpen()}
							disabled={workspace.busy}
						>
							{t('app.workspace.action.openInWorkbench', 'Open in workbench')}
						</button>
					) : (
						<button
							className="button primary"
							type="button"
							onClick={step === 'source' ? goReview : () => setStep('open')}
							disabled={
								workspace.busy ||
								workspace.discoveryBusy ||
								workspace.pickerBusy ||
								workspace.workspaceNameConflict
							}
						>
							{t('app.workspace.action.next', 'Next')}
						</button>
					)}
				</div>
			</div>
		</Modal>
	);
}
