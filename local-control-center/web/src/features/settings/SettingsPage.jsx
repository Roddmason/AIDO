import React from 'react';
import { Moon, RefreshCw, Sun } from 'lucide-react';

import { Badge, Button, Surface } from '../../components/primitives.jsx';
import { asArray, formatDate } from '../common/format.js';

export function SettingsPage({ data, theme, setTheme }) {
	const overview = data.overview || {};
	const projects = asArray(overview.projects);
	const activeProject = projects[0];

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Settings</p>
				<h1 className="editorial-title">Runtime settings stay explicit and local.</h1>
			</div>
			<div className="section-grid" data-motion-item>
				<Surface title="Theme" kicker="Local Preference">
					<div className="command-bar-group">
						<Button variant={theme === 'light' ? 'primary' : 'secondary'} onClick={() => setTheme('light')}>
							<Sun size={16} aria-hidden="true" /> Light
						</Button>
						<Button variant={theme === 'dark' ? 'primary' : 'secondary'} onClick={() => setTheme('dark')}>
							<Moon size={16} aria-hidden="true" /> Dark
						</Button>
					</div>
				</Surface>
				<Surface title="Connection" kicker="SSE + Polling">
					<div className="timeline">
						<div className="timeline-item">
							<Badge status={data.connected ? 'ok' : 'offline'}>{data.connected ? 'SSE connected' : 'polling fallback'}</Badge>
							<div className="timeline-content">
								<strong>Last refresh</strong>
								<span className="muted">{formatDate(data.lastUpdatedAt)}</span>
							</div>
						</div>
					</div>
					<div style={{ marginTop: 'var(--space-md)' }}>
						<Button variant="secondary" onClick={() => data.refresh()}>
							<RefreshCw size={16} aria-hidden="true" /> Refresh
						</Button>
					</div>
				</Surface>
				<Surface title="Active Workspace" kicker="Windows Native">
					<div className="timeline">
						<div className="timeline-item">
							<Badge status={activeProject?.status || 'ok'}>{activeProject?.status || 'active'}</Badge>
							<div className="timeline-content">
								<strong>{activeProject?.name || 'runtime project'}</strong>
								<span className="muted mono">{activeProject?.path || 'not reported'}</span>
							</div>
						</div>
					</div>
				</Surface>
			</div>
		</div>
	);
}
