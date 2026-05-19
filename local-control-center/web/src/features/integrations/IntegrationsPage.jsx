import React from 'react';
import { Boxes, Cable, PlugZap } from 'lucide-react';

import { Badge, DataTable, EmptyState, Surface } from '../../components/primitives.jsx';
import { asArray, formatDate } from '../common/format.js';

export function IntegrationsPage({ data }) {
	const overview = data.overview || {};
	const providers = asArray(overview.providers);
	const ideConnections = asArray(overview.ideConnections);
	const prompts = asArray(overview.promptTemplates);
	const openDesign = overview.openDesign || {};

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Integrations</p>
				<h1 className="editorial-title">Providers, IDE links and prompt inventory without client-side business logic.</h1>
			</div>
			<div className="section-grid" data-motion-item>
				<Surface title="Open Design" kicker="Artifact Lane">
					<div className="timeline">
						<div className="timeline-item">
							<Boxes size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>{openDesign.status || 'not exposed'}</strong>
								<span className="muted">backend {openDesign.backend || 'not reported'}</span>
							</div>
						</div>
					</div>
				</Surface>
				<Surface title="Providers" kicker="Runtime">
					<div className="timeline">
						{providers.map((provider) => (
							<div className="timeline-item" key={provider.id}>
								<Cable size={18} aria-hidden="true" />
								<div className="timeline-content">
									<strong>{provider.label || provider.id}</strong>
									<span className="muted">{provider.kind} · {provider.status}</span>
								</div>
							</div>
						))}
						{!providers.length ? <EmptyState title="No providers" body="Provider catalog is empty." /> : null}
					</div>
				</Surface>
				<Surface title="IDE Connections" kicker="Local Tools">
					<div className="timeline">
						{ideConnections.map((connection) => (
							<div className="timeline-item" key={connection.id}>
								<PlugZap size={18} aria-hidden="true" />
								<div className="timeline-content">
									<strong>{connection.editor}</strong>
									<span className="muted">{connection.status} · {connection.workspaceRoot}</span>
								</div>
							</div>
						))}
						{!ideConnections.length ? <EmptyState title="No IDE connections" body="VS Code or editor connectors can register their workspace state here." /> : null}
					</div>
				</Surface>
			</div>
			<Surface title="Prompt Templates" kicker="Versioned">
				<DataTable
					rows={prompts}
					empty={<EmptyState title="No prompt templates" body="Prompt packs and optimizers will appear as versioned records." />}
					columns={[
						{ key: 'name', label: 'Name' },
						{ key: 'mode', label: 'Mode' },
						{ key: 'version', label: 'Version' },
						{ key: 'optimizer', label: 'Optimizer', render: (row) => row.optimizer || 'manual' },
						{ key: 'updatedAt', label: 'Updated', render: (row) => formatDate(row.updatedAt) },
					]}
				/>
			</Surface>
		</div>
	);
}
