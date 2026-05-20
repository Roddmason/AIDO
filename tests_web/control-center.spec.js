import { expect, test } from '@playwright/test';

async function createApprovalJob(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	const projectsResponse = await page.request.get('/api/v1/projects');
	const { projects } = await projectsResponse.json();
	const projectId = projects[0].id;

	await page.request.post('/api/v1/jobs', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			kind: 'pipeline.start',
			payload: {
				command: 'pipeline.start',
				pipelineId: 'pipeline-web-smoke',
				approvalRequired: true,
			},
			idempotencyKey: `web-smoke-${Date.now()}`,
		},
	});
}

async function createWorkflowEvidence(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	const projectsResponse = await page.request.get('/api/v1/projects');
	const { projects } = await projectsResponse.json();
	const projectId = projects[0].id;
	const workflowResponse = await page.request.post('/api/v1/workflows', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			title: `Web workflow ${Date.now()}`,
		},
	});
	const { workflow } = await workflowResponse.json();
	const startedResponse = await page.request.post(`/api/v1/workflows/${workflow.id}/start`, {
		headers: { 'X-Local-Control-Token': token },
		data: { reason: 'web smoke' },
	});
	const started = await startedResponse.json();
	const workspaceStep = started.workflowSteps.find((step) => step.name === 'workspace_create');
	await page.request.post('/api/v1/workspaces', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			taskId: `web-story-${Date.now()}`,
			agentId: 'implementer',
			workflowRunId: started.workflowRun.id,
			workflowStepId: workspaceStep.id,
			isolationType: 'git_worktree',
		},
	});
	const evidenceResponse = await page.request.post('/api/v1/evidence', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			workflowRunId: started.workflowRun.id,
			agentId: 'qa_reviewer',
			taskId: 'web-story-evidence',
			testPlan: 'Run web smoke',
			qaVerdict: 'passed',
			testResults: [{ command: 'uv run pytest tests_py -q', status: 'passed' }],
		},
	});
	const { evidencePackage } = await evidenceResponse.json();
	const artifactName = `web-qa-report-${Date.now()}.md`;
	await page.request.post(`/api/v1/evidence/${evidencePackage.id}/artifacts`, {
		headers: { 'X-Local-Control-Token': token },
		data: {
			kind: 'qa_report',
			name: artifactName,
			content: '# Web QA report\n\nWorkflow inspector artifact smoke.',
			mimeType: 'text/markdown',
		},
	});
	return { ...workflow, workflowRunId: started.workflowRun.id, evidenceId: evidencePackage.id, artifactName };
}

async function createGovernanceState(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	const projectsResponse = await page.request.get('/api/v1/projects');
	const { projects } = await projectsResponse.json();
	const projectId = projects[0].id;
	const suffix = Date.now();
	const riskTitle = `Policy bypass risk ${suffix}`;
	const nextStepTitle = `Tighten approval telemetry ${suffix}`;
	const decisionTitle = `Keep policy decisions in SQLite ${suffix}`;

	const riskResponse = await page.request.post('/api/v1/risks', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			title: riskTitle,
			severity: 'high',
			mitigation: 'Require mitigation before high risk can be accepted.',
			owner: 'technical_lead',
			tags: ['policy', 'audit'],
		},
	});
	const { risk } = await riskResponse.json();
	const stepResponse = await page.request.post('/api/v1/next-steps', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			title: nextStepTitle,
			priority: 'high',
			owner: 'technical_lead',
			sourceRiskId: risk.id,
		},
	});
	const { nextStep } = await stepResponse.json();
	await page.request.post('/api/v1/architecture-decisions', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			title: decisionTitle,
			status: 'accepted',
			context: 'Governance must be queryable by the dashboard.',
			decision: 'Persist architecture decisions, risks and next steps in SQLite.',
			consequences: 'The operational UI can expose engineering governance as state.',
			linkedRiskIds: [risk.id],
			nextStepIds: [nextStep.id],
		},
	});
	return { decisionTitle, riskTitle, nextStepTitle };
}

