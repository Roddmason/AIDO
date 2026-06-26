/**
 * Two-pane Settings modal: a section navigator on the left (search + General/Project
 * groups) and the active section's content on the right. Built on the Dialog primitive.
 * Sections are sourced from the GENERAL_SECTIONS and PROJECT_SECTIONS registry;
 * wired sections render SettingRow controls; display and placeholder sections reuse
 * existing panel bodies or the SectionPlaceholder component respectively.
 */

import { useState } from 'react';

import type {
	Overview,
	Project,
	RuntimeProviderConfiguration,
	RuntimeProviders,
} from '../../api/types';
import { Dialog } from '../../components/ui/Dialog';
import { ErrorState } from '../../components/ui/ErrorState';
import { Skeleton } from '../../components/ui/Skeleton';
import { useI18n } from '../../i18n/I18nProvider';
import { CredentialManagerPanel } from './CredentialManagerPanel';
import { SectionPlaceholder } from './SectionPlaceholder';
import { SettingRow } from './SettingRow';
import {
	AdvancedBody,
	AgentsBody,
	ConsoleLink,
	IntegrationsBody,
	ProjectBody,
	RuntimeBody,
	SecurityBody,
	WorkspacesBody,
} from './SettingsPage';
import { GENERAL_SECTIONS, PROJECT_SECTIONS, SECTION_TO_SETTING_SECTION } from './sections';
import { useSettings } from './useSettings';

export interface SettingsModalProps {
	open: boolean;
	onClose: () => void;
	/** Project id to load project-scoped settings for. */
	projectId: string | undefined;
	/** Section id to navigate to on open. */
	initialSection?: string;
	overview: Overview;
	selectedProject: Project | null;
	runtimeProviders: RuntimeProviders | null;
	runtimeProviderConfiguration: RuntimeProviderConfiguration[] | null;
	token: string;
	onRefresh: () => Promise<unknown> | undefined;
	onCreateProject: () => void;
	onSelectProject: (projectId: string) => void;
	mutate: <T>(operation: (token: string) => Promise<T>) => Promise<T>;
	language: 'en' | 'es';
}

const DEFAULT_SECTION = 'general';

/** Returns the resolved settings filtered to a particular section id. */
function settingsForSection(
	resolved: ReturnType<typeof useSettings>['general'],
	sectionId: string,
	isProject: boolean,
): ReturnType<typeof useSettings>['general'] {
	const sectionKey = SECTION_TO_SETTING_SECTION[sectionId];
	if (!sectionKey) return [];
	return resolved.filter((s) =>
		isProject ? s.projectSection === sectionKey : s.section === sectionKey,
	);
}

