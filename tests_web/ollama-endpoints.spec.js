import { createServer } from 'node:http';

import { expect, test } from '@playwright/test';

/**
 * Settings → Providers & CLI → Ollama endpoints, against the REAL control plane: every endpoint is a
 * real provider account, every probe is a real `/api/tags` round-trip. The only thing simulated is the
 * Ollama server itself, and it speaks the real protocol — so a green card here means the operator can
 * actually reach that daemon, and a red one carries the reason the backend reported, not a placeholder.
 */

/** TEST-NET-3 (RFC 5737): guaranteed non-loopback, so the backend classifies it as a remote server. */
const REMOTE_BASE_URL = 'http://203.0.113.10:11434';
/** Discard port on loopback: nothing listens, so a probe is refused immediately instead of timing out. */
const DEAD_BASE_URL = 'http://127.0.0.1:9';
/**
 * Endpoints are provider accounts, so they outlive the page. Scoping the ids to this run lets every
 * test assert the transition it causes (card absent → present, no models → models) instead of
 * inheriting a state a previous run left in the dashboard database.
 */
const RUN_ID = String(Date.now()).slice(-8);
const REMOTE_ID = `ollama-e2e-remote-${RUN_ID}`;
const DOWN_ID = `ollama-e2e-down-${RUN_ID}`;
const SYNCED_ID = `ollama-e2e-synced-${RUN_ID}`;
const PREFERRED_ID = `ollama-e2e-pref-${RUN_ID}`;
const SYNCED_MODELS = ['llama3.2:3b', 'qwen3:8b'];
/** The role whose policy the "set preferred" test rewrites; restored before the test returns. */
const PREFERRED_ROLE = 'analyst';

let tagsServer = null;
let tagsBaseUrl = '';

/** A real Ollama-protocol server exposing `/api/tags`, so sync-models has something true to read. */
function startTagsServer() {
	return new Promise((resolve) => {
		const server = createServer((incoming, response) => {
			if (incoming.url !== '/api/tags') {
				response.writeHead(404).end();
				return;
			}
			response.writeHead(200, { 'Content-Type': 'application/json' });
			response.end(JSON.stringify({ models: SYNCED_MODELS.map((name) => ({ name })) }));
		});
		server.listen(0, '127.0.0.1', () => {
			resolve({ server, baseUrl: `http://127.0.0.1:${server.address().port}` });
		});
	});
}

async function writeToken(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	return (await handshake.json()).token;
}

/** Registers an endpoint through the write API so a test can start from a known control-plane state. */
async function seedEndpoint(page, { id, baseUrl, enabled = true, syncModels = false }) {
	const token = await writeToken(page);
	const response = await page.request.post('/api/v1/ollama/endpoints', {
		headers: { 'X-Local-Control-Token': token },
		data: { id, displayName: id, baseUrl, enabled },
	});
	expect(response.status()).toBe(201);
	if (!syncModels) return;
	const synced = await page.request.post(`/api/v1/ollama/endpoints/${id}/sync-models`, {
		headers: { 'X-Local-Control-Token': token },
	});
	expect(synced.status()).toBe(200);
}

async function rolePolicy(page, role) {
	const response = await page.request.get('/api/v1/model-gateway/role-policies');
	return (await response.json()).rolePolicies.find((policy) => policy.role === role);
}

async function restoreRolePolicy(page, role, preferred) {
	const token = await writeToken(page);
	await page.request.patch(`/api/v1/model-gateway/role-policies/${role}`, {
		headers: { 'X-Local-Control-Token': token },
		data: { preferred },
	});
}

/** Opens Settings at Providers & CLI and returns the Settings dialog. */
async function openProvidersSettings(page) {
	await page.goto('/#settings-runtime');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const settings = page.getByRole('dialog', { name: 'Settings' });
	await expect(settings).toBeVisible();
	await expect(settings.getByText('Ollama endpoints', { exact: true })).toBeVisible();
	return settings;
}

function endpointCard(settings, endpointId) {
	return settings.locator('.card').filter({ hasText: endpointId });
}

test.beforeAll(async () => {
	const started = await startTagsServer();
	tagsServer = started.server;
	tagsBaseUrl = started.baseUrl;
});

test.afterAll(async ({ playwright }, testInfo) => {
	// Every endpoint left behind is polled by the runtime-status rollup for the rest of the chunk, so
	// re-point them at a refused loopback port: a leftover must fail fast, never stall the poll.
	const context = await playwright.request.newContext({ baseURL: testInfo.project.use.baseURL });
	try {
		const token = (await (await context.get('/api/v1/security/handshake')).json()).token;
		for (const id of [REMOTE_ID, DOWN_ID, SYNCED_ID, PREFERRED_ID]) {
			await context.post('/api/v1/ollama/endpoints', {
				headers: { 'X-Local-Control-Token': token },
				data: { id, displayName: id, baseUrl: DEAD_BASE_URL, enabled: false },
			});
		}
	} finally {
		await context.dispose();
	}
	await new Promise((resolve) => tagsServer.close(resolve));
});

