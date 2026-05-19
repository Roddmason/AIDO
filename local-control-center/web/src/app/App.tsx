import {
	Activity,
	Archive,
	Bot,
	Brain,
	ClipboardCheck,
	FileCheck2,
	Gauge,
	GitBranch,
	History,
	KeyRound,
	Network,
	RefreshCw,
	ShieldCheck,
	TerminalSquare,
	Workflow,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { useControlPlane } from '../hooks/useControlPlane';
import { useMotionPreference, usePageMotion } from '../motion/useControlMotion';
import { Badge, DataTable, Drawer, EmptyState, StatusDot } from '../components/primitives';
import { OverviewPage } from '../features/overview/OverviewPage';
import { JobsApprovalsPage } from '../features/jobs-approvals/JobsApprovalsPage';
import { WorkflowsPage } from '../features/workflows/WorkflowsPage';
import { AgentsPage } from '../features/agents/AgentsPage';
import {
	AuditPage,
	CommandCenterPage,
	EvidencePage,
	GovernancePage,
	IntegrationsPage,
	MemoryPage,
	ModelGatewayPage,
	PolicySecurityPage,
	SettingsPage,
	WorkspacesPage,
} from '../features/pages';
import { countByStatus, shortId, toneForStatus } from '../lib/format';

const navItems = [
	{ id: 'overview', label: 'Overview', icon: Gauge },
	{ id: 'command', label: 'Command Center', icon: TerminalSquare },
	{ id: 'workflows', label: 'Workflows', icon: Workflow },
	{ id: 'jobs', label: 'Jobs & Approvals', icon: ClipboardCheck },
	{ id: 'agents', label: 'Agents', icon: Bot },
	{ id: 'workspaces', label: 'Workspaces', icon: GitBranch },
	{ id: 'policy', label: 'Policy & Security', icon: ShieldCheck },
	{ id: 'memory', label: 'Memory & Retrieval', icon: Brain },
	{ id: 'evidence', label: 'Evidence & QA', icon: FileCheck2 },
	{ id: 'models', label: 'Model Gateway', icon: Network },
	{ id: 'governance', label: 'Governance', icon: KeyRound },
	{ id: 'audit', label: 'Audit Log', icon: History },
	{ id: 'integrations', label: 'Integrations', icon: Archive },
	{ id: 'settings', label: 'Settings', icon: Activity },
] as const;

type PageId = (typeof navItems)[number]['id'];

function currentHash(): PageId {
	const value = window.location.hash.replace('#', '') as PageId;
	return navItems.some((item) => item.id === value) ? value : 'overview';
}

export function App() {
	useMotionPreference();
	const [page, setPage] = useState<PageId>(currentHash());
	const [approvalDrawerOpen, setApprovalDrawerOpen] = useState(false);
	const [eventDrawerOpen, setEventDrawerOpen] = useState(false);
	const state = useControlPlane();
	const motionRef = usePageMotion(page);

	useEffect(() => {
		const onHash = () => setPage(currentHash());
		window.addEventListener('hashchange', onHash);
		return () => window.removeEventListener('hashchange', onHash);
	}, []);

	useEffect(() => {
		const onKeyDown = (event: KeyboardEvent) => {
			if (event.key === 'Escape') {
				setApprovalDrawerOpen(false);
				setEventDrawerOpen(false);
			}
		};
		window.addEventListener('keydown', onKeyDown);
		return () => window.removeEventListener('keydown', onKeyDown);
	}, []);

	const overview = state.overview;
	const selectedProject = overview?.projects[0];
	const runningJobs = overview ? countByStatus(overview.jobs, 'running') : 0;
	const pendingApprovals = overview?.actionRequests.filter((item) => item.status === 'pending').length ?? 0;
	const costToday = useMemo(
		() => overview?.costUsage.reduce((total, row) => total + Number(row.amountUsd ?? 0), 0) ?? 0,
		[overview],
	);

	const pageContent = () => {
		if (state.loading || !overview) {
			return <EmptyState title="Loading control plane" body="Waiting for FastAPI v1, SQLite and runtime providers." />;
		}
		if (state.error) {
			return <EmptyState title="Control plane unavailable" body={state.error} />;
		}
		switch (page) {
			case 'command':
				return <CommandCenterPage overview={overview} mutate={state.mutate} />;
			case 'workflows':
				return <WorkflowsPage overview={overview} />;
			case 'jobs':
				return <JobsApprovalsPage overview={overview} mutate={state.mutate} />;
			case 'agents':
				return <AgentsPage overview={overview} runtimeProviders={state.runtimeProviders} mutate={state.mutate} />;
			case 'workspaces':
				return <WorkspacesPage overview={overview} />;
			case 'policy':
				return <PolicySecurityPage overview={overview} />;
			case 'memory':
				return <MemoryPage overview={overview} retrievalStatus={state.retrievalStatus} />;
			case 'evidence':
				return <EvidencePage overview={overview} />;
			case 'models':
				return <ModelGatewayPage overview={overview} runtimeProviders={state.runtimeProviders} mutate={state.mutate} />;
			case 'governance':
				return <GovernancePage overview={overview} />;
			case 'audit':
				return <AuditPage overview={overview} />;
			case 'integrations':
				return <IntegrationsPage overview={overview} />;
			case 'settings':
				return <SettingsPage />;
			default:
				return <OverviewPage overview={overview} runtimeProviders={state.runtimeProviders} />;
		}
	};

	return (
		<div className="app-shell">
			<aside className="sidebar" aria-label="Primary navigation">
				<div className="brand-mark">
					<div className="brand-kicker">Windows native · v1 only</div>
					<h1 className="brand-title">AIDO Control Center</h1>
				</div>
				<nav className="nav-list">
					{navItems.map((item) => {
						const Icon = item.icon;
						return (
							<button
								key={item.id}
								type="button"
								className="nav-item"
								aria-current={page === item.id ? 'page' : undefined}
								onClick={() => {
									window.location.hash = item.id;
									setPage(item.id);
								}}
							>
								<Icon aria-hidden="true" />
								<span>{item.label}</span>
							</button>
						);
					})}
				</nav>
				<div className="sidebar-footer">
					<div className="inline"><StatusDot tone={state.connected ? 'ok' : 'warn'} /> {state.connected ? 'SSE connected' : 'polling fallback'}</div>
					<div>{state.lastUpdatedAt ? `Updated ${new Date(state.lastUpdatedAt).toLocaleTimeString()}` : 'Waiting for data'}</div>
				</div>
			</aside>
			<main className="main-area">
				<header className="topbar">
					<div className="topbar-primary">
						<div>
							<div className="page-kicker">Project · Workspace · Runtime</div>
							<h2 className="topbar-title">{selectedProject?.name ?? 'Runtime project'}</h2>
						</div>
						<div className="topbar-actions">
							<Badge tone={pendingApprovals ? 'warn' : 'ok'}>{pendingApprovals} approvals</Badge>
							<Badge tone={runningJobs ? 'warn' : 'ok'}>{runningJobs} running</Badge>
							<Badge>{costToday.toFixed(2)} USD today</Badge>
							<button className="button" type="button" onClick={() => setApprovalDrawerOpen(true)}>
								Open approvals drawer
							</button>
							<button className="button" type="button" onClick={() => setEventDrawerOpen(true)}>
								Open event drawer
							</button>
							<button className="icon-button" type="button" aria-label="Refresh state" onClick={() => void state.refresh()}>
								<RefreshCw aria-hidden="true" size={18} />
							</button>
						</div>
					</div>
				</header>
				<div className="status-strip" aria-label="Global status">
					<Badge tone={state.connected ? 'ok' : 'warn'}>SSE {state.connected ? 'connected' : 'fallback'}</Badge>
					<Badge tone="ok">SQLite canonical</Badge>
					<Badge tone={state.runtimeProviders?.ollama.available ? 'ok' : 'warn'}>Ollama {state.runtimeProviders?.ollama.available ? 'ready' : 'optional'}</Badge>
					<Badge tone="ok">Policy engine active</Badge>
					<Badge>Sandbox gated</Badge>
				</div>
				<section ref={motionRef} className="content-frame motion-scope" aria-live="polite">
					{pageContent()}
				</section>
			</main>
			{overview ? (
				<>
					<Drawer label="Approval drawer" open={approvalDrawerOpen} onClose={() => setApprovalDrawerOpen(false)}>
						<DataTable
							rows={overview.actionRequests.filter((item) => item.status === 'pending').slice(0, 12)}
							empty={<EmptyState title="No pending approvals" body="Action requests appear here when policy gates execution." />}
							columns={[
								{ key: 'action', label: 'Action', render: (row) => <span className="mono">{row.actionType}</span> },
								{ key: 'risk', label: 'Risk', render: (row) => <Badge tone={toneForStatus(row.riskLevel)}>{row.riskLevel}</Badge> },
								{ key: 'command', label: 'Command', render: (row) => <span className="mono">{row.command || 'n/a'}</span> },
								{ key: 'job', label: 'Job', render: (row) => <span className="mono">{shortId(row.jobId)}</span> },
							]}
						/>
					</Drawer>
					<Drawer label="Event drawer" open={eventDrawerOpen} onClose={() => setEventDrawerOpen(false)}>
						<DataTable
							rows={overview.events.slice(0, 16)}
							empty={<EmptyState title="No events" body="Workflow, job, policy, and evidence events appear here." />}
							columns={[
								{ key: 'type', label: 'Type', render: (row) => <span className="mono">{row.type}</span> },
								{ key: 'severity', label: 'Severity', render: (row) => <Badge tone={toneForStatus(row.severity)}>{row.severity ?? 'info'}</Badge> },
								{ key: 'id', label: 'Event', render: (row) => <span className="mono">{shortId(row.id)}</span> },
							]}
						/>
					</Drawer>
				</>
			) : null}
		</div>
	);
}
