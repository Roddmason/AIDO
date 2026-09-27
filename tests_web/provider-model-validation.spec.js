import { expect, test } from '@playwright/test';

/**
 * Providers & CLI: "Validate all models" on a gateway card tests every enabled model one by one. The
 * card shows a progress bar (done/total, ok/failed) while the queued run is live, then "N ok · M
 * discarded" and a filterable result list where a discarded model offers Retest and Re-enable. The
 * backend is mocked end to end (providers, models, the queued operation, its execution and the
 * model-validation status): the domain behaviour is pinned by tests_py/test_gateway_catalog_validation.py.
 * @author Rodrigo Mason
 */

const PROVIDER = 'omniroute';
const MODELS = [
	{ model: 'oc/big-pickle', enabled: true },
	{ model: 'oc/deepseek-free', enabled: true },
	{ model: 'cc/claude-x', enabled: false, disabledReason: 'validation_failed' },
	{ model: 'deepinfra/busy', enabled: true },
].map((item) => ({ id: `${PROVIDER}:${item.model}`, providerId: PROVIDER, freeTier: true, ...item }));

function outcome(model, status, extra = {}) {
	return {
		modelId: `${PROVIDER}:${model}`,
		model,
		status,
		httpStatus: null,
		reason: null,
		detail: null,
		latencyMs: 120,
		runId: 'exec-validate-all',
		testedAt: '2026-09-27T10:00:00.000Z',
		enabled: status !== 'failed',
		discarded: status === 'failed',
		...extra,
	};
}

function run(status, counts) {
	return {
		runId: 'exec-validate-all',
		providerId: PROVIDER,
		status,
		reason: '',
		total: 4,
		concurrency: 4,
		modelTimeoutSeconds: 20,
		budgetSeconds: 600,
		startedAt: '2026-09-27T10:00:00.000Z',
		updatedAt: '2026-09-27T10:00:01.000Z',
		finishedAt: status === 'running' ? null : '2026-09-27T10:00:02.000Z',
		skipped: 0,
		discarded: 0,
		...counts,
	};
}

const FINAL_OUTCOMES = [
	outcome('oc/big-pickle', 'ok'),
	outcome('oc/deepseek-free', 'ok'),
	outcome('cc/claude-x', 'failed', {
		httpStatus: 401,
		reason: 'credential_invalid',
		detail: 'HTTP 401: Invalid API key for upstream cc',
	}),
	outcome('deepinfra/busy', 'skipped', { httpStatus: 429, detail: 'HTTP 429: Upstream quota exceeded' }),
];
const VALIDATED = { status: 'validated', checkedAt: '2026-09-27T10:00:02Z', model: 'oc/big-pickle' };

/** Mocks the card's reads and the validation endpoints; returns the recorded writes. */
async function mockGateway(page) {
	const state = { phase: 'idle', polls: 0, writes: [] };
	await page.route('**/api/v1/model-gateway/providers', async (route) => {
		const response = await route.fetch();
		const body = await response.json();
		const original = body.providers.find((item) => item.providerId === PROVIDER) ?? {};
		body.providers = [
			...body.providers.filter((item) => item.providerId !== PROVIDER),
			{
				...original,
				providerId: PROVIDER,
				providerType: 'gateway',
				displayName: 'OmniRoute',
				enabled: true,
				credentialStatus: 'not_required',
				healthStatus: 'healthy',
				baseUrl: 'http://localhost:20128/v1',
			},
		];
		await route.fulfill({ response, json: body });
	});
	await page.route('**/api/v1/model-gateway/models', async (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		const response = await route.fetch();
		const body = await response.json();
		body.models = [...body.models.filter((item) => item.providerId !== PROVIDER), ...MODELS];
		await route.fulfill({ response, json: body });
	});
	await page.route(`**/api/v1/model-gateway/providers/${PROVIDER}/model-validation`, (route) => {
		if (state.phase === 'idle') {
			return route.fulfill({
				json: { run: null, outcomes: [], untested: 3, providerValidation: { status: 'never' } },
			});
		}
		if (state.phase === 'running') {
			return route.fulfill({
				json: {
					run: run('running', { done: 2, ok: 1, failed: 1 }),
					outcomes: FINAL_OUTCOMES.slice(0, 2),
					untested: 2,
					providerValidation: VALIDATED,
				},
			});
		}
		return route.fulfill({
			json: {
				run: run('completed', { done: 4, ok: 2, failed: 1, skipped: 1, discarded: 1 }),
				outcomes: FINAL_OUTCOMES,
				untested: 0,
				providerValidation: VALIDATED,
			},
		});
	});
	await page.route(`**/api/v1/model-gateway/providers/${PROVIDER}/validate-all-models`, (route) => {
		state.writes.push({ validate: route.request().postDataJSON() });
		state.phase = 'running';
		state.polls = 0;
		// 200 (not 202): the accepted execution is observed by the UI, never run by a fixture.
		return route.fulfill({
			json: {
				executionId: 'exec-validate-all',
				jobId: 'exec-validate-all',
				operation: 'models.validate_all_models',
				status: 'queued',
			},
		});
	});
	await page.route('**/api/v1/executions/exec-validate-all', (route) => {
		state.polls += 1;
		const done = state.polls >= 5;
		if (done) state.phase = 'done';
		return route.fulfill({
			json: {
				executionId: 'exec-validate-all',
				jobId: 'exec-validate-all',
				operation: 'models.validate_all_models',
				projectId: null,
				workloadClass: 'remote_llm_light',
				status: done ? 'completed' : 'running',
				createdAt: '2026-09-27T10:00:00Z',
				startedAt: '2026-09-27T10:00:00Z',
				finishedAt: done ? '2026-09-27T10:00:02Z' : null,
				cancelRequestedAt: null,
				reason: '',
				canCancel: !done,
				resultStatusCode: done ? 200 : null,
				result: done
					? {
							run: run('completed', { done: 4, ok: 2, failed: 1, skipped: 1, discarded: 1 }),
							outcomes: FINAL_OUTCOMES,
							providerValidation: VALIDATED,
						}
					: null,
			},
		});
	});
	await page.route(`**/api/v1/model-gateway/providers/${PROVIDER}/models`, async (route) => {
		if (route.request().method() !== 'PATCH') return route.continue();
		state.writes.push({ patch: route.request().postDataJSON() });
		await route.fulfill({ json: { providerId: PROVIDER, enabled: true, updated: 1 } });
	});
	return state;
}

