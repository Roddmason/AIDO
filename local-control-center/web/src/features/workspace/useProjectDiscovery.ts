/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
import { useMemo, useState } from 'react';

import { createProject, discoverProject, selectLocalDirectory } from '../../api/client';
import type { JsonValue } from '../../api/generated/openapi';
import type { Overview } from '../../api/types';
import { useI18n } from '../../i18n/I18nProvider';
import { joinLocalPath, lastPathSegment, prettyNameFromDirectory, slugFromName } from '../../lib/paths';

type Mutate = <T>(operation: (token: string) => Promise<T>) => Promise<T>;

export type WorkspaceMode = 'open_folder' | 'create_workspace';

export type ProjectDiscovery = {
	path?: string;
	exists?: boolean;
	suggestedName?: string;
	templateId?: string;
	manifestSources?: Array<Record<string, unknown>>;
	detectedRuntimes?: Array<Record<string, unknown>>;
};

export type DetectionMarker = {
	/** Manifest filename as returned by backend discovery (rendered verbatim). */
	file: string;
	detected: boolean;
};

/** Markers shown as detection cards, in display order. Filenames must match discovery output. */
export const DETECTION_MARKER_FILES = ['.git', 'package.json', 'pyproject.toml', 'requirements.txt', 'go.mod', 'Cargo.toml'] as const;

/** Legacy metadata values kept stable so the backend and existing records do not drift. */
const METADATA_CREATION_MODE: Record<WorkspaceMode, string> = {
	open_folder: 'import_existing_workspace',
	create_workspace: 'create_from_zero',
};

function manifestName(source: Record<string, unknown>): string {
	return String(source.manifest ?? '');
}

function runtimeLabel(runtime: Record<string, unknown>): string {
	return String(runtime.label ?? runtime.id ?? '');
}

