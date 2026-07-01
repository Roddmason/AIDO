import { expect, test } from '@playwright/test';

async function expectControlPlaneLoaded(page) {
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
}

async function getActiveProject(page) {
	const projectsResponse = await page.request.get('/api/v1/projects');
	const { projects } = await projectsResponse.json();
	const project = projects.find((item) => item.status === 'active');
	expect(project).toBeTruthy();
	return project;
}

async function getWriteToken(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	return token;
}

test('Threads: create a real thread, select it immediately, and show queued console events', async ({
	page,
}) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	// Select the active workspace, then open the new-thread composer in the center.
	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();

	// The title is auto-derived from the first line of the message (no manual title field).
	const firstMessage = `Add a new dashboard endpoint to list active workspaces ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(firstMessage);
	await page.getByRole('button', { name: 'Create thread' }).click();

	// The thread is selected before the Product Loop job runs; dashboard-only test servers do not run
	// the worker, so the honest state is queued with a visible console event.
	await expect(page.getByText(firstMessage).first()).toBeVisible({ timeout: 20_000 });
	await expect(page.getByText(/Run queued|Run encolado/).first()).toBeVisible({ timeout: 20_000 });
	await expect(page.getByText(/waiting for a worker|esperando que un worker/).first()).toBeVisible({
		timeout: 20_000,
	});

	// The coordinator left an intake artifact on the thread (verified via the API).
	const project = await getActiveProject(page);
	const listed = await page.request.get(`/api/v1/threads?projectId=${project.id}`);
	const { threads } = await listed.json();
	const created = threads.find((thread) => thread.title === firstMessage.slice(0, 80));
	expect(created).toBeTruthy();
	const detailResponse = await page.request.get(`/api/v1/threads/${created.id}`);
	const detail = await detailResponse.json();
	expect(detail.artifacts.some((artifact) => artifact.kind === 'intake_classification')).toBe(true);
	expect(detail.messages.some((message) => message.kind === 'user')).toBe(true);
	expect(detail.events.some((event) => event.type === 'run_queued')).toBe(true);
	expect(detail.thread.status).toBe('queued');
});

test('Threads: an ambiguous message makes the coordinator block with a decision request', async ({
	page,
}) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();

	await page.getByLabel('Message AIDO').fill('help');
	await page.getByRole('button', { name: 'Create thread' }).click();

	// A blocked coordinator surfaces a decision request with actionable options.
	await expect(page.locator('.thread-decision-console').first()).toBeVisible({ timeout: 20_000 });
});

test('Threads: an existing blocked thread can send another message and refresh console events', async ({
	page,
}) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();

	await page.getByLabel('Message AIDO').fill('help');
	await page.getByRole('button', { name: 'Create thread' }).click();
	await expect(page.locator('.thread-decision-console').first()).toBeVisible({ timeout: 20_000 });
	await page.waitForTimeout(6500);

	const secondMessage = `Add an execution audit row ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(secondMessage);
	await page.getByRole('button', { name: 'Send' }).click();

	await expect(page.getByText(secondMessage).first()).toBeVisible({ timeout: 20_000 });
	await expect(page.getByText(/Run queued|Run encolado/).first()).toBeVisible({ timeout: 20_000 });
});

test('Threads: the whole chat block stays pinned while the execution console scrolls', async ({
	page,
}) => {
	await page.setViewportSize({ width: 1280, height: 560 });
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();

	const firstMessage = `Add a pinned thread layout endpoint ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(firstMessage);
	await page.getByRole('button', { name: 'Create thread' }).click();
	await expect(page.getByText(firstMessage).first()).toBeVisible({ timeout: 20_000 });
	await expect(page.getByText(/Run queued|Run encolado/).first()).toBeVisible({ timeout: 20_000 });

	const project = await getActiveProject(page);
	const listed = await page.request.get(`/api/v1/threads?projectId=${project.id}`);
	const { threads } = await listed.json();
	const created = threads.find((thread) => thread.title === firstMessage.slice(0, 80));
	expect(created).toBeTruthy();
	const token = await getWriteToken(page);
	for (let index = 0; index < 6; index += 1) {
		const response = await page.request.post(`/api/v1/threads/${created.id}/messages`, {
			headers: { 'X-Local-Control-Token': token },
			data: { content: `Add pinned layout overflow event ${index} ${Date.now()}` },
		});
		expect(response.ok()).toBe(true);
	}

	const scrollRegion = page.locator('.content-frame').first();
	const stickyHeader = page.locator('.thread-conversation-head').first();
	const stickyChat = page.locator('.thread-chat-sticky').first();

	// Precondition: the thread surface must actually overflow the viewport, otherwise the assertions
	// below would pass vacuously.
	await expect
		.poll(() => scrollRegion.evaluate((node) => node.scrollHeight - node.clientHeight), {
			timeout: 20_000,
		})
		.toBeGreaterThan(260);

	await scrollRegion.evaluate((node) => {
		node.scrollTop = 120;
	});
	await page.waitForTimeout(150);
	const headerBefore = await stickyHeader.boundingBox();
	const chatBefore = await stickyChat.boundingBox();
	expect(headerBefore).not.toBeNull();
	expect(chatBefore).not.toBeNull();

	await scrollRegion.evaluate((node) => {
		node.scrollTop = 240;
	});
	await page.waitForTimeout(150);

	const scrollTop = await scrollRegion.evaluate((node) => node.scrollTop);
	expect(scrollTop).toBeGreaterThan(0);

	const headerAfter = await stickyHeader.boundingBox();
	const chatAfter = await stickyChat.boundingBox();
	expect(headerAfter).not.toBeNull();
	expect(chatAfter).not.toBeNull();
	expect(Math.round(headerAfter.y)).toBe(Math.round(headerBefore.y));
	// The whole chat block (title + transcript + composer) stays pinned, not just the title — before
	// this task only the header had `position: sticky`.
	expect(Math.round(chatAfter.y)).toBe(Math.round(chatBefore.y));
});

test('Threads: creating a thread still renders the live layout under reduced motion', async ({
	browser,
}) => {
	const context = await browser.newContext({ reducedMotion: 'reduce' });
	const page = await context.newPage();
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await expect(page.locator('html')).toHaveAttribute('data-motion', 'reduced');

	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();

	const firstMessage = `Reduced motion check ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(firstMessage);
	await page.getByRole('button', { name: 'Create thread' }).click();

	await expect(page.getByText(firstMessage).first()).toBeVisible({ timeout: 20_000 });
	await expect(page.locator('.thread-chat-sticky').first()).toBeVisible();
	await expect(page.locator('.thread-execution-console').first()).toBeVisible();

	await context.close();
});

test('Threads: the new-thread to live-thread handoff renders the full layout', async ({ page }) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();

	const firstMessage = `Handoff check ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(firstMessage);
	await page.getByRole('button', { name: 'Create thread' }).click();

	await expect(page.getByText(firstMessage).first()).toBeVisible({ timeout: 20_000 });
	await expect(page.locator('.thread-chat-sticky').first()).toBeVisible();
	await expect(page.locator('.thread-execution-console').first()).toBeVisible();
	// The intake heading is gone once the live layout has taken over.
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeHidden();
});
