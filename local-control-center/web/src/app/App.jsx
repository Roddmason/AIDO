import React, { useEffect, useMemo, useState } from 'react';
import {
	Archive,
	Bot,
	Database,
	FolderKanban,
	LayoutDashboard,
	LockKeyhole,
	Paintbrush2,
	RefreshCw,
	Settings,
	ShieldCheck,
	Workflow,
} from 'lucide-react';

import { usePlatformData } from '../api/usePlatformData.js';
import { Badge, Button, CommandBar, IconButton, StatusDot } from '../components/primitives.jsx';
import { useMotionPreference, useSectionMotion } from '../motion/useGsapMotion.js';
import { AgentsRuntimePage } from '../features/agents-runtime/AgentsRuntimePage.jsx';
import { DesignLabPage } from '../features/design-lab/DesignLabPage.jsx';
import { IntegrationsPage } from '../features/integrations/IntegrationsPage.jsx';
import { JobsPage } from '../features/jobs/JobsPage.jsx';
import { MemoryRetrievalPage } from '../features/memory-retrieval/MemoryRetrievalPage.jsx';
import { OverviewPage } from '../features/overview/OverviewPage.jsx';
import { PipelinesPage } from '../features/pipelines/PipelinesPage.jsx';
import { SandboxSecurityPage } from '../features/sandbox-security/SandboxSecurityPage.jsx';
import { SettingsPage } from '../features/settings/SettingsPage.jsx';
import { WorkspacesSessionsPage } from '../features/workspaces-sessions/WorkspacesSessionsPage.jsx';
import { asArray, formatDate } from '../features/common/format.js';

const SECTIONS = [
	{ id: 'overview', label: 'Overview', icon: LayoutDashboard, component: OverviewPage, description: 'health, queue and events' },
	{ id: 'jobs', label: 'Jobs/Approvals', icon: ShieldCheck, component: JobsPage, description: 'leases and gates' },
	{ id: 'memory', label: 'Memory/Retrieval', icon: Database, component: MemoryRetrievalPage, description: 'FAISS and SQLite' },
	{ id: 'agents', label: 'Agents Runtime', icon: Bot, component: AgentsRuntimePage, description: 'planner visibility' },
	{ id: 'security', label: 'Sandbox/Security', icon: LockKeyhole, component: SandboxSecurityPage, description: 'policy posture' },
	{ id: 'pipelines', label: 'Pipelines', icon: Workflow, component: PipelinesPage, description: 'stages and branches' },
	{ id: 'workspaces', label: 'Workspaces/Sessions', icon: FolderKanban, component: WorkspacesSessionsPage, description: 'project context' },
	{ id: 'integrations', label: 'Integrations', icon: Archive, component: IntegrationsPage, description: 'providers and IDEs' },
	{ id: 'design', label: 'Design Lab', icon: Paintbrush2, component: DesignLabPage, description: 'artifact lane' },
	{ id: 'settings', label: 'Settings', icon: Settings, component: SettingsPage, description: 'local runtime' },
];

function initialSection() {
	const fromHash = typeof window !== 'undefined' ? window.location.hash.replace('#', '') : '';
	return SECTIONS.some((section) => section.id === fromHash) ? fromHash : 'overview';
}

export function App() {
	const data = usePlatformData();
	const [sectionId, setSectionId] = useState(initialSection);
	const [theme, setTheme] = useState(() => localStorage.getItem('lcc.theme') || 'light');
	const motionRef = useSectionMotion(sectionId);
	useMotionPreference();

	useEffect(() => {
		localStorage.setItem('lcc.theme', theme);
	}, [theme]);

	useEffect(() => {
		const next = `#${sectionId}`;
		if (window.location.hash !== next) window.history.replaceState(null, '', next);
	}, [sectionId]);

	const section = useMemo(() => SECTIONS.find((item) => item.id === sectionId) || SECTIONS[0], [sectionId]);
	const Page = section.component;
	const jobs = asArray(data.overview?.jobs);
	const pendingActions = asArray(data.overview?.actionRequests).filter((action) => action.status === 'pending');
	const runningJobs = jobs.filter((job) => job.status === 'running').length;

	return (
		<div className="control-plane" data-theme={theme}>
			<a className="skip-link" href="#main-content">
				Skip to main content
			</a>
			<div className="shell">
				<aside className="sidebar" aria-label="Control Center navigation">
					<div className="brand-lockup">
						<p className="brand-kicker">Windows native · Python backend</p>
						<strong className="brand-title">Local Control Center</strong>
					</div>
					<nav className="nav-list">
						{SECTIONS.map((item) => {
							const Icon = item.icon;
							return (
								<button
									className="nav-button"
									key={item.id}
									type="button"
									aria-current={item.id === sectionId ? 'page' : undefined}
									onClick={() => setSectionId(item.id)}
								>
									<Icon size={18} aria-hidden="true" />
									<span>{item.label}</span>
									{item.id === 'jobs' && pendingActions.length ? <Badge tone="oxblood" status="pending">{pendingActions.length}</Badge> : null}
								</button>
							);
						})}
					</nav>
					<div className="sidebar-footer">
						<div className="command-bar-group">
							<StatusDot status={data.connected ? 'ok' : 'offline'} />
							<span>{data.connected ? 'SSE live' : 'polling fallback'}</span>
						</div>
						<span>Last update {formatDate(data.lastUpdatedAt)}</span>
					</div>
				</aside>
				<div className="main-shell">
					<header className="topbar">
						<div className="topbar-title">
							<p className="eyebrow">{section.description}</p>
							<h1>{section.label}</h1>
						</div>
						<div className="topbar-actions">
							<Badge status={pendingActions.length ? 'pending' : 'ok'} tone={pendingActions.length ? 'oxblood' : 'moss'}>
								{pendingActions.length} approvals
							</Badge>
							<Badge status={runningJobs ? 'running' : 'queued'} tone={runningJobs ? 'amber' : undefined}>
								{runningJobs} running
							</Badge>
							<IconButton label="Refresh overview" variant="secondary" onClick={() => data.refresh()} disabled={data.loading}>
								<RefreshCw size={18} aria-hidden="true" />
							</IconButton>
						</div>
					</header>
					<main id="main-content" className="content" ref={motionRef}>
						{data.error ? (
							<div className="toast-region" aria-live="polite">
								<div className="alert" role="alert">
									<strong>Backend error</strong>
									<span>{data.error}</span>
								</div>
							</div>
						) : null}
						{data.loading && !data.overview ? (
							<div className="page-stack" data-motion="section">
								<div data-motion-item>
									<p className="eyebrow">Loading</p>
									<h1 className="editorial-title">Reading SQLite state from FastAPI.</h1>
								</div>
								<CommandBar meta={<Badge status="running" tone="amber">loading</Badge>}>
									<span className="muted">Waiting for `/api/v1/overview` and retrieval status.</span>
								</CommandBar>
							</div>
						) : (
							<Page data={data} theme={theme} setTheme={setTheme} />
						)}
					</main>
				</div>
			</div>
		</div>
	);
}