export function useProjectDiscovery(overview: Overview, mutate: Mutate) {
	const { t } = useI18n();
	const [mode, setModeState] = useState<WorkspaceMode>('open_folder');
	const [name, setNameState] = useState('');
	const [nameTouched, setNameTouched] = useState(false);
	const [workspaceFolder, setWorkspaceFolder] = useState('');
	const [workspaceBasePath, setWorkspaceBasePath] = useState('');
	const [workspaceName, setWorkspaceNameState] = useState('');
	const [templateId, setTemplateId] = useState(overview.projectTemplates[0]?.id ?? 'other');
	const [createDirectory, setCreateDirectory] = useState(false);
	const [discovery, setDiscovery] = useState<ProjectDiscovery | null>(null);
	const [error, setError] = useState('');
	const [busy, setBusy] = useState(false);
	const [discoveryBusy, setDiscoveryBusy] = useState(false);
	const [pickerBusy, setPickerBusy] = useState(false);

	const finalPath = mode === 'create_workspace' ? joinLocalPath(workspaceBasePath, workspaceName) : workspaceFolder.trim();
	const detectedRuntimes = discovery?.detectedRuntimes ?? [];
	const manifestSources = discovery?.manifestSources ?? [];
	const runtimeLabels = useMemo(() => detectedRuntimes.map(runtimeLabel).filter(Boolean), [detectedRuntimes]);
	const detectionMarkers = useMemo<DetectionMarker[]>(() => {
		const present = new Set(manifestSources.map(manifestName));
		return DETECTION_MARKER_FILES.map((file) => ({ file, detected: present.has(file) }));
	}, [manifestSources]);

	const workspaceNameConflict = useMemo(() => {
		if (mode !== 'create_workspace') return false;
		const normalized = workspaceName.trim().toLowerCase();
		if (!normalized) return false;
		const projectConflict = overview.projects.some((project) => {
			return project.name.trim().toLowerCase() === normalized || lastPathSegment(project.path).toLowerCase() === normalized;
		});
		const workspaceConflict = overview.runtimeWorkspaces.some((workspace) => {
			return lastPathSegment(workspace.path).toLowerCase() === normalized || String(workspace.taskId ?? '').trim().toLowerCase() === normalized;
		});
		return projectConflict || workspaceConflict;
	}, [mode, overview.projects, overview.runtimeWorkspaces, workspaceName]);

	const applyDiscovery = (next: ProjectDiscovery) => {
		setDiscovery(next);
		if (next.suggestedName && !nameTouched) {
			setNameState(next.suggestedName);
		}
		if (next.templateId && overview.projectTemplates.some((template) => template.id === next.templateId)) {
			setTemplateId(next.templateId);
		}
	};

	const setMode = (next: WorkspaceMode) => {
		setModeState(next);
		setDiscovery(null);
		setError('');
		setCreateDirectory(next === 'create_workspace');
	};

	const setName = (value: string) => {
		setNameTouched(true);
		setNameState(value);
		if (!workspaceName && value.trim()) {
			setWorkspaceNameState(slugFromName(value));
		}
	};

	const updateWorkspaceName = (value: string) => {
		setWorkspaceNameState(value);
		if (!nameTouched && value.trim()) {
			setNameState(prettyNameFromDirectory(value));
		}
	};

	const runDiscovery = async (targetPath = finalPath) => {
		if (!targetPath.trim()) {
			setError(mode === 'create_workspace' ? t('app.workspace.error.basePathAndName', 'Workspace base path and workspace name are required.') : t('app.workspace.error.folderRequired', 'Workspace folder is required.'));
			return;
		}
		setDiscoveryBusy(true);
		setError('');
		try {
			const result = await mutate((token) => discoverProject(token, { path: targetPath.trim() }));
			applyDiscovery(result.discovery as ProjectDiscovery);
		} catch (caught) {
			setError(caught instanceof Error ? caught.message : t('app.workspace.error.detect', 'Project discovery failed.'));
		} finally {
			setDiscoveryBusy(false);
		}
	};

	const browseDirectory = async (target: 'workspaceBasePath' | 'workspaceFolder') => {
		setPickerBusy(true);
		setError('');
		try {
			const initialPath = target === 'workspaceBasePath' ? workspaceBasePath || workspaceFolder || undefined : workspaceFolder || workspaceBasePath || undefined;
			const result = await mutate((token) => selectLocalDirectory(token, { title: t('app.workspace.picker.title', 'Open project folder'), initialPath }));
			if (result.status === 'selected' && result.selectedPath) {
				if (target === 'workspaceBasePath') {
					setWorkspaceBasePath(result.selectedPath);
				} else {
					setWorkspaceFolder(result.selectedPath);
					await runDiscovery(result.selectedPath);
				}
				return;
			}
			if (result.status === 'unavailable') {
				setError(result.reason ?? t('app.workspace.error.pickerUnavailable', 'Native directory picker is unavailable. Enter the path manually.'));
			}
		} catch (caught) {
			setError(caught instanceof Error ? caught.message : t('app.workspace.error.picker', 'Directory picker failed.'));
		} finally {
			setPickerBusy(false);
		}
	};

	const validate = () => {
		if (mode === 'create_workspace') {
			if (!workspaceBasePath.trim()) return t('app.workspace.error.basePathRequired', 'Workspace base path is required.');
			if (!workspaceName.trim()) return t('app.workspace.error.nameMissing', 'Workspace name is required.');
			if (workspaceNameConflict) return t('app.workspace.error.nameExists', 'Workspace name already exists.');
		} else if (!workspaceFolder.trim()) {
			return t('app.workspace.error.folderRequired', 'Workspace folder is required.');
		}
		if (!name.trim()) return t('app.workspace.error.nameRequired', 'Project name is required.');
		if (!overview.projectTemplates.some((template) => template.id === templateId)) {
			return t('app.workspace.error.templateInvalid', 'Project template is invalid.');
		}
		return '';
	};

	const submit = async (): Promise<{ id: string } | null> => {
		const message = validate();
		if (message) {
			setError(message);
			return null;
		}
		setBusy(true);
		setError('');
		try {
			const metadata: Record<string, JsonValue> = {
				source: 'workspace_dialog',
				creationMode: METADATA_CREATION_MODE[mode],
				workspaceFlow: METADATA_CREATION_MODE[mode],
				detectedRuntimes: detectedRuntimes as JsonValue[],
				manifestSources: manifestSources as JsonValue[],
			};
			if (workspaceBasePath.trim()) metadata.workspaceBasePath = workspaceBasePath.trim();
			if (workspaceName.trim()) metadata.projectDirectoryName = workspaceName.trim();
			if (workspaceFolder.trim()) metadata.workspaceFolder = workspaceFolder.trim();
			const result = await mutate((token) =>
				createProject(token, {
					name: name.trim(),
					path: mode === 'open_folder' ? workspaceFolder.trim() : undefined,
					workspaceBasePath: mode === 'create_workspace' ? workspaceBasePath.trim() : undefined,
					projectDirectoryName: mode === 'create_workspace' ? workspaceName.trim() : undefined,
					templateId,
					createDirectory,
					metadata,
				}),
			);
			return { id: result.project.id };
		} catch (caught) {
			setError(caught instanceof Error ? caught.message : t('app.workspace.error.create', 'Project creation failed.'));
			return null;
		} finally {
			setBusy(false);
		}
	};

	const reset = () => {
		setModeState('open_folder');
		setNameState('');
		setNameTouched(false);
		setWorkspaceFolder('');
		setWorkspaceBasePath('');
		setWorkspaceNameState('');
		setTemplateId(overview.projectTemplates[0]?.id ?? 'other');
		setCreateDirectory(false);
		setDiscovery(null);
		setError('');
		setBusy(false);
		setDiscoveryBusy(false);
		setPickerBusy(false);
	};

	return {
		mode,
		name,
		workspaceFolder,
		workspaceBasePath,
		workspaceName,
		templateId,
		createDirectory,
		discovery,
		finalPath,
		detectedRuntimes,
		manifestSources,
		runtimeLabels,
		detectionMarkers,
		workspaceNameConflict,
		error,
		busy,
		discoveryBusy,
		pickerBusy,
		setMode,
		setName,
		updateWorkspaceName,
		setWorkspaceFolder,
		setWorkspaceBasePath,
		setTemplateId,
		setCreateDirectory,
		setError,
		browseDirectory,
		runDiscovery,
		validate,
		submit,
		reset,
	};
}
