import React from 'react';
import { Bot, BrainCircuit, Cable } from 'lucide-react';

import { Badge, DataTable, EmptyState, Surface } from '../../components/primitives.jsx';
import { asArray, formatDate } from '../common/format.js';

export function AgentsRuntimePage({ data }) {
	const overview = data.overview || {};
	const providers = asArray(overview.providers);
	const agents = asArray(overview.agents);
	const agentRuns = asArray(overview.agentRuns);

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Agents Runtime</p>
				<h1 className="editorial-title">Planner state is visible; execution remains policy gated.</h1>
			</div>
			<div className="section-grid" data-motion-item>
				<Surface title="Providers" kicker="Models">
					<div className="timeline">
						{providers.map((provider) => (
							<div className="timeline-item" key={provider.id}>
								<Cable size={18} aria-hidden="true" />
								<div className="timeline-content">
									<strong>{provider.label || provider.id}</strong>
									<span className="muted">{provider.status || 'unknown'} · {asArray(provider.models).length} models</span>
								</div>
							</div>
						))}
						{!providers.length ? <EmptyState title="No providers" body="Provider records will appear when runtime configuration is available." /> : null}
					</div>
				</Surface>
				<Surface title="Planner Gate" kicker="OpenAI Agents SDK">
					<div className="timeline">
						<div className="timeline-item">
							<BrainCircuit size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>{overview.agentRuntime?.status || 'not exposed by API yet'}</strong>
								<span className="muted">The UI will render agent runs/tool calls when the backend exposes them.</span>
							</div>
						</div>
						<div className="timeline-item">
							<Bot size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>{agents.length} configured agents</strong>
								<span className="muted">System prompts and permissions stay server-owned.</span>
							</div>
						</div>
					</div>
				</Surface>
			</div>
			<Surface title="Agents" kicker="Team Runtime">
				<DataTable
					rows={agents}
					empty={<EmptyState title="No agents" body="Specialist agents imported or created in SQLite will appear here." />}
					columns={[
						{ key: 'name', label: 'Name' },
						{ key: 'role', label: 'Role' },
						{ key: 'kind', label: 'Kind' },
						{ key: 'providerId', label: 'Provider' },
						{ key: 'model', label: 'Model' },
						{ key: 'capabilities', label: 'Capabilities', render: (row) => asArray(row.capabilities).join(', ') || 'not recorded' },
					]}
				/>
			</Surface>
			<Surface title="Agent Runs and Tool Calls" kicker="Read-only">
				{agentRuns.length ? (
					<DataTable
						rows={agentRuns}
						columns={[
							{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status}>{row.status}</Badge> },
							{ key: 'agentId', label: 'Agent' },
							{ key: 'summary', label: 'Summary' },
							{ key: 'createdAt', label: 'Created', render: (row) => formatDate(row.createdAt) },
						]}
					/>
				) : (
					<EmptyState title="No agent run endpoint yet" body="This is intentionally empty instead of fabricated. Add a read-only API when the runtime persists agent runs/tool calls." />
				)}
			</Surface>
		</div>
	);
}
