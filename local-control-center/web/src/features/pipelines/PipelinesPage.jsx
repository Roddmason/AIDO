import React from 'react';
import { GitBranch, Workflow } from 'lucide-react';

import { Badge, DataTable, EmptyState, Surface, Timeline } from '../../components/primitives.jsx';
import { asArray, formatDate, shortId, toneForStatus } from '../common/format.js';

function flattenStages(pipeline) {
	return asArray(pipeline.modules).flatMap((module) =>
		Object.values(module.stages || {}).map((stage) => ({
			...stage,
			moduleName: module.name,
			pipelineId: pipeline.id,
			pipelineName: pipeline.name,
		})),
	);
}

export function PipelinesPage({ data }) {
	const workspaceState = data.overview?.workspaceState || {};
	const pipelines = asArray(workspaceState.pipelines);
	const activePipelineId = workspaceState.activePipelineId;
	const activePipeline = pipelines.find((pipeline) => pipeline.id === activePipelineId) || pipelines[0];
	const stages = activePipeline ? flattenStages(activePipeline) : [];
	const corrections = asArray(activePipeline?.modules?.[0]?.corrections);

	return (
		<div className="page-stack" data-motion="section">
			<div data-motion-item>
				<p className="eyebrow">Pipelines</p>
				<h1 className="editorial-title">Operational flow, stage gates and correction branches.</h1>
			</div>
			<div className="split-pane" data-motion-item>
				<Surface title="Pipeline Board" kicker="Active Flow">
					<DataTable
						rows={pipelines}
						empty={<EmptyState title="No pipelines" body="Idea intake and pipeline jobs will populate this board through Python-compatible routes." />}
						columns={[
							{ key: 'status', label: 'Status', render: (row) => <Badge status={row.status} tone={toneForStatus(row.status)}>{row.status}</Badge> },
							{ key: 'ticketId', label: 'Ticket', render: (row) => <code>{row.ticketId || shortId(row.id)}</code> },
							{ key: 'name', label: 'Name' },
							{ key: 'sessionId', label: 'Session', render: (row) => <code>{shortId(row.sessionId)}</code> },
							{ key: 'updatedAt', label: 'Updated', render: (row) => formatDate(row.updatedAt) },
						]}
					/>
				</Surface>
				<Surface title="Timeline" kicker={activePipeline?.ticketId || 'No active pipeline'}>
					{activePipeline ? (
						<Timeline
							items={stages.map((stage) => ({
								id: stage.id,
								type: stage.stage,
								status: stage.status,
								body: `${stage.moduleName} · gate ${stage.gate || 'pending'}`,
								createdAt: formatDate(stage.updatedAt || activePipeline.updatedAt),
							}))}
						/>
					) : (
						<EmptyState title="No active timeline" body="Select or create a pipeline to inspect stage state." />
					)}
				</Surface>
			</div>
			<div className="section-grid" data-motion-item>
				<Surface title="Stage Focus" kicker="First incomplete">
					{stages.length ? (
						<div className="timeline">
							{stages.slice(0, 8).map((stage) => (
								<div className="timeline-item" key={stage.id}>
									<Workflow size={18} aria-hidden="true" />
									<div className="timeline-content">
										<strong>{stage.stage}</strong>
										<span className="muted">{stage.status} · gate {stage.gate || 'pending'}</span>
									</div>
								</div>
							))}
						</div>
					) : (
						<EmptyState title="No stages" body="Pipeline stage records are not available yet." />
					)}
				</Surface>
				<Surface title="Correction Branches" kicker="Recovery">
					{corrections.length ? (
						<Timeline
							items={corrections.map((correction) => ({
								id: correction.id,
								type: correction.stage || 'correction',
								status: correction.status || 'queued',
								body: correction.summary || correction.reason || 'correction branch',
								createdAt: formatDate(correction.createdAt),
							}))}
						/>
					) : (
						<EmptyState title="No correction branches" body="Retry and correction flows will appear here when persisted." />
					)}
				</Surface>
				<Surface title="Transcript Links" kicker="Chat/Prompt Packs">
					<div className="timeline">
						<div className="timeline-item">
							<GitBranch size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>Chat</strong>
								<span className="muted mono">{shortId(activePipeline?.chatId)}</span>
							</div>
						</div>
						<div className="timeline-item">
							<GitBranch size={18} aria-hidden="true" />
							<div className="timeline-content">
								<strong>Prompt pack</strong>
								<span className="muted mono">{shortId(activePipeline?.promptPackId)}</span>
							</div>
						</div>
					</div>
				</Surface>
			</div>
		</div>
	);
}