async function createRuntimeTrace(page, workflowRunId) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	const projectsResponse = await page.request.get('/api/v1/projects');
	const { projects } = await projectsResponse.json();
	const project = projects[0];
	const profileId = `web-cli-${Date.now()}`;

	await page.request.post('/api/v1/agent-profiles', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			id: profileId,
			name: 'Web CLI Runtime',
			role: 'implementer',
			runtimeMode: 'cli',
			modelPolicyId: 'implementation_default',
			permissionProfile: 'dev_safe',
			allowedTools: ['shell'],
		},
	});
	await page.request.post('/api/v1/agent-runs', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId: project.id,
			agentProfileId: profileId,
			taskId: 'web-policy-trace',
			workflowRunId,
			input: {
				toolCalls: [
					{
						tool: 'shell',
						command: 'python --version',
						path: project.path,
						workspacePath: project.path,
					},
				],
			},
		},
	});
}

async function createModelGatewayTrace(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	const projectsResponse = await page.request.get('/api/v1/projects');
	const { projects } = await projectsResponse.json();
	const projectId = projects[0].id;
	const profileId = `web-mock-${Date.now()}`;

	await page.request.post('/api/v1/agent-profiles', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			id: profileId,
			name: 'Web Mock Runtime',
			role: 'implementer',
			runtimeType: 'internal_mock',
			modelPolicyId: 'implementation_default',
			allowedTools: ['policy.evaluate'],
			permissionProfile: 'dev_safe',
		},
	});
	await page.request.post('/api/v1/model-policies', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			id: 'implementation_default',
			name: 'Implementation Default',
			preferred: [{ provider: 'internal_mock', model: 'mock' }],
			fallback: [],
			maxCostUsd: 1,
			maxTokens: 4000,
			allowRemote: false,
			allowLocal: true,
		},
	});
	await page.request.post('/api/v1/agent-runs', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId,
			agentProfileId: profileId,
			taskId: 'web-model-trace',
			input: { goal: 'record model trace for UI' },
		},
	});
}

