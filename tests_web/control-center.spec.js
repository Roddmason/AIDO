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
	await page.request.post('/api/v1/evidence', {
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
	return workflow;
}

test('shell renders the editorial control plane', async ({ page }) => {
	await page.goto('/');
	await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Jobs/Approvals' })).toBeVisible();
	await expect(page.getByText('Local Control Center')).toBeVisible();
});

test('Jobs/Approvals shows queue, pending actions and buttons', async ({ page }) => {
	await createApprovalJob(page);
	await page.goto('/#jobs');
	await page.getByRole('button', { name: 'Jobs/Approvals' }).click();

	await expect(page.getByRole('heading', { name: 'Jobs/Approvals' })).toBeVisible();
	await expect(page.getByText('pipeline.start').first()).toBeVisible();
	await expect(page.getByRole('button', { name: 'Approve action' }).first()).toBeVisible();
	await expect(page.getByRole('button', { name: 'Deny' }).first()).toBeVisible();
});

test('Memory/Retrieval shows FAISS status, search and reindex', async ({ page }) => {
	await page.goto('/#memory');
	await page.getByRole('button', { name: 'Memory/Retrieval' }).click();

	await expect(page.getByRole('heading', { name: 'Memory/Retrieval' })).toBeVisible();
	await expect(page.getByText('Retrieval Backend')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Reindex' })).toBeVisible();
	await expect(page.getByLabel('Search memory')).toBeVisible();
});

test('mobile layout has no horizontal overflow', async ({ page }) => {
	await page.setViewportSize({ width: 375, height: 812 });
	await page.goto('/');
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
	expect(overflow).toBe(false);
});

test('reduced motion disables GSAP timelines', async ({ browser }) => {
	const context = await browser.newContext({ reducedMotion: 'reduce' });
	const page = await context.newPage();
	await page.goto('/');
	await expect(page.locator('html')).toHaveAttribute('data-motion', 'reduced');
	await expect(page.evaluate(() => window.__lccMotionReduced)).resolves.toBe(true);
	await context.close();
});

test('Workflows shows runs, steps, workspaces and evidence from backend', async ({ page }) => {
	const workflow = await createWorkflowEvidence(page);
	await page.goto('/#workflows');
	await page.getByRole('button', { name: 'Workflows' }).click();

	await expect(page.getByRole('heading', { name: 'Workflows' })).toBeVisible();
	await expect(page.getByRole('cell', { name: workflow.title })).toBeVisible();
	await expect(page.getByText('workspace_create')).toBeVisible();
	await expect(page.getByText('Linked Workspaces')).toBeVisible();
	await expect(page.getByText('Evidence Packages')).toBeVisible();
});

test('Evidence and QA shows persisted test result records', async ({ page }) => {
	await createWorkflowEvidence(page);
	await page.goto('/#evidence');
	await page.getByRole('button', { name: 'Evidence & QA' }).click();

	await expect(page.getByRole('heading', { name: 'Evidence & QA' })).toBeVisible();
	await expect(page.getByText('uv run pytest tests_py -q').first()).toBeVisible();
	await expect(page.getByText('passed').first()).toBeVisible();
});
