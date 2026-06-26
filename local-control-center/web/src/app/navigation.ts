/**
 * Navigation registry: the canonical map of pages, the top-level areas and their
 * explorer links/labels. Drives `areaForPage` (which sidebar an area shows), the
 * ExplorerPanel and the browser tab title from one declarative source. The icon
 * rail was retired; the Go menu and command palette now carry page navigation.
 */

import type { LucideProps } from 'lucide-react';
import {
	Bot,
	ClipboardCheck,
	Code2,
	FileCheck2,
	FolderKanban,
	GitBranch,
	History,
	Home,
	KeyRound,
	LayoutGrid,
	MessagesSquare,
	Network,
	PlugZap,
	ScanSearch,
	Settings as SettingsIcon,
	ShieldCheck,
	Workflow,
} from 'lucide-react';
import type { ComponentType } from 'react';

type IconComponent = ComponentType<LucideProps>;

/** Every reachable page hash, in declaration order. The route vocabulary shared
 *  by the router, navigation registry and {@link resolveHashRoute}. */
export const pageIds = [
	'threads',
	'home',
	'workbench',
	'projects',
	'workflows',
	'review-board',
	'agents',
	'workspaces',
	'policy',
	'memory',
	'evidence',
	'models',
	'governance',
	'audit',
	'integrations',
	'assessment',
] as const;

export type PageId = (typeof pageIds)[number];

export type AreaId = 'threads' | 'home' | 'workbench' | 'runs' | 'review' | 'settings';

export type BilingualLabel = { en: string; es: string };

/** A top-level area in the ActivityBar. `leadPage` is the page opened when the
 *  area icon is clicked; `pages` are all pages that count as inside this area
 *  (used to highlight the active area regardless of which child page is shown). */
export type AreaDef = {
	id: AreaId;
	leadPage: PageId;
	icon: IconComponent;
	label: BilingualLabel;
	pages: PageId[];
};

/** One navigable entry rendered in the ExplorerPanel (and grouped sections):
 *  a target page, its icon and bilingual label. */
export type NavigationItem = {
	page: PageId;
	icon: IconComponent;
	label: BilingualLabel;
};

/** A labelled set of NavigationItems, used to group dense areas (Settings)
 *  into a progressive, scannable order instead of one flat list. */
export type ExplorerGroup = {
	label: BilingualLabel;
	links: NavigationItem[];
};

/** Top-level areas (in order). Each owns a set of pages; `areaForPage` resolves the
 *  owning area so the shell knows which sidebar to show and which tab title to set. */
export const AREAS: AreaDef[] = [
	{
		id: 'threads',
		leadPage: 'threads',
		icon: MessagesSquare,
		label: { en: 'Threads', es: 'Hilos' },
		pages: ['threads'],
	},
	{
		id: 'home',
		leadPage: 'home',
		icon: Home,
		label: { en: 'Home', es: 'Inicio' },
		pages: ['home', 'projects'],
	},
	{
		id: 'workbench',
		leadPage: 'workbench',
		icon: Code2,
		label: { en: 'Workbench', es: 'Workbench' },
		pages: ['workbench', 'workspaces'],
	},
	{
		id: 'runs',
		leadPage: 'workflows',
		icon: Workflow,
		label: { en: 'Runs', es: 'Ejecuciones' },
		pages: ['workflows', 'agents'],
	},
	{
		id: 'review',
		leadPage: 'review-board',
		icon: ClipboardCheck,
		label: { en: 'Review', es: 'Revisión' },
		pages: ['review-board', 'evidence', 'governance', 'policy', 'audit', 'assessment'],
	},
	{
		id: 'settings',
		leadPage: 'models',
		icon: SettingsIcon,
		label: { en: 'Settings', es: 'Configuración' },
		pages: ['models', 'integrations', 'memory'],
	},
];

const AREA_BY_PAGE: Record<PageId, AreaId> = pageIds.reduce(
	(map, page) => {
		const owner = AREAS.find((area) => area.pages.includes(page)) ?? AREAS[1];
		map[page] = owner.id;
		return map;
	},
	{} as Record<PageId, AreaId>,
);

