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
