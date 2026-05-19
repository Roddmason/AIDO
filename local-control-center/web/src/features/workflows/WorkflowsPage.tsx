import { Background, Controls, ReactFlow, type Edge, type Node } from '@xyflow/react';

import type { Overview, WorkflowStep } from '../../api/types';
import { Badge, DataTable, EmptyState, PageHeader, Surface } from '../../components/primitives';
import { toneForStatus } from '../../lib/format';

function nodesFromSteps(steps: WorkflowStep[]): Node[] {
	return steps.slice(0, 12).map((step, index) => ({
		id: step.id,
		position: { x: (index % 4) * 210, y: Math.floor(index / 4) * 120 },
		data: { label: `${step.name}\n${step.status}` },
		style: {
			border: '1px solid var(--line-strong)',
			borderRadius: '8px',
			background: 'var(--surface-panel-strong)',
			color: 'var(--ink)',
			fontFamily: 'var(--font-data)',
			whiteSpace: 'pre-line',
		},
	}));
}

function edgesFromNodes(nodes: Node[]): Edge[] {
	return nodes.slice(1).map((node, index) => ({
		id: `edge-${nodes[index].id}-${node.id}`,
		source: nodes[index].id,
		target: node.id,
		animated: true,
	}));
}

export function WorkflowsPage({ overview }: { overview: Overview }) {
	const nodes = nodesFromSteps(overview.workflowSteps);
	const edges = edgesFromNodes(nodes);
	return (
		<>
			<PageHeader
				kicker="SDLC graph"
				title="Workflows"
				summary="Durable workflow runs with steps, evidence and permission checkpoints represented as operational state."
			/>
			<Surface title="Workflow graph">
				<div className="flow-board" aria-label="Workflow graph">
					<ReactFlow nodes={nodes} edges={edges} fitView>
						<Background />
						<Controls />
					</ReactFlow>
				</div>
			</Surface>
			<div className="grid two">
				<Surface title="Runs">
					<DataTable
						rows={overview.workflows}
						empty={<EmptyState title="No workflows" body="Command Center can create a workflow when a project is selected." />}
						columns={[
							{ key: 'title', label: 'Title', render: (row) => row.title },
							{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
						]}
					/>
				</Surface>
				<Surface title="Steps">
					<DataTable
						rows={overview.workflowSteps}
						empty={<EmptyState title="No steps" body="Starting a workflow expands the SDLC step plan." />}
						columns={[
							{ key: 'name', label: 'Step', render: (row) => <span className="mono">{row.name}</span> },
							{ key: 'status', label: 'Status', render: (row) => <Badge tone={toneForStatus(row.status)}>{row.status}</Badge> },
							{ key: 'agent', label: 'Agent', render: (row) => row.agentProfileId ?? 'unassigned' },
						]}
					/>
				</Surface>
			</div>
		</>
	);
}
