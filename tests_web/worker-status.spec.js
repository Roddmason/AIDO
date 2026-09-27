import { expect, test } from './fixtures/operations.js';

/**
 * Status bar worker item: a fresh install starts the worker paused on purpose, so the bar must say
 * so and resume it in one click; otherwise every queued operation waits forever without a cue.
 * @author Rodrigo Mason
 */

const pausedStatus = {
	autostart: false,
	claimedJobs: 0,
	completedRuns: 0,
	failedRuns: 0,
	inFlightJobs: 0,
	maxConcurrentJobs: 1,
	paused: true,
	pollIntervalSeconds: 5,
	reason: 'Worker requires an explicit resume.',
	running: false,
	status: 'paused',
	role: 'leader',
	connected: true,
};

test('Status bar: a paused worker is announced and resumed from the bar', async ({ page }) => {
	let resumed = 0;
	await page.route('**/api/v1/workers/status', (route) =>
		route.fulfill({ json: resumed ? { ...pausedStatus, paused: false, running: true, status: 'running' } : pausedStatus }),
	);
	await page.route('**/api/v1/workers/resume', async (route) => {
		resumed += 1;
		await route.fulfill({ json: { ...pausedStatus, paused: false, running: true, status: 'running' } });
	});
	try {
		await page.goto('/');
		const bar = page.getByRole('contentinfo', { name: 'Global status' });
		const item = bar.locator('[data-worker-state]');
		await expect(item).toHaveAttribute('data-worker-state', 'paused', { timeout: 30_000 });
		await expect(item).toContainText('Worker paused');
		await expect(item).toHaveAttribute('title', /waits until you resume the worker/);
		await item.getByRole('button', { name: 'Resume' }).click();
		await expect(item).toHaveAttribute('data-worker-state', 'running');
		await expect(item.getByRole('button', { name: 'Resume' })).toHaveCount(0);
		expect(resumed).toBe(1);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Status bar: without a connected worker process the bar says the worker is offline', async ({ page }) => {
	await page.route('**/api/v1/workers/status', (route) =>
		route.fulfill({ json: { ...pausedStatus, paused: false, status: 'stopped', role: 'offline', connected: false } }),
	);
	try {
		await page.goto('/');
		const item = page.getByRole('contentinfo', { name: 'Global status' }).locator('[data-worker-state]');
		await expect(item).toHaveAttribute('data-worker-state', 'offline', { timeout: 30_000 });
		await expect(item).toContainText('Worker offline');
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});
