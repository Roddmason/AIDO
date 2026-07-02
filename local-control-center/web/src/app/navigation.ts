/**
 * Navigation registry: the canonical map of pages, the top-level areas that own
 * them and the bilingual per-page titles. Drives `areaForPage` (which area the
 * shell highlights), `titleForPage` (browser tab title) and the Settings
 * "Developer Details" link groups from one declarative source. The icon rail and
 * the ExplorerPanel were retired; the ShellSidebar, Go menu and command palette
 * now carry page navigation.
 * @author Rodrigo Mason
 */

import type { LucideProps } from 'lucide-react';
import {
	Bot,
	FileCheck2,
	History,
	KeyRound,
	LayoutGrid,
	ScanSearch,
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

type BilingualLabel = { en: string; es: string };

/** A top-level area and the pages it owns; `areaForPage` resolves the owning
 *  area so the shell knows which area is active regardless of the child page. */
type AreaDef = {
	id: AreaId;
	pages: PageId[];
};

/** One navigable entry in a Settings "Developer Details" group: a target page,
 *  its icon and bilingual label. */
export type NavigationItem = {
	page: PageId;
	icon: IconComponent;
	label: BilingualLabel;
};

/** A labelled set of NavigationItems, used to group dense link clusters into a
 *  progressive, scannable order instead of one flat list. */
export type ExplorerGroup = {
	label: BilingualLabel;
	links: NavigationItem[];
};

/** Top-level areas (in order) and the pages each one owns. */
const AREAS: AreaDef[] = [
	{ id: 'threads', pages: ['threads'] },
	{ id: 'home', pages: ['home', 'projects'] },
	{ id: 'workbench', pages: ['workbench', 'workspaces'] },
	{
		id: 'runs',
		pages: ['workflows', 'agents'],
	},
	{
		id: 'review',
		pages: ['review-board', 'evidence', 'governance', 'policy', 'audit', 'assessment'],
	},
	{
		id: 'settings',
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

/** Resolves which top-level area owns a page, so the shell can highlight the
 *  active area. Falls back to `workbench` for any unmapped page. */
export function areaForPage(page: PageId): AreaId {
	return AREA_BY_PAGE[page] ?? 'workbench';
}

/** Bilingual browser-tab title per page. The total record keeps every PageId
 *  covered at compile time when a new page is added. */
const PAGE_TITLES: Record<PageId, BilingualLabel> = {
	threads: { en: 'Threads', es: 'Hilos' },
	home: { en: 'Home', es: 'Inicio' },
	workbench: { en: 'Workbench', es: 'Workbench' },
	projects: { en: 'Projects', es: 'Proyectos' },
	workflows: { en: 'Workflows', es: 'Flujos de trabajo' },
	'review-board': { en: 'Review board', es: 'Tablero de revisión' },
	agents: { en: 'Agents', es: 'Agentes' },
	workspaces: { en: 'Workspaces', es: 'Workspaces' },
	policy: { en: 'Policy & Security', es: 'Política y seguridad' },
	memory: { en: 'Memory & Retrieval', es: 'Memoria y recuperación' },
	evidence: { en: 'Evidence & QA', es: 'Evidencia y QA' },
	models: { en: 'Model Gateway', es: 'Pasarela de modelos' },
	governance: { en: 'Governance', es: 'Gobierno' },
	audit: { en: 'Audit Log', es: 'Auditoría' },
	integrations: { en: 'Integrations', es: 'Integraciones' },
	assessment: { en: 'Assessment', es: 'Evaluación' },
};

/** Read-only technical consoles demoted from primary navigation: rendered as the
 *  grouped link clusters of the Settings "Developer Details" section. Pages stay
 *  hash-routable; this registry is their only curated entry point in the chrome. */
export const DEVELOPER_PAGE_GROUPS: ExplorerGroup[] = [
	{
		label: { en: 'Team & routing', es: 'Equipo y enrutamiento' },
		links: [
			{ page: 'agents', icon: Bot, label: { en: 'Agents', es: 'Agentes' } },
			{
				page: 'models',
				icon: LayoutGrid,
				label: { en: 'Model Gateway', es: 'Pasarela de modelos' },
			},
			{ page: 'workflows', icon: Workflow, label: { en: 'Workflows', es: 'Flujos de trabajo' } },
		],
	},
	{
		label: { en: 'Trust & security', es: 'Confianza y seguridad' },
		links: [
			{
				page: 'policy',
				icon: ShieldCheck,
				label: { en: 'Policy & Security', es: 'Política y seguridad' },
			},
			{ page: 'governance', icon: KeyRound, label: { en: 'Governance', es: 'Gobierno' } },
			{ page: 'assessment', icon: ScanSearch, label: { en: 'Assessment', es: 'Evaluación' } },
		],
	},
	{
		label: { en: 'Records & evidence', es: 'Registros y evidencia' },
		links: [
			{ page: 'evidence', icon: FileCheck2, label: { en: 'Evidence & QA', es: 'Evidencia y QA' } },
			{ page: 'audit', icon: History, label: { en: 'Audit Log', es: 'Auditoría' } },
		],
	},
];

/** Picks the label variant for the active language; defaults to English for any
 *  language other than `es`. */
export function pickLabel(label: BilingualLabel, language: string): string {
	return language === 'es' ? label.es : label.en;
}

/** Human-readable title for the browser tab, from the per-page bilingual titles. */
export function titleForPage(page: PageId, language: string): string {
	return pickLabel(PAGE_TITLES[page], language);
}