test('shell renders the editorial control plane', async ({ page }) => {
	await page.goto('/');
	await expect(page.getByRole('heading', { name: 'AIDO control plane' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Jobs & Approvals' })).toBeVisible();
	await expect(page.getByText('AIDO Control Center')).toBeVisible();
});

test('Jobs/Approvals shows queue, pending actions and buttons', async ({ page }) => {
	await createApprovalJob(page);
	await page.goto('/#jobs');
	await page.getByRole('button', { name: 'Jobs & Approvals' }).click();

	await expect(page.getByRole('heading', { name: 'Jobs & Approvals' })).toBeVisible();
	await expect(page.getByText('pipeline.start').first()).toBeVisible();
	await expect(page.getByRole('button', { name: 'Approve action' }).first()).toBeVisible();
	await expect(page.getByRole('button', { name: 'Deny' }).first()).toBeVisible();
	await page.getByRole('button', { name: 'Open approvals drawer' }).click();
	await expect(page.getByRole('dialog', { name: 'Approval drawer' })).toBeVisible();
	await expect(page.getByText('pipeline.start').first()).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Approval drawer' })).toBeHidden();
});

test('Event drawer exposes recent operational events', async ({ page }) => {
	await createApprovalJob(page);
	await page.goto('/');

	await page.getByRole('button', { name: 'Open event drawer' }).click();
	await expect(page.getByRole('dialog', { name: 'Event drawer' })).toBeVisible();
	await expect(page.getByText('job.created').first()).toBeVisible();
});

test('command palette opens searchable event drawer', async ({ page }) => {
	await createApprovalJob(page);
	await page.goto('/');

	await page.getByRole('button', { name: 'Open command palette' }).click();
	await page.getByLabel('Command palette filter').fill('search events');
	await page.getByRole('button', { name: 'Search Events' }).click();

	await expect(page.getByRole('dialog', { name: 'Event drawer' })).toBeVisible();
	await page.getByLabel('Event filter').fill('job.created');
	await expect(page.getByText('job.created').first()).toBeVisible();
	await page.getByLabel('Event filter').fill('no-such-event-type');
	await expect(page.getByText('No events')).toBeVisible();
});

test('keyboard shortcuts open operational surfaces without mouse navigation', async ({ page }) => {
	await createApprovalJob(page);
	await page.goto('/');

	await page.keyboard.press('Control+Alt+A');
	await expect(page.getByRole('dialog', { name: 'Approval drawer' })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Approval drawer' })).toBeHidden();

	await page.keyboard.press('Control+Alt+E');
	await expect(page.getByRole('dialog', { name: 'Event drawer' })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Event drawer' })).toBeHidden();

	await page.keyboard.press('Control+Alt+W');
	await expect(page.getByRole('heading', { name: 'Workflows' })).toBeVisible();
});

test('Memory & Retrieval shows backend status and memory records', async ({ page }) => {
	await page.goto('/#memory');
	await page.getByRole('button', { name: 'Memory & Retrieval' }).click();

	await expect(page.getByRole('heading', { name: 'Memory & Retrieval' })).toBeVisible();
	await expect(page.getByText('Retrieval Backend')).toBeVisible();
	await expect(page.getByText('SQLite is canonical')).toBeVisible();
	await expect(page.getByText('Memory items')).toBeVisible();
});

test('mobile layout has no horizontal overflow', async ({ page }) => {
	await page.setViewportSize({ width: 375, height: 812 });
	await page.goto('/');
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
	expect(overflow).toBe(false);
});

test('reduced motion disables non-essential motion', async ({ browser }) => {
	const context = await browser.newContext({ reducedMotion: 'reduce' });
	const page = await context.newPage();
	await page.goto('/');
	await expect(page.locator('html')).toHaveAttribute('data-motion', 'reduced');
	await expect(page.evaluate(() => window.__aidoMotionReduced)).resolves.toBe(true);
	await context.close();
});

test('Workflows shows runs, steps, workspaces and evidence from backend', async ({ page }) => {
	const workflow = await createWorkflowEvidence(page);
	await page.goto('/#workflows');
	await page.getByRole('button', { name: 'Workflows' }).click();

	await expect(page.getByRole('heading', { name: 'Workflows' })).toBeVisible();
	await expect(page.getByRole('cell', { name: workflow.title, exact: true })).toBeVisible();
	await expect(page.getByText('workspace_create').first()).toBeVisible();
	await expect(page.getByText('Workflow graph')).toBeVisible();
});

test('Evidence and QA shows persisted test result records', async ({ page }) => {
	await createWorkflowEvidence(page);
	await page.goto('/#evidence');
	await page.getByRole('button', { name: 'Evidence & QA' }).click();

	await expect(page.getByRole('heading', { name: 'Evidence & QA' })).toBeVisible();
	await expect(page.getByText('uv run pytest tests_py -q').first()).toBeVisible();
	await expect(page.getByText('passed').first()).toBeVisible();
});

test('Evidence and QA previews token-protected artifacts', async ({ page }) => {
	const workflow = await createWorkflowEvidence(page);
	await page.goto('/#evidence');
	await page.getByRole('button', { name: 'Evidence & QA' }).click();

	await expect(page.getByText(workflow.artifactName).first()).toBeVisible();
	await page.getByRole('button', { name: `Preview artifact ${workflow.artifactName}` }).click();
	await expect(page.getByRole('dialog', { name: 'Artifact preview' })).toBeVisible();
	await expect(page.getByText('Workflow inspector artifact smoke.')).toBeVisible();
	await expect(page.getByText('text/markdown')).toBeVisible();
	await expect(page.getByRole('button', { name: `Download artifact ${workflow.artifactName}` })).toBeVisible();
});

test('Governance shows architecture decisions, risks and next steps', async ({ page }) => {
	const governance = await createGovernanceState(page);
	await page.goto('/#governance');
	await page.getByRole('button', { name: 'Governance' }).click();

	await expect(page.getByRole('heading', { name: 'Governance', exact: true })).toBeVisible();
	await expect(page.getByText(governance.decisionTitle)).toBeVisible();
	await expect(page.getByRole('cell', { name: governance.riskTitle })).toBeVisible();
	await expect(page.getByText(governance.nextStepTitle)).toBeVisible();
});

test('Governance filters records and updates risk status through strict controls', async ({ page }) => {
	const governance = await createGovernanceState(page);
	await page.goto('/#governance');
	await page.getByRole('button', { name: 'Governance' }).click();

	await page.getByLabel('Governance filter').fill(governance.riskTitle);
	await expect(page.getByRole('cell', { name: governance.riskTitle })).toBeVisible();
	await page.getByLabel('Risk status filter').selectOption('open');
	await expect(page.getByRole('cell', { name: governance.riskTitle })).toBeVisible();

	await page.getByLabel('Risk to update').selectOption({ label: governance.riskTitle });
	await page.getByLabel('Risk update status').selectOption('mitigating');
	await page.getByRole('button', { name: 'Update risk status' }).click();
	await page.getByLabel('Risk status filter').selectOption('mitigating');
	await expect(page.getByRole('cell', { name: governance.riskTitle })).toBeVisible();
	await expect(page.getByRole('cell', { name: 'mitigating' }).first()).toBeVisible();
});

test('Policy & Security exposes tool-call execution state', async ({ page }) => {
	await createRuntimeTrace(page);
	await page.goto('/#policy');
	await page.getByRole('button', { name: 'Policy & Security' }).click();

	await expect(page.getByRole('heading', { name: 'Policy & Security' })).toBeVisible();
	await expect(page.getByText('Tool-call execution')).toBeVisible();
	await expect(page.getByText('python --version').first()).toBeVisible();
	await expect(page.getByText('not_executed').first()).toBeVisible();
});

test('Policy & Security shows sandbox policy revision diffs', async ({ page }) => {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	await page.request.patch('/api/v1/sandbox/profiles/default_docker', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			reason: 'Set baseline before visual diff smoke.',
			allowedImages: ['python:3.12-slim'],
			allowedNetworks: ['none'],
			defaultNetwork: 'none',
			memory: '1g',
			cpus: '1',
			timeoutSeconds: 120,
			status: 'active',
		},
	});

	await page.goto('/#policy');
	await page.getByRole('button', { name: 'Policy & Security' }).click();
	await page.getByLabel('Sandbox update reason').fill('Create visual policy diff smoke.');
	await page.getByLabel('Sandbox memory limit').fill('768m');
	await page.getByLabel('Sandbox CPU limit').fill('1');
	await page.getByLabel('Sandbox timeout seconds').fill('90');
	await page.getByLabel('Sandbox allowed image').fill('python:3.12-slim');
	await page.getByRole('button', { name: 'Save sandbox profile' }).click();

	await expect(page.getByText('Policy revisions')).toBeVisible();
	const revisionRow = page.getByRole('row', { name: /default_docker v\d+ memory, timeoutSeconds/ }).first();
	await expect(revisionRow).toBeVisible();
	await revisionRow.getByRole('button', { name: 'View policy revision diff for default_docker' }).click();
	const diffDialog = page.getByRole('dialog', { name: 'Policy revision diff' });
	await expect(diffDialog).toBeVisible();
	await expect(diffDialog.getByRole('cell', { name: 'memory' })).toBeVisible();
	await expect(diffDialog.getByRole('cell', { name: '"1g"' })).toBeVisible();
	await expect(diffDialog.getByRole('cell', { name: '"768m"' })).toBeVisible();
	await expect(diffDialog.getByRole('cell', { name: 'timeoutSeconds' })).toBeVisible();
	await expect(diffDialog.getByRole('cell', { name: '120' })).toBeVisible();
	await expect(diffDialog.getByRole('cell', { name: '90' })).toBeVisible();
});

test('Model Gateway exposes model calls and cost ledger', async ({ page }) => {
	await createModelGatewayTrace(page);
	await page.goto('/#models');
	await page.getByRole('button', { name: 'Model Gateway' }).click();

	await expect(page.getByRole('heading', { name: 'Model Gateway' })).toBeVisible();
	await expect(page.getByText('Model calls')).toBeVisible();
	await expect(page.getByText('Cost ledger')).toBeVisible();
	await expect(page.getByText('internal_mock').first()).toBeVisible();
});

test('Model Gateway console renders provider catalog routing usage budgets and CLI sessions', async ({ page }) => {
	await page.goto('/#models');
	await page.getByRole('button', { name: 'Model Gateway' }).click();

	await expect(page.getByRole('heading', { name: 'Model Gateway' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Provider Accounts' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Model Catalog' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Routing Profiles' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Role Assignments' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Usage Ledger' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Budgets' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Provider Limits' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Routing Decisions' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'CLI Sessions' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Benchmarks' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
	await expect(page.getByText('No usage ledger entries')).toBeVisible();
	await expect(page.getByText('No CLI sessions')).toBeVisible();
});

test('Model Gateway route preview submits mock request without exposing credentials', async ({ page }) => {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	await page.request.patch('/api/v1/model-gateway/providers/nvidia_nim', {
		headers: { 'X-Local-Control-Token': token },
		data: { enabled: true, healthStatus: 'healthy', lastError: 'Authorization: Bearer sk-websecret123456' },
	});
	await page.goto('/#models');
	await page.getByRole('button', { name: 'Model Gateway' }).click();

	await expect(page.locator('body')).not.toContainText('sk-websecret');
	await expect(page.getByText('NVIDIA_NIM_API_KEY').first()).toBeVisible();
	await page.getByLabel('Preview role').selectOption('analyst');
	await page.getByLabel('Preview mode').selectOption('free_first');
	await page.getByLabel('Preview task type').fill('research_brief');
	await page.getByLabel('Preview context tokens').fill('4000');
	await page.getByLabel('Preview requires search').check();
	await page.getByRole('button', { name: 'Preview route' }).click();

	await expect(page.getByText('Selected route')).toBeVisible();
	await expect(page.getByText('nvidia_nim').first()).toBeVisible();
	await expect(page.locator('body')).not.toContainText('sk-');
});

test('strict configuration forms prevent manual JSON edits', async ({ page }) => {
	await page.goto('/#agents');
	await page.getByRole('button', { name: 'Agents' }).click();

	await expect(page.getByLabel('Profile id')).toBeVisible();
	await expect(page.locator('textarea')).toHaveCount(0);
	await page.getByLabel('Profile id').fill('Bad Profile!');
	await page.getByRole('button', { name: 'Save agent profile' }).click();
	await expect(page.getByText('Use lowercase letters, numbers, dashes or underscores.')).toBeVisible();

	const suffix = Date.now();
	const profileId = `web_form_${suffix}`;
	const profileName = `Web Form Agent ${suffix}`;
	await page.getByLabel('Profile id').fill(profileId);
	await page.getByLabel('Display name').fill(profileName);
	await page.getByLabel('Role', { exact: true }).selectOption('developer');
	await page.getByLabel('Routing profile').selectOption('balanced_best_value');
	await page.getByLabel('Role model policy').selectOption('developer');
	await page.getByLabel('Runtime mode').selectOption('internal_mock');
	await page.getByLabel('Permission profile').selectOption('dev_safe');
	await page.getByLabel('Allowed tool').selectOption('shell');
	await page.getByLabel('Allowed provider').selectOption('codex_cli');
	await page.getByLabel('Allowed runtime').selectOption('cli');
	await page.getByLabel('Max tokens per run').fill('120000');
	await page.getByLabel('Approval threshold USD').fill('1.25');
	await page.getByLabel('Allow API').uncheck();
	await page.getByRole('button', { name: 'Save agent profile' }).click();
	await expect(page.getByRole('cell', { name: profileName })).toBeVisible();
	const profilesResponse = await page.request.get('/api/v1/agent-profiles');
	const profile = (await profilesResponse.json()).agentProfiles.find((item) => item.id === profileId);
	expect(profile.routingProfileId).toBe('balanced_best_value');
	expect(profile.roleModelPolicyId).toBe('developer');
	expect(profile.allowedProviders).toEqual(['codex_cli']);
	expect(profile.allowedRuntimes).toEqual(['cli']);
	expect(profile.maxTokensPerRun).toBe(120000);
	expect(profile.allowApi).toBe(false);
	expect(profile.requiresApprovalOverUsd).toBe(1.25);

	await page.getByRole('button', { name: 'Model Gateway' }).click();
	await expect(page.getByLabel('Policy id')).toBeVisible();
	await expect(page.locator('textarea')).toHaveCount(0);
	await page.getByLabel('Policy id').fill('Bad Policy!');
	await page.getByRole('button', { name: 'Save model policy' }).click();
	await expect(page.getByText('Policy id must use lowercase letters, numbers, dashes or underscores.')).toBeVisible();

	const policyId = `web_policy_${suffix}`;
	await page.getByLabel('Policy id').fill(policyId);
	await page.getByLabel('Policy name').fill('Web Policy');
	await page.getByLabel('Preferred provider').selectOption('internal_mock');
	await page.getByLabel('Model').selectOption('mock');
	await page.getByLabel('Maximum cost USD').fill('1');
	await page.getByLabel('Maximum tokens').fill('4000');
	await page.getByRole('button', { name: 'Save model policy' }).click();
	await expect(page.getByRole('cell', { name: policyId })).toBeVisible();
});

test('strict operational forms cover workflows governance sandbox and MCP settings', async ({ page }) => {
	await page.goto('/#command');
	await page.getByRole('button', { name: 'Command Center' }).click();

	await expect(page.getByLabel('Workflow title')).toBeVisible();
	await expect(page.locator('textarea')).toHaveCount(0);
	await page.getByLabel('Workflow title').fill('');
	await page.getByRole('button', { name: 'Create workflow' }).click();
	await expect(page.getByText('Workflow title is required.')).toBeVisible();

	const suffix = Date.now();
	const workflowTitle = `Strict workflow ${suffix}`;
	await page.getByLabel('Workflow title').fill(workflowTitle);
	await page.getByLabel('Workflow kind').selectOption('idea_to_pr');
	await page.getByRole('button', { name: 'Create workflow' }).click();
	await expect(page.getByText(workflowTitle)).toBeVisible();

	await page.getByRole('button', { name: 'Governance' }).click();
	await expect(page.getByLabel('Risk title')).toBeVisible();
	await page.getByLabel('Risk title').fill(`High risk ${suffix}`);
	await page.getByLabel('Risk severity').selectOption('high');
	await page.getByRole('button', { name: 'Save risk' }).click();
	await expect(page.getByText('High and critical risks require mitigation.')).toBeVisible();
	await page.getByLabel('Risk mitigation').fill('Track owner, date and validation evidence before closing.');
	await page.getByRole('button', { name: 'Save risk' }).click();
	await expect(page.getByRole('cell', { name: `High risk ${suffix}` })).toBeVisible();

	await page.getByLabel('Decision title').fill(`Decision ${suffix}`);
	await page.getByLabel('Decision status').selectOption('accepted');
	await page.getByRole('button', { name: 'Save decision' }).click();
	await expect(page.getByText('Accepted decisions require context and decision text.')).toBeVisible();
	await page.getByLabel('Decision context').fill('Configuration must be edited through controlled forms.');
	await page.getByLabel('Decision text').fill('Keep JSON out of operator workflows.');
	await page.getByRole('button', { name: 'Save decision' }).click();
	await expect(page.getByRole('cell', { name: `Decision ${suffix}` })).toBeVisible();

	await page.getByLabel('Next step title').fill(`Next step ${suffix}`);
	await page.getByLabel('Next step priority').selectOption('urgent');
	await page.getByRole('button', { name: 'Save next step' }).click();
	await expect(page.getByRole('cell', { name: `Next step ${suffix}` })).toBeVisible();

	await page.getByRole('button', { name: 'Policy & Security' }).click();
	await page.getByLabel('Sandbox update reason').fill('');
	await page.getByRole('button', { name: 'Save sandbox profile' }).click();
	await expect(page.getByText('Sandbox update reason is required.')).toBeVisible();
	await page.getByLabel('Sandbox update reason').fill('Constrain post-MVP smoke runtime resources.');
	await page.getByLabel('Sandbox memory limit').fill('768m');
	await page.getByLabel('Sandbox CPU limit').fill('1');
	await page.getByLabel('Sandbox timeout seconds').fill('120');
	await page.getByLabel('Sandbox allowed image').fill('python:3.12-slim');
	await page.getByRole('button', { name: 'Save sandbox profile' }).click();
	await expect(page.getByText('768m').first()).toBeVisible();

	await page.getByRole('button', { name: 'Integrations' }).click();
	await expect(page.getByLabel('MCP server id')).toBeVisible();
	await page.getByLabel('MCP server id').fill('Bad Server!');
	await page.getByRole('button', { name: 'Register MCP server' }).click();
	await expect(page.getByText('MCP server id must use lowercase letters, numbers, dashes or underscores.')).toBeVisible();
	const mcpId = `mcp_form_${suffix}`;
	await page.getByLabel('MCP server id').fill(mcpId);
	await page.getByLabel('MCP command').fill('python -m local_mcp_server');
	await page.getByLabel('MCP transport').selectOption('stdio');
	await page.getByRole('button', { name: 'Register MCP server' }).click();
	await expect(page.getByRole('cell', { name: mcpId })).toBeVisible();
});

test('command palette executes v1 actions and workflow inspector shows linked records', async ({ page }) => {
	const workflow = await createWorkflowEvidence(page);
	await createRuntimeTrace(page, workflow.workflowRunId);
	await createApprovalJob(page);
	await page.goto('/');

	await page.getByRole('button', { name: 'Open command palette' }).click();
	await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible();
	await page.getByLabel('Command palette filter').fill('workflow');
	await page.getByRole('button', { name: 'Go to Workflows' }).click();
	await expect(page.getByRole('heading', { name: 'Workflows' })).toBeVisible();

	await page.getByRole('button', { name: `Inspect workflow ${workflow.title}` }).click();
	await expect(page.getByRole('dialog', { name: 'Workflow inspector' })).toBeVisible();
	await expect(page.getByText(workflow.title).first()).toBeVisible();
	await expect(page.getByText('workspace_create').first()).toBeVisible();
	await expect(page.getByText('web-story-evidence').first()).toBeVisible();
	await expect(page.getByText('python --version').first()).toBeVisible();
	await expect(page.getByText('Policy decisions')).toBeVisible();
	await expect(page.getByText('allowlisted_diagnostic').first()).toBeVisible();
	await expect(page.getByText('Artifacts')).toBeVisible();
	await expect(page.getByText(workflow.artifactName).first()).toBeVisible();
	await page.getByRole('button', { name: `Preview workflow artifact ${workflow.artifactName}` }).click();
	await expect(page.getByRole('dialog', { name: 'Workflow artifact preview' })).toBeVisible();
	await expect(page.getByText('Workflow inspector artifact smoke.')).toBeVisible();
	await expect(page.getByRole('button', { name: `Download workflow artifact ${workflow.artifactName}` })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Workflow artifact preview' })).toBeHidden();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Workflow inspector' })).toBeHidden();

	await page.getByRole('button', { name: 'Open command palette' }).click();
	await page.getByLabel('Command palette filter').fill('approval');
	await page.getByRole('button', { name: 'Open Pending Approvals' }).click();
	await expect(page.getByRole('dialog', { name: 'Approval drawer' })).toBeVisible();
});

test('command palette can create a workflow through typed v1 mutation', async ({ page }) => {
	await page.goto('/');

	await page.getByRole('button', { name: 'Open command palette' }).click();
	await page.getByLabel('Command palette filter').fill('create workflow');
	await page.getByRole('button', { name: 'Create Workflow' }).click();

	await expect(page.getByRole('heading', { name: 'Workflows' })).toBeVisible();
	await expect(page.getByText('Palette workflow').first()).toBeVisible();
});
