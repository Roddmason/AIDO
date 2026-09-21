import { expect, test } from './fixtures/operations.js';

test.use({ viewport: { width: 1280, height: 800 } });

test('Threads: a worker failure refreshes worker status while the thread stays queued', async ({ page }) => {
	const { projects } = await (await page.request.get('/api/v1/projects')).json();
	const project = projects.find((entry) => entry.status === 'active');
	expect(project).toBeTruthy();
	const thread = {
		id: 'thread-worker-failure-fixture', projectId: project.id, ownerId: project.id,
		ownerType: 'workspace', title: 'Queued worker failure fixture', summary: '',
		status: 'queued', metadata: {}, createdAt: '2026-09-20T00:00:00Z', updatedAt: '2026-09-20T00:00:00Z',
	};
	let failed = false;
	const event = (sequence, type, payload) => ({
		id: `worker-failure-${sequence}`, threadId: thread.id, projectId: project.id,
		sequence, type, payload, metadata: {}, createdAt: thread.createdAt,
	});
	const events = [event(1, 'run_queued', { diagnostic: `diagnostic-only-marker ${'.'.repeat(64_000)}` })];
	await page.route('**/api/v1/overview', async (route) => {
		const response = await route.fetch();
		const overview = await response.json();
		await route.fulfill({ response, json: { ...overview, threads: [...overview.threads, thread] } });
	});
	await page.route(`**/api/v1/threads/${thread.id}`, (route) => route.fulfill({ json: {
		thread, messages: [], decisions: [], artifacts: [], events: [],
	} }));
	await page.route(`**/api/v1/threads/${thread.id}/remediations`, (route) =>
		route.fulfill({ json: { remediations: [] } }),
	);
	await page.route(`**/api/v1/threads/${thread.id}/events?*`, (route) => {
		const afterSeq = Number(new URL(route.request().url()).searchParams.get('afterSeq'));
		return route.fulfill({ json: {
			events: events.filter((item) => item.sequence > afterSeq),
			lastSeq: events.at(-1).sequence, running: true, threadStatus: 'queued',
		} });
	});
	await page.route('**/api/v1/workers/status', (route) => route.fulfill({ json: {
		status: failed ? 'blocked' : 'running', running: !failed, paused: false,
		autostart: true, reason: failed ? 'Host capacity is unavailable.' : '',
		maxConcurrentJobs: 1, pollIntervalSeconds: 1, inFlightJobs: 0,
		claimedJobs: 0, completedRuns: 0, failedRuns: 0,
	} }));
	try {
		await page.goto('/#threads');
		await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
		await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
		const threadButton = page.getByRole('button', { name: thread.title, exact: true });
		if (!(await threadButton.isVisible())) {
			await page.locator('.thread-workspace-head').filter({ hasText: project.name }).click();
		}
		await threadButton.click();
		const execution = page.getByRole('complementary', { name: 'Execution console' });
		await expect(execution.getByText('Worker running', { exact: true })).toBeVisible();
		await expect(execution.getByRole('button', { name: 'Run now', exact: true })).toHaveCount(0);
		const technical = execution.locator('.thread-console-row[data-type="run_queued"] details');
		await expect(technical.locator('pre')).toHaveCount(0);
		await technical.locator('summary').focus();
		await expect(technical.locator('summary')).toBeFocused();
		await page.keyboard.press('Enter');
		await expect(technical).toHaveJSProperty('open', true);
		await expect(technical.locator('pre')).toContainText('diagnostic-only-marker');
		await technical.locator('summary').press('Space');
		await expect(technical.locator('pre')).toHaveCount(0);

		failed = true;
		events.push(event(2, 'worker_failed', { reason: 'Host capacity is unavailable.' }));
		await expect(execution.getByText('Host capacity is unavailable.', { exact: true })).toBeVisible();
		await expect(execution.getByText('Worker stopped', { exact: true })).toBeVisible();
		await expect(execution.getByRole('region', { name: 'Waiting for worker' })).toBeVisible();
		await expect(execution.getByRole('button', { name: 'Run now', exact: true })).toBeVisible();
		await expect(page.locator('.thread-conversation-head').getByText('queued', { exact: true })).toBeVisible();
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

for (const resumed of [false, true]) {
	test(`Threads: a worker failure ${resumed ? 'is superseded by later execution' : 'blocks execution after historical blockers'}`, async ({ page }) => {
		const { projects } = await (await page.request.get('/api/v1/projects')).json();
		const project = projects.find((entry) => entry.status === 'active');
		expect(project).toBeTruthy();
		const thread = {
			id: `thread-worker-pipeline-${resumed ? 'resumed' : 'blocked'}`, projectId: project.id, ownerId: project.id,
			ownerType: 'workspace', title: `Worker pipeline ${resumed ? 'resumed' : 'blocked'}`, summary: '',
			status: 'blocked', metadata: {}, createdAt: '2026-09-21T00:00:00Z', updatedAt: '2026-09-21T00:00:00Z',
		};
		const event = (sequence, type, payload = {}) => ({
			id: `worker-pipeline-${sequence}`, threadId: thread.id, projectId: project.id,
			sequence, type, payload, metadata: {}, createdAt: thread.createdAt,
		});
		const events = [
			event(10, 'blocked', { stage: 'resource_manager', reason: 'Historical runtime failure.' }),
			event(20, 'runtime_check'), event(21, 'git_check'), event(30, 'executing'),
			event(31, 'worker_failed', { reason: 'execution_deadline_exhausted' }),
			...(resumed ? [event(32, 'executing')] : []),
		];
		await page.route('**/api/v1/overview', async (route) => {
			const response = await route.fetch();
			const overview = await response.json();
			await route.fulfill({ response, json: { ...overview, threads: [...overview.threads, thread] } });
		});
		await page.route(`**/api/v1/threads/${thread.id}`, (route) => route.fulfill({ json: {
			thread, messages: [], decisions: [], artifacts: [], events: [],
		} }));
		await page.route(`**/api/v1/threads/${thread.id}/remediations`, (route) =>
			route.fulfill({ json: { remediations: [] } }),
		);
		await page.route(`**/api/v1/threads/${thread.id}/events?*`, (route) => {
			const afterSeq = Number(new URL(route.request().url()).searchParams.get('afterSeq'));
			return route.fulfill({ json: {
				events: events.filter((item) => item.sequence > afterSeq), lastSeq: events.at(-1).sequence,
				running: resumed, threadStatus: 'blocked',
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
			const execution = page.getByRole('complementary', { name: 'Execution console' });
			await expect(execution.locator('.thread-pipeline-step', { hasText: 'Runtime check' })).toHaveAttribute('data-state', 'done');
			await expect(execution.locator('.thread-pipeline-step', { hasText: 'Git check' })).toHaveAttribute('data-state', 'done');
			await expect(execution.locator('.thread-pipeline-step', { hasText: 'Executing' })).toHaveAttribute('data-state', resumed ? 'active' : 'blocked');
			await expect(execution.locator('.thread-remediation-card')).toHaveCount(resumed ? 0 : 1);
			if (!resumed) {
				await expect(execution.locator('.thread-remediation-card')).toContainText('execution_deadline_exhausted');
				await expect(execution.locator('.thread-remediation-card').getByText('worker', { exact: true })).toBeVisible();
			}
		} finally {
			await page.unrouteAll({ behavior: 'ignoreErrors' });
		}
	});
}

test('Threads: a ProductOwner block keeps its stage when the job repeats the failure', async ({ page }) => {
	const firstMessage = `Pipeline blocker stage ${Date.now()}`;
	const reason = 'ProductOwnerAgent provider request failed (http_status=404).';
	await page.route('**/api/v1/threads/*/remediations', (route) =>
		route.fulfill({ json: { remediations: [] } }),
	);
	await page.route('**/api/v1/threads/*/events?*', (route) =>
		route.fulfill({ json: { events: [], lastSeq: 0, running: false, threadStatus: 'blocked' } }),
	);
	await page.route('**/api/v1/threads/*', async (route) => {
		const response = await route.fetch();
		const detail = await response.json();
		if (detail.thread?.title !== firstMessage.slice(0, 80)) {
			await route.fulfill({ response });
			return;
		}
		const event = (sequence, type, payload) => ({
			id: `pipeline-stage-${sequence}`, threadId: detail.thread.id,
			projectId: detail.thread.projectId, sequence, type, payload, metadata: {},
			createdAt: detail.thread.createdAt,
		});
		await route.fulfill({ response, json: {
			...detail,
			thread: { ...detail.thread, status: 'blocked' },
			events: [
				event(10, 'blocked', { loopId: 'previous-loop', stage: 'resource_manager', reason: 'Previous runtime failure.' }),
				event(20, 'runtime_check', { loopId: 'current-loop' }),
				event(21, 'git_check', { loopId: 'current-loop' }),
				event(30, 'blocked', { loopId: 'current-loop', stage: 'product_owner', reason }),
				event(31, 'blocked', { loopId: 'current-loop', reason }),
			],
		} });
	});
	try {
		await page.goto('/#threads');
		await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
		await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
		await page.locator('.thread-workspace-head').first().click();
		await page.locator('.shell-new-thread').click();
		await page.getByLabel('Message AIDO').fill(firstMessage);
		await page.getByRole('button', { name: 'Create thread' }).click();
		const execution = page.getByRole('complementary', { name: 'Execution console' });
		await expect(execution.locator('.thread-pipeline-step', { hasText: 'Git check' })).toHaveAttribute('data-state', 'done');
		await expect(execution.locator('.thread-pipeline-step', { hasText: 'Discovery and research' })).toHaveAttribute('data-state', 'blocked');
		const blocker = execution.locator('.thread-remediation-card');
		await expect(blocker.getByText('product owner', { exact: true })).toBeVisible();
		await expect(blocker.getByText(reason, { exact: true })).toBeVisible();
		await expect(blocker.getByText('Previous runtime failure.', { exact: true })).toHaveCount(0);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});