test('Ollama endpoints: a failed initial load shows Retry instead of a false empty state', async ({
	page,
}) => {
	let failLoad = true;
	await page.route('**/api/v1/ollama/endpoints', async (route) => {
		if (route.request().method() === 'GET' && failLoad) {
			await route.fulfill({ status: 503, json: { detail: 'controlled endpoint read failure' } });
			return;
		}
		await route.continue();
	});

	const settings = await openProvidersSettings(page);
	const loadFailure = settings.getByRole('alert').filter({
		hasText: 'Could not load Ollama endpoints',
	});
	await expect(loadFailure).toBeVisible();
	await expect(settings.getByText('No Ollama endpoint is configured yet')).toBeHidden();

	failLoad = false;
	await loadFailure.getByRole('button', { name: 'Retry' }).click();
	await expect(loadFailure).toBeHidden();
	await expect(settings.getByRole('button', { name: 'Add endpoint' })).toBeVisible();
});

test('Ollama endpoints: adding a remote server registers it as a remote endpoint card', async ({
	page,
}) => {
	const settings = await openProvidersSettings(page);
	await expect(endpointCard(settings, REMOTE_ID)).toBeHidden();

	await settings.getByRole('button', { name: 'Add endpoint' }).click();
	const dialog = page.getByRole('dialog', { name: 'Add Ollama endpoint' });
	await expect(dialog).toBeVisible();
	await dialog.getByLabel('Endpoint id').fill(REMOTE_ID);
	await dialog.getByLabel('Display name').fill(REMOTE_ID);
	await dialog.getByLabel('Base URL').fill(REMOTE_BASE_URL);
	await expect(dialog.getByLabel('Enable this endpoint')).toBeChecked();
	await dialog.getByRole('button', { name: 'Add endpoint' }).click();
	await expect(dialog).toBeHidden();

	// The card states the four facts the operator needs before trusting the endpoint.
	const card = endpointCard(settings, REMOTE_ID);
	await expect(card).toBeVisible();
	await expect(card).toContainText('remote server');
	await expect(card).toContainText('enabled');
	await expect(card).toContainText(REMOTE_BASE_URL);
	await expect(card).toContainText('not measured');
});

test('Ollama endpoints: validating an unreachable endpoint shows the backend reason', async ({
	page,
}) => {
	await seedEndpoint(page, { id: DOWN_ID, baseUrl: DEAD_BASE_URL });
	const settings = await openProvidersSettings(page);
	const card = endpointCard(settings, DOWN_ID);
	await expect(card).toBeVisible();

	await card.getByRole('button', { name: 'Validate' }).click();

	await expect(page.getByText('Endpoint did not respond')).toBeVisible();
	await expect(card).toContainText('offline');
	await expect(card.getByText('Last error')).toBeVisible();
	// The reason is the probe's own failure, surfaced verbatim rather than a generic "unavailable".
	await expect(card).toContainText(/URLError/);
});

test('Ollama endpoints: synced models become visible on the endpoint card', async ({ page }) => {
	await seedEndpoint(page, { id: SYNCED_ID, baseUrl: tagsBaseUrl });
	const settings = await openProvidersSettings(page);
	const card = endpointCard(settings, SYNCED_ID);
	await expect(card).toBeVisible();
	await expect(card).toContainText('No model synced yet');

	await card.getByRole('button', { name: 'Sync models' }).click();

	await expect(page.getByText('Models synced')).toBeVisible();
	for (const model of SYNCED_MODELS) {
		await expect(card.getByText(model, { exact: true })).toBeVisible();
	}
	// Only a synced endpoint can be routed to, so the role action unlocks with its models.
	await expect(card.getByRole('button', { name: 'Set preferred for role' })).toBeEnabled();
});

test('Ollama endpoints: setting an endpoint as preferred rewrites the real role policy', async ({
	page,
}) => {
	await seedEndpoint(page, { id: PREFERRED_ID, baseUrl: tagsBaseUrl, syncModels: true });
	const original = await rolePolicy(page, PREFERRED_ROLE);
	expect(original.preferred[0].provider).not.toBe(PREFERRED_ID);

	try {
		const settings = await openProvidersSettings(page);
		const card = endpointCard(settings, PREFERRED_ID);
		await card.getByRole('button', { name: 'Set preferred for role' }).click();

		const dialog = page.getByRole('dialog', { name: 'Set preferred for role' });
		await expect(dialog).toBeVisible();
		// `exact` matters: the dialog's own close button is labelled "Close Set preferred for role".
		await dialog.getByLabel('Role', { exact: true }).selectOption(PREFERRED_ROLE);
		await dialog.getByLabel('Model', { exact: true }).selectOption(SYNCED_MODELS[0]);
		await dialog.getByRole('button', { name: 'Set as preferred' }).click();

		await expect(page.getByText('Role now prefers this endpoint')).toBeVisible();
		await expect(card.getByText('Preferred for roles')).toBeVisible();
		await expect(card.getByText(PREFERRED_ROLE, { exact: true })).toBeVisible();

		// The chip is only a reflection: the routing policy itself must now lead with this endpoint,
		// and the candidates it already had must survive the reorder.
		const updated = await rolePolicy(page, PREFERRED_ROLE);
		expect(updated.preferred[0]).toMatchObject({
			provider: PREFERRED_ID,
			model: SYNCED_MODELS[0],
		});
		expect(updated.preferred.slice(1)).toEqual(original.preferred);
	} finally {
		await restoreRolePolicy(page, PREFERRED_ROLE, original.preferred);
	}
});
