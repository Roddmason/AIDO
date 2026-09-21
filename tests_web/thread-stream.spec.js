import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 1280, height: 800 } });

test('Threads: an idle event stream recovers after quiet polls and a transient error', async ({ page }) => {
	// Exercises the real 15-second idle/retry interval without altering the app's other timers.
	test.setTimeout(90_000);
	const { projects } = await (await page.request.get('/api/v1/projects')).json();
	const project = projects.find((entry) => entry.status === 'active');
	expect(project).toBeTruthy();
	const thread = {
		id: 'thread-stream-recovery-fixture', projectId: project.id, ownerId: project.id,
		ownerType: 'workspace', title: 'Idle stream recovery fixture', summary: '',
		status: 'blocked', metadata: {}, createdAt: '2026-09-20T00:00:00Z', updatedAt: '2026-09-20T00:00:00Z',
	};
	const queries = [];
	await page.route('**/api/v1/overview', async (route) => {
		const response = await route.fetch();
		const overview = await response.json();
		await route.fulfill({ response, json: { ...overview, threads: [...overview.threads, thread] } });
	});
	await page.route(`**/api/v1/threads/${thread.id}`, (route) => route.fulfill({ json: {
		thread: { ...thread, status: queries.length >= 8 ? 'running' : 'blocked' },
		messages: [], decisions: [], artifacts: [], events: [],
	} }));
	await page.route(`**/api/v1/threads/${thread.id}/remediations`, (route) =>
		route.fulfill({ json: { remediations: [] } }),
	);
	await page.route(`**/api/v1/threads/${thread.id}/events?*`, async (route) => {
		const afterSeq = Number(new URL(route.request().url()).searchParams.get('afterSeq'));
		queries.push(afterSeq);
		if (queries.length === 7) {
			await route.fulfill({ status: 503, json: { detail: 'thread_events_temporarily_unavailable' } });
			return;
		}
		const recovered = queries.length >= 8;
		const events = recovered && afterSeq === 0 ? [{
			id: 'stream-recovered-event', threadId: thread.id, projectId: project.id,
			sequence: 1, type: 'executing', payload: {}, metadata: {}, createdAt: thread.createdAt,
		}] : [];
		await route.fulfill({ json: {
			events, lastSeq: recovered ? 1 : 0, running: recovered,
			threadStatus: recovered ? 'running' : 'blocked',
		} });
	});
	try {
		await page.goto('/#threads');
		await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
		await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
		const threadButton = page.getByRole('button', { name: thread.title, exact: true });
		if (!(await threadButton.isVisible())) {
			await page.locator('.thread-workspace-head').filter({ hasText: project.name }).click();
		}
		await threadButton.click();
		await expect.poll(() => queries.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(6);
		const execution = page.getByRole('complementary', { name: 'Execution console' });
		const error = execution.locator('.thread-console-status[data-tone="danger"]');
		await expect(error).toContainText('thread_events_temporarily_unavailable', { timeout: 20_000 });
		await expect(execution.locator('.thread-pipeline-step', { hasText: 'Executing' })).toHaveAttribute(
			'data-state', 'active', { timeout: 20_000 },
		);
		await expect(error).toHaveCount(0);
		await expect.poll(() => queries.length, { timeout: 5_000 }).toBeGreaterThanOrEqual(9);
		expect(queries[8]).toBe(1);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});
