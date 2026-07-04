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

/** Seeds a real thread with one executed message so the similarity index has material to match. */
async function seedThreadWithGoal(page, projectId, token, goal) {
	const created = await page.request.post('/api/v1/threads', {
		headers: { 'X-Local-Control-Token': token },
		data: { projectId, ownerType: 'workspace', ownerId: projectId, title: goal.slice(0, 80) },
	});
	expect(created.ok()).toBe(true);
	const { thread } = await created.json();
	const message = await page.request.post(`/api/v1/threads/${thread.id}/messages`, {
		headers: { 'X-Local-Control-Token': token },
		data: { content: goal },
	});
	expect(message.ok()).toBe(true);
	return thread;
}

/** Opens the new-thread intake and types the goal, which arms the debounced similarity lookup. */
async function typeGoalInNewThreadIntake(page, goal) {
	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();
	await page.getByLabel('Message AIDO').fill(goal);
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

test('Threads: the composer stays fixed at the bottom in a single scroll region while the execution console scrolls', async ({
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

	const outerFrame = page.locator('.content-frame').first();
	const scrollRegion = page.locator('.thread-live-scroll').first();
	const header = page.locator('.thread-conversation-head').first();
	const composerDock = page.locator('.thread-composer-dock').first();

	// Precondition: the console must actually overflow its own scroll region, otherwise the
	// assertions below would pass vacuously.
	await expect
		.poll(() => scrollRegion.evaluate((node) => node.scrollHeight - node.clientHeight), {
			timeout: 20_000,
		})
		.toBeGreaterThan(260);

	// The outer frame never scrolls — `.thread-live-scroll` is the only scroll region, so the
	// page never shows a double scrollbar.
	expect(await outerFrame.evaluate((node) => node.scrollHeight - node.clientHeight)).toBe(0);

	await scrollRegion.evaluate((node) => {
		node.scrollTop = 120;
	});
	await page.waitForTimeout(150);
	const headerBefore = await header.boundingBox();
	const composerBefore = await composerDock.boundingBox();
	expect(headerBefore).not.toBeNull();
	expect(composerBefore).not.toBeNull();

	await scrollRegion.evaluate((node) => {
		node.scrollTop = 240;
	});
	await page.waitForTimeout(150);

	const scrollTop = await scrollRegion.evaluate((node) => node.scrollTop);
	expect(scrollTop).toBeGreaterThan(0);

	const headerAfter = await header.boundingBox();
	const composerAfter = await composerDock.boundingBox();
	expect(headerAfter).not.toBeNull();
	expect(composerAfter).not.toBeNull();
	expect(Math.round(headerAfter.y)).toBe(Math.round(headerBefore.y));
	// The composer dock stays pinned at the bottom of the layout (above the global status bar)
	// while only the execution console underneath it scrolls.
	expect(Math.round(composerAfter.y)).toBe(Math.round(composerBefore.y));
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
	await expect(page.locator('.thread-conversation-head').first()).toBeVisible();
	await expect(page.locator('.thread-composer-dock').first()).toBeVisible();
	await expect(page.locator('.thread-execution-pane').first()).toBeVisible();

	await context.close();
});

test('Threads: typing a goal similar to previous work surfaces the suggestion card', async ({
	page,
}) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	const project = await getActiveProject(page);
	const token = await getWriteToken(page);
	const goal = `Add an invoice export endpoint with streaming batches ${Date.now()}`;
	await seedThreadWithGoal(page, project.id, token, goal);

	await typeGoalInNewThreadIntake(page, goal);

	// 500ms debounce + one similarity read; the card names the matched thread.
	const card = page.locator('.thread-similarity-card');
	await expect(card).toBeVisible({ timeout: 15_000 });
	await expect(card).toContainText('This was already worked on in');
	await expect(card).toContainText(goal.slice(0, 80));
	await expect(card.getByRole('button', { name: 'Continue existing thread' })).toBeVisible();
	await expect(card.getByRole('button', { name: 'Refactor existing work' })).toBeVisible();
	await expect(card.getByRole('button', { name: 'Improve performance' })).toBeVisible();
	await expect(card.getByRole('button', { name: 'Create new thread anyway' })).toBeVisible();
});

test('Threads: an archived thread still surfaces in the similarity card with an archived badge', async ({
	page,
}) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	const project = await getActiveProject(page);
	const token = await getWriteToken(page);
	const goal = `Add a dead-letter queue endpoint with replay controls ${Date.now()}`;
	const seeded = await seedThreadWithGoal(page, project.id, token, goal);

	// Archiving must not erase the thread from recall: archived work still surfaces, just flagged.
	const archived = await page.request.post(`/api/v1/threads/${seeded.id}/archive`, {
		headers: { 'X-Local-Control-Token': token },
		data: { reason: 'Archived to verify the similarity badge' },
	});
	expect(archived.ok()).toBe(true);

	await typeGoalInNewThreadIntake(page, goal);
	const card = page.locator('.thread-similarity-card');
	await expect(card).toBeVisible({ timeout: 15_000 });

	// The archived match stays visible and carries the Archived badge instead of being dropped.
	await expect(card).toContainText(seeded.title);
	await expect(card.getByText('Archived', { exact: true })).toBeVisible();
});

test('Threads: continuing from the similarity card opens the existing thread', async ({ page }) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	const project = await getActiveProject(page);
	const token = await getWriteToken(page);
	const goal = `Add a websocket reconnect endpoint with retry limits ${Date.now()}`;
	const seeded = await seedThreadWithGoal(page, project.id, token, goal);

	await typeGoalInNewThreadIntake(page, goal);
	const card = page.locator('.thread-similarity-card');
	await expect(card).toBeVisible({ timeout: 15_000 });

	await card.getByRole('button', { name: 'Continue existing thread' }).click();

	// The existing thread opens (no creation happened): its header shows the seeded title.
	await expect(page.locator('.thread-conversation-head h2')).toHaveText(seeded.title, {
		timeout: 20_000,
	});
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeHidden();
});

