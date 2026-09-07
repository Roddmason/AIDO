import { expect, test } from '@playwright/test';
import { requestCompletedOperation } from '../local-control-center/web/src/api/execution-client';

test('Durable client polls queued and resource_wait before returning the typed result', async () => {
	const originalFetch = globalThis.fetch;
	const calls = [];
	const states = [
		{ executionId: 'test-execution', operation: 'git.refresh', status: 'queued' },
		{ executionId: 'test-execution', status: 'resource_wait', reason: 'memory_floor' },
		{ executionId: 'test-execution', status: 'completed', resultStatusCode: 200, result: { snapshot: { currentBranch: 'dev' } } },
	];
	globalThis.fetch = async (url, options) => {
		calls.push({ url, method: options.method });
		return new Response(JSON.stringify(states.shift()), { status: calls.length === 1 ? 202 : 200 });
	};
	try {
		const result = await requestCompletedOperation('refresh_git_api_v1_projects__project_id__git_refresh_post', { pathParams: { project_id: 'test' }, token: 'test-only' });
		expect(result.snapshot.currentBranch).toBe('dev');
		expect(calls.map(call => call.method)).toEqual(['POST', 'GET', 'GET']);
	} finally { globalThis.fetch = originalFetch; }
});

test('Durable client preserves a terminal failure and never treats control 202 as queued work', async () => {
	const originalFetch = globalThis.fetch;
	try {
		let count = 0;
		globalThis.fetch = async () => new Response(JSON.stringify(++count === 1
			? { executionId: 'failed-execution', operation: 'git.refresh', status: 'queued' }
			: { executionId: 'failed-execution', status: 'failed', reason: 'permission_denied', resultStatusCode: 403 }), { status: count === 1 ? 202 : 200 });
		await expect(requestCompletedOperation('refresh_git_api_v1_projects__project_id__git_refresh_post', { pathParams: { project_id: 'test' } })).rejects.toThrow('permission_denied');
		globalThis.fetch = async () => new Response(JSON.stringify({ executionId: 'cancelled-execution', status: 'cancel_requested' }), { status: 202 });
		const cancelled = await requestCompletedOperation('cancel_api_v1_executions__execution_id__cancel_post', { pathParams: { execution_id: 'cancelled-execution' }, body: { reason: 'operator' } });
		expect(cancelled.status).toBe('cancel_requested');
	} finally { globalThis.fetch = originalFetch; }
});

test('Operations displays offline truth and requires a reason for emergency stop', async ({ page }, testInfo) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await page.goto('/#settings');
	const settings = page.getByRole('dialog', { name: 'Settings', exact: true });
	await expect(settings).toBeVisible({ timeout: 30_000 });
	const disclosure = settings.getByRole('button', { name: /^Operations/ });
	await disclosure.focus();
	await page.keyboard.press('Enter');
	await expect(disclosure).toHaveAttribute('aria-expanded', 'true');
	await expect(settings.getByText('API connected', { exact: true })).toBeVisible();
	await expect(settings.getByText(/Worker · false · offline/)).toBeVisible();
	await settings.getByRole('button', { name: 'Emergency stop', exact: true }).click();
	const stop = page.getByRole('dialog', { name: 'Confirm stop request', exact: true });
	await expect(stop).toBeVisible();
	await expect(stop.getByRole('button', { name: 'Request termination' })).toBeDisabled();
	await stop.getByLabel('Human reason', { exact: true }).fill('Test-only isolated dashboard emergency control');
	await stop.getByRole('button', { name: 'Request termination' }).click();
	await expect(stop).toBeHidden();
	await expect(settings.getByText(/Test-only isolated dashboard emergency control/)).toBeVisible();
	await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	await page.screenshot({ path: testInfo.outputPath(`operations-${testInfo.project.name}.png`), fullPage: true });
});