async function openGatewayCard(page) {
	await page.goto('/#settings-runtime');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const settings = page.getByRole('dialog', { name: 'Settings' });
	await expect(settings).toBeVisible();
	const card = settings.locator('.provider-card').filter({ hasText: 'OmniRoute' }).first();
	await expect(card).toBeVisible({ timeout: 30_000 });
	return card;
}

test('Providers & CLI: validate all models shows progress, the ok/discarded summary and each result', async ({
	page,
}) => {
	const state = await mockGateway(page);
	try {
		const card = await openGatewayCard(page);
		const section = card.getByRole('region', { name: 'Model validation' });
		await section.getByRole('button', { name: 'Validate all models' }).click();

		// While the run is live: a progress bar with done/total and the running ok/failed tally.
		await expect(section.getByRole('progressbar', { name: 'Models tested' })).toHaveAttribute(
			'aria-valuenow',
			'2',
		);
		await expect(section.getByText('2/4 tested · 1 ok · 1 failed')).toBeVisible();

		// Then the summary: the provider keeps its two working models and discards one.
		await expect(section.getByText('2 ok · 1 discarded', { exact: true })).toBeVisible();
		await expect(section.getByText('1 not confirmed')).toBeVisible();
		expect(state.writes[0]).toEqual({ validate: {} });

		await section.getByRole('button', { name: 'Show results (4)' }).click();
		await section.getByRole('button', { name: 'Failed (1)' }).click();
		const rows = section.getByRole('listitem');
		await expect(rows).toHaveCount(1);
		await expect(rows.first()).toContainText('cc/claude-x');
		await expect(rows.first()).toContainText('Discarded by validation');
		await expect(rows.first()).toContainText('HTTP 401: Invalid API key for upstream cc');

		await section.getByRole('button', { name: 'Re-enable cc/claude-x' }).click();
		await expect.poll(() => state.writes.length).toBe(2);
		expect(state.writes[1]).toEqual({
			patch: { enabled: true, models: [`${PROVIDER}:cc/claude-x`] },
		});

		// The transient failure stays enabled as "not confirmed" and can be retested, not re-enabled.
		await section.getByRole('button', { name: 'Not confirmed (1)' }).click();
		await expect(rows.first()).toContainText('deepinfra/busy');
		await expect(section.getByRole('button', { name: 'Re-enable deepinfra/busy' })).toHaveCount(0);
		await expect(section.getByRole('button', { name: 'Retest deepinfra/busy' })).toBeVisible();

		// Retest sends only that model to the same queued operation.
		await section.getByRole('button', { name: 'Failed (1)' }).click();
		await section.getByRole('button', { name: 'Retest cc/claude-x' }).click();
		await expect.poll(() => state.writes.length).toBe(3);
		expect(state.writes[2]).toEqual({ validate: { models: [`${PROVIDER}:cc/claude-x`] } });
		await expect(section.getByText('2 ok · 1 discarded', { exact: true })).toBeVisible({ timeout: 15_000 });
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});