/** Two-pane settings modal backed by the resolved settings hook. */
export function SettingsModal({
	open,
	onClose,
	projectId,
	initialSection,
	overview,
	selectedProject,
	runtimeProviders,
	runtimeProviderConfiguration,
	token,
	onRefresh,
	onCreateProject,
	onSelectProject,
	mutate,
	language,
}: SettingsModalProps) {
	const { t } = useI18n();
	const [activeSection, setActiveSection] = useState(initialSection ?? DEFAULT_SECTION);
	const [searchQuery, setSearchQuery] = useState('');

	const { general, project, loading, error, setValue, clearValue } = useSettings(projectId, open);

	const allSections = [...GENERAL_SECTIONS, ...PROJECT_SECTIONS];
	const filteredGeneral = searchQuery
		? GENERAL_SECTIONS.filter((s) =>
				t(s.titleKey, s.titleFallback).toLowerCase().includes(searchQuery.toLowerCase()),
			)
		: GENERAL_SECTIONS;
	const filteredProject = searchQuery
		? PROJECT_SECTIONS.filter((s) =>
				t(s.titleKey, s.titleFallback).toLowerCase().includes(searchQuery.toLowerCase()),
			)
		: PROJECT_SECTIONS;

	const currentSection = allSections.find((s) => s.id === activeSection) ?? GENERAL_SECTIONS[0];

	const isProjectSection = PROJECT_SECTIONS.some((s) => s.id === activeSection);
	const resolvedForSection = settingsForSection(
		isProjectSection ? project : general,
		activeSection,
		isProjectSection,
	);

	function renderSectionContent() {
		if (loading) {
			return (
				<>
					<Skeleton
						className="settings-skeleton"
						label={t('app.settings.loading', 'Loading settings...')}
					/>
					<Skeleton className="settings-skeleton" />
					<Skeleton className="settings-skeleton" />
				</>
			);
		}
		if (error) {
			return (
				<ErrorState title={t('app.settings.error.title', 'Failed to load settings')} body={error} />
			);
		}

		const scope = isProjectSection ? 'project' : 'general';
		const scopeId = isProjectSection ? (projectId ?? null) : null;

		// Wired sections: render SettingRows for each resolved setting in this section
		if (currentSection.kind === 'wired') {
			if (resolvedForSection.length === 0) {
				return (
					<p className="muted">
						{t('app.settings.section.noSettings', 'No settings found for this section.')}
					</p>
				);
			}
			return (
				<div className="stack">
					{resolvedForSection.map((setting) => {
						const enumOptions =
							setting.key === 'security.sandboxProfileId'
								? [
										{ value: '', label: t('app.settings.security.sandboxNone', 'None') },
										...overview.sandboxProfiles
											.filter((p) => p.status === 'active')
											.map((p) => ({ value: p.id, label: p.name })),
									]
								: undefined;
						return (
							<SettingRow
								key={setting.key}
								setting={setting}
								enumOptions={enumOptions}
								onSet={(value) => setValue(setting.key, scope, scopeId, value)}
								onRevert={() => clearValue(setting.key, scope, scopeId)}
							/>
						);
					})}
				</div>
			);
		}

		// Placeholder sections
		if (currentSection.kind === 'placeholder') {
			return (
				<SectionPlaceholder
					titleKey={currentSection.titleKey}
					titleFallback={currentSection.titleFallback}
				/>
			);
		}

		// Display sections: reuse existing body components
		switch (activeSection) {
			case 'providers-cli':
				return (
					<RuntimeBody
						overview={overview}
						runtimeProviders={runtimeProviders}
						runtimeProviderConfiguration={runtimeProviderConfiguration}
						token={token}
						onRefresh={onRefresh}
					/>
				);
			case 'credentials':
			case 'project-credentials':
				return <CredentialManagerPanel token={token} />;
			case 'integrations':
			case 'project-integrations':
				return <IntegrationsBody overview={overview} />;
			case 'advanced':
			case 'project-advanced':
				return (
					<AdvancedBody
						overview={overview}
						selectedProject={selectedProject}
						mutate={mutate}
						language={language}
					/>
				);
			case 'project':
				return (
					<ProjectBody
						overview={overview}
						activeProjects={overview.projects.filter((p) => p.status === 'active')}
						selectedProject={selectedProject}
						onSelectProject={onSelectProject}
						onNewProject={onCreateProject}
					/>
				);
			case 'workspaces':
				return <WorkspacesBody overview={overview} />;
			case 'team':
			case 'quality':
			case 'routing':
				return (
					<>
						<AgentsBody overview={overview} selectedProject={selectedProject} />
						<ConsoleLink page="agents" label={t('app.settings.openAgents', 'Open Agents')} />
					</>
				);
			case 'appearance':
				return <SecurityBody overview={overview} token={token} />;
			default:
				return (
					<SectionPlaceholder
						titleKey={currentSection.titleKey}
						titleFallback={currentSection.titleFallback}
					/>
				);
		}
	}

	return (
		<Dialog
			open={open}
			onClose={onClose}
			label={t('app.settings.title', 'Settings')}
			className="settings-modal"
		>
			<div className="settings-modal-body">
				{/* Left: section navigator */}
				<nav className="settings-nav" aria-label={t('app.settings.nav.label', 'Settings sections')}>
					<div className="settings-nav-search">
						<input
							type="search"
							className="settings-nav-search-input"
							placeholder={t('app.settings.nav.searchPlaceholder', 'Search sections...')}
							value={searchQuery}
							aria-label={t('app.settings.nav.searchLabel', 'Search settings sections')}
							onChange={(e) => setSearchQuery(e.target.value)}
						/>
					</div>

					{filteredGeneral.length > 0 && (
						<>
							<p className="settings-nav-group-label" aria-hidden="true">
								{t('app.settings.nav.groupGeneral', 'General')}
							</p>
							{filteredGeneral.map((section) => (
								<button
									key={section.id}
									type="button"
									className="settings-nav-item"
									aria-current={activeSection === section.id ? 'true' : undefined}
									onClick={() => {
										setActiveSection(section.id);
										setSearchQuery('');
									}}
								>
									{t(section.titleKey, section.titleFallback)}
								</button>
							))}
						</>
					)}

					{filteredProject.length > 0 && (
						<>
							<p className="settings-nav-group-label" aria-hidden="true">
								{t('app.settings.nav.groupProject', 'Project')}
							</p>
							{filteredProject.map((section) => (
								<button
									key={section.id}
									type="button"
									className="settings-nav-item"
									aria-current={activeSection === section.id ? 'true' : undefined}
									onClick={() => {
										setActiveSection(section.id);
										setSearchQuery('');
									}}
								>
									{t(section.titleKey, section.titleFallback)}
								</button>
							))}
						</>
					)}

					{filteredGeneral.length === 0 && filteredProject.length === 0 && (
						<p
							className="muted"
							style={{ padding: 'var(--space-3)', fontSize: 'var(--font-size-sm)' }}
						>
							{t('app.settings.nav.noResults', 'No sections match your search.')}
						</p>
					)}
				</nav>

				{/* Right: content */}
				<div className="settings-content">
					<h3 className="settings-content-title">
						{t(currentSection.titleKey, currentSection.titleFallback)}
					</h3>
					{renderSectionContent()}
				</div>
			</div>
		</Dialog>
	);
}