test('Threads: improving from the similarity card posts into the existing thread without creating one', async ({
	page,
}) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	const project = await getActiveProject(page);
	const token = await getWriteToken(page);
	const goal = `Add an audit retention endpoint with purge schedules ${Date.now()}`;
	const seeded = await seedThreadWithGoal(page, project.id, token, goal);

	await typeGoalInNewThreadIntake(page, goal);
	const card = page.locator('.thread-similarity-card');
	await expect(card).toBeVisible({ timeout: 15_000 });

	await card.getByRole('button', { name: 'Refactor existing work' }).click();

	// The existing thread opens with the improve message inside it.
	await expect(page.locator('.thread-conversation-head h2')).toHaveText(seeded.title, {
		timeout: 20_000,
	});

	// No second thread with this title exists, and the improve message carries the mode metadata.
	const listed = await page.request.get(`/api/v1/threads?projectId=${project.id}`);
	const { threads } = await listed.json();
	expect(threads.filter((thread) => thread.title === seeded.title)).toHaveLength(1);
	const detailResponse = await page.request.get(`/api/v1/threads/${seeded.id}`);
	const detail = await detailResponse.json();
	const improveMessage = detail.messages.find(
		(message) => message.kind === 'user' && message.metadata?.mode === 'improve_existing',
	);
	expect(improveMessage).toBeTruthy();
	expect(improveMessage.content).toBe(goal);
});

test('Threads: creating a new thread anyway keeps the flow and records the decision event', async ({
	page,
}) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	const project = await getActiveProject(page);
	const token = await getWriteToken(page);
	const goal = `Add a report render endpoint with worker pool caps ${Date.now()}`;
	const seeded = await seedThreadWithGoal(page, project.id, token, goal);

	await typeGoalInNewThreadIntake(page, goal);
	const card = page.locator('.thread-similarity-card');
	await expect(card).toBeVisible({ timeout: 15_000 });

	await card.getByRole('button', { name: 'Create new thread anyway' }).click();

	// The normal create flow is preserved: live layout, message visible, run queued.
	await expect(page.getByText(goal).first()).toBeVisible({ timeout: 20_000 });
	await expect(page.getByText(/Run queued|Run encolado/).first()).toBeVisible({ timeout: 20_000 });

	// A second thread with the same derived title now exists...
	const listed = await page.request.get(`/api/v1/threads?projectId=${project.id}`);
	const { threads } = await listed.json();
	const twins = threads.filter((thread) => thread.title === seeded.title);
	expect(twins).toHaveLength(2);
	const createdThread = twins.find((thread) => thread.id !== seeded.id);
	expect(createdThread).toBeTruthy();

	// ...and it carries the deduplication decision: message metadata + the similarity_marked event.
	await expect
		.poll(
			async () => {
				const detailResponse = await page.request.get(`/api/v1/threads/${createdThread.id}`);
				const detail = await detailResponse.json();
				return detail.events.some((event) => event.type === 'similarity_marked');
			},
			{ timeout: 20_000 },
		)
		.toBe(true);
	const detailResponse = await page.request.get(`/api/v1/threads/${createdThread.id}`);
	const detail = await detailResponse.json();
	const firstMessage = detail.messages.find((message) => message.kind === 'user');
	expect(firstMessage.metadata?.mode).toBe('create_new_anyway');
	expect(firstMessage.metadata?.similarThreadId).toBe(seeded.id);
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
	await expect(page.locator('.thread-conversation-head').first()).toBeVisible();
	await expect(page.locator('.thread-composer-dock').first()).toBeVisible();
	await expect(page.locator('.thread-execution-pane').first()).toBeVisible();
	// The intake heading is gone once the live layout has taken over.
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeHidden();
});

test('Threads: the inspector Memory tab shows what AIDO remembers across six categories', async ({
	page,
}) => {
	// The inspector auto-opens only on the desktop layout; pin it so the mobile project runs
	// the same three-pane shell instead of the stacked layout without the side console.
	await page.setViewportSize({ width: 1280, height: 800 });
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	const project = await getActiveProject(page);
	const token = await getWriteToken(page);
	const goal = `Add a nightly report scheduler endpoint with cron windows ${Date.now()}`;
	const seeded = await seedThreadWithGoal(page, project.id, token, goal);

	// Typing the same goal surfaces the duplicate card; creating anyway leaves the seeded twin
	// behind as recallable memory for the new thread.
	await typeGoalInNewThreadIntake(page, goal);
	const card = page.locator('.thread-similarity-card');
	await expect(card).toBeVisible({ timeout: 15_000 });
	await card.getByRole('button', { name: 'Create new thread anyway' }).click();
	await expect(page.locator('.thread-conversation-head h2')).toBeVisible({ timeout: 20_000 });

	// Selecting a live thread auto-opens the right inspector; switch to the Memory tab.
	await page.getByRole('tab', { name: 'Memory' }).click();

	await expect(page.getByText('What AIDO remembers about work like this')).toBeVisible({
		timeout: 20_000,
	});
	await expect(page.getByRole('heading', { name: 'Similar threads' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Previous decisions' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Related evidence' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Lessons learned' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Prior performance issues' })).toBeVisible();
	await expect(
		page.getByRole('heading', { name: 'Functionality already implemented' }),
	).toBeVisible();

	// The recall is real, not decorative: the seeded twin shows up under Similar threads.
	await expect(page.locator('.thread-inspector-list').first()).toContainText(seeded.title);
});