/** Resolves which top-level area owns a page, so the ActivityBar can highlight
 *  the active area. Falls back to `workbench` for any unmapped page. */
export function areaForPage(page: PageId): AreaId {
	return AREA_BY_PAGE[page] ?? 'workbench';
}

/** Static, contextual ExplorerPanel links per area (dynamic lists are added in the component). */
export const EXPLORER_LINKS: Record<AreaId, NavigationItem[]> = {
	// The threads area renders the ShellSidebar (Threads/Loops tabs) instead of the flat Explorer
	// list, so this single entry is only a fallback for the navigation registry's exhaustiveness.
	threads: [{ page: 'threads', icon: MessagesSquare, label: { en: 'Threads', es: 'Hilos' } }],
	home: [
		{ page: 'home', icon: LayoutGrid, label: { en: 'Home', es: 'Inicio' } },
		{ page: 'projects', icon: FolderKanban, label: { en: 'Projects', es: 'Proyectos' } },
	],
	workbench: [
		{ page: 'workbench', icon: Code2, label: { en: 'Workbench', es: 'Workbench' } },
		{ page: 'workspaces', icon: GitBranch, label: { en: 'Workspaces', es: 'Workspaces' } },
	],
	runs: [
		{ page: 'workflows', icon: Workflow, label: { en: 'Workflows', es: 'Flujos de trabajo' } },
		{ page: 'agents', icon: Bot, label: { en: 'Agents', es: 'Agentes' } },
	],
	review: [
		{
			page: 'review-board',
			icon: LayoutGrid,
			label: { en: 'Review board', es: 'Tablero de revisión' },
		},
		{ page: 'evidence', icon: FileCheck2, label: { en: 'Evidence & QA', es: 'Evidencia y QA' } },
		{ page: 'governance', icon: KeyRound, label: { en: 'Governance', es: 'Gobierno' } },
		{
			page: 'policy',
			icon: ShieldCheck,
			label: { en: 'Policy & Security', es: 'Política y seguridad' },
		},
		{ page: 'audit', icon: History, label: { en: 'Audit Log', es: 'Auditoría' } },
		{
			page: 'assessment',
			icon: ScanSearch,
			label: { en: 'Assessment', es: 'Evaluación' },
		},
	],
	settings: [
		{
			page: 'models',
			icon: LayoutGrid,
			label: { en: 'Model Gateway', es: 'Pasarela de modelos' },
		},
		{
			page: 'integrations',
			icon: PlugZap,
			label: { en: 'Integrations', es: 'Integraciones' },
		},
		{
			page: 'memory',
			icon: Network,
			label: { en: 'Memory & Retrieval', es: 'Memoria y recuperación' },
		},
	],
};

/** Areas whose ExplorerPanel renders grouped, progressive sections instead of
 *  a single flat list. Areas absent here fall back to the flat EXPLORER_LINKS. */
export const EXPLORER_GROUPS: Partial<Record<AreaId, ExplorerGroup[]>> = {};

/** Short bilingual title for the ExplorerPanel header per area. */
export const EXPLORER_TITLE: Record<AreaId, BilingualLabel> = {
	threads: { en: 'Threads', es: 'Hilos' },
	home: { en: 'Projects', es: 'Proyectos' },
	workbench: { en: 'Workspace', es: 'Workspace' },
	runs: { en: 'Runs', es: 'Ejecuciones' },
	review: { en: 'Review', es: 'Revisión' },
	settings: { en: 'Settings', es: 'Configuración' },
};

/** Picks the label variant for the active language; defaults to English for any
 *  language other than `es`. */
export function pickLabel(label: BilingualLabel, language: string): string {
	return language === 'es' ? label.es : label.en;
}

/** Best human-readable title for the browser tab: prefer an Explorer link label,
 *  then fall back to the owning area's label. */
export function titleForPage(page: PageId, language: string): string {
	for (const areaLinks of Object.values(EXPLORER_LINKS)) {
		const link = areaLinks.find((item) => item.page === page);
		if (link) return pickLabel(link.label, language);
	}
	const area = AREAS.find((item) => item.pages.includes(page));
	return area ? pickLabel(area.label, language) : 'AIDO';
}
