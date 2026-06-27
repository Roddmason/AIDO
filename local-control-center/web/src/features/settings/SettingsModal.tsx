/**
 * Two-pane Settings modal: a section navigator on the left (search + General/Project
 * groups) and the active section's content on the right. Built on the Dialog primitive.
 * Sections are sourced from the GENERAL_SECTIONS and PROJECT_SECTIONS registry;
 * each section's render(ctx) produces its own content — no switch needed here.
 */

import { useEffect, useState } from 'react';

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
import {
	GENERAL_SECTIONS,
	PROJECT_SECTIONS,
	SECTION_TO_SETTING_SECTION,
	type SectionContext,
} from './sections';
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

	// The modal stays mounted (open=false) between opens, so re-seed the active section and clear
	// the search each time it opens (or when the requested section changes while open). Without this,
	// deep-links (#settings-security) and openSettings('project') would reopen at the last-viewed
	// section instead of the requested one.
	useEffect(() => {
		if (open) {
			setActiveSection(initialSection ?? DEFAULT_SECTION);
			setSearchQuery('');
		}
	}, [open, initialSection]);

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
	const scope: 'general' | 'project' = isProjectSection ? 'project' : 'general';
	const scopeId = isProjectSection ? (projectId ?? null) : null;
	const resolvedForSection = settingsForSection(
		isProjectSection ? project : general,
		activeSection,
		isProjectSection,
	);

	/** Builds the enum options for a given setting key (sandbox picker needs live data). */
	function enumOptionsFor(key: string): Array<{ value: string; label: string }> | undefined {
		if (key === 'security.sandboxProfileId') {
			return [
				{ value: '', label: t('app.settings.security.sandboxNone', 'None') },
				...overview.sandboxProfiles
					.filter((p) => p.status === 'active')
					.map((p) => ({ value: p.id, label: p.name })),
			];
		}
		return undefined;
	}

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

		const ctx: SectionContext = {
			resolved: resolvedForSection,
			overview,
			selectedProject,
			runtimeProviders,
			runtimeProviderConfiguration,
			token,
			setValue,
			clearValue,
			t,
			onRefresh,
			onCreateProject,
			onSelectProject,
			mutate,
			language,
			enumOptionsFor,
			scope,
			scopeId,
		};

		return currentSection.render(ctx);
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
									aria-current={activeSection === section.id ? 'page' : undefined}
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
									aria-current={activeSection === section.id ? 'page' : undefined}
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
						<p className="settings-nav-no-results">
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
