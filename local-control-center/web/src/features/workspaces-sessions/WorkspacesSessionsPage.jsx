import React from 'react';
import { FolderKanban, MessageSquareText } from 'lucide-react';

import { Badge, DataTable, EmptyState, Surface } from '../../components/primitives.jsx';
import { asArray, formatDate, shortId } from '../common/format.js';

export function WorkspacesSessionsPage({ data }) {
	const overview = data.overview || {};
	const legacy = data.legacyState?.state || {};
	const projects = asArray(overview.projects);
	const sessions = asArray(overview.sessions);
	const chats = asArray(legacy.chats);

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Workspaces/Sessions</p>
				<h1 className="editorial-title">Project context, sessions and chat lineage stay inspectable.</h1>
			</div>
			<div className="split-pane" data-motion-item>
				<Surface title="Workspaces" kicker="Projects">
					<DataTable
						rows={projects}
						empty={<EmptyState title="No workspaces" body="Runtime project creation should expose at least the current workspace." />}
						columns={[
							{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status || 'ok'}>{row.status || 'active'}</Badge> },
							{ key: 'name', label: 'Name' },
							{ key: 'templateId', label: 'Template' },
							{ key: 'path', label: 'Path', render: (row) => <code>{row.path}</code> },
						]}
					/>
				</Surface>
				<Surface title="Active Context" kicker="Pointers">
					<div className="timeline">
						{[
							['active team', legacy.activeTeamId],
							['active session', legacy.activeSessionId],
							['active chat', legacy.activeChatId],
							['active pipeline', legacy.activePipelineId],
						].map(([label, value]) => (
							<div className="timeline-item" key={label}>
								<FolderKanban size={18} aria-hidden="true" />
								<div className="timeline-content">
									<strong>{label}</strong>
									<span className="muted mono">{shortId(value)}</span>
								</div>
							</div>
						))}
					</div>
				</Surface>
			</div>
			<Surface title="Sessions" kicker="SQLite">
				<DataTable
					rows={sessions}
					empty={<EmptyState title="No sessions" body="Session records imported from legacy JSON or created by chat routes will appear here." />}
					columns={[
						{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status || 'ok'}>{row.status || 'active'}</Badge> },
						{ key: 'name', label: 'Name' },
						{ key: 'teamId', label: 'Team', render: (row) => <code>{shortId(row.teamId)}</code> },
						{ key: 'createdAt', label: 'Created', render: (row) => formatDate(row.createdAt) },
					]}
				/>
			</Surface>
			<Surface title="Chats" kicker="Conversation Runs">
				<DataTable
					rows={chats}
					empty={<EmptyState title="No chats" body="Chat routes write compatible records through the Python backend." />}
					columns={[
						{ key: 'title', label: 'Title' },
						{ key: 'mode', label: 'Mode' },
						{ key: 'sessionId', label: 'Session', render: (row) => <code>{shortId(row.sessionId)}</code> },
						{ key: 'runs', label: 'Runs', render: (row) => asArray(row.runs).length },
						{
							key: 'latest',
							label: 'Latest output',
							render: (row) => (
								<span className="subtle">
									<MessageSquareText size={14} aria-hidden="true" /> {asArray(row.runs).at(-1)?.status || 'not recorded'}
								</span>
							),
						},
					]}
				/>
			</Surface>
		</div>
	);
}
