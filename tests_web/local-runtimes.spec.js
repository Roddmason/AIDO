import { createServer } from 'node:http';

import { expect, test } from './fixtures/operations.js';

/**
 * Local OpenAI-compatible runtimes (llama.cpp, LM Studio, vLLM, generic) against the REAL control
 * plane. The only simulated piece is the model server: a node http double that speaks the llama.cpp
 * router protocol (`/health`, `/props`, `/v1/models` with per-model load state, chat completions),
 * so every probe, sync, validation and deletion here is a real round trip through AIDO. Thread-team
 * candidates, thread events and the provider poll are mocked only where a scenario needs state the
 * double cannot create (a sealed thread, a down server).
 */

test.use({ viewport: { width: 1280, height: 800 } });

const RUN_ID = String(Date.now()).slice(-8);
const DOUBLE_MODELS = [
	{ id: 'qwen3-8b', state: 'loaded' },
	{ id: 'gemma-3-4b', state: 'unloaded' },
];
const PANEL_ID = `llama-e2e-panel-${RUN_ID}`;
const DELETE_ID = `llama-e2e-delete-${RUN_ID}`;
const IN_USE_ID = `llama-e2e-inuse-${RUN_ID}`;
const WIZARD_ID = `llama-e2e-wizard-${RUN_ID}`;
const DISCOVERED_ID = `llama-e2e-found-${RUN_ID}`;
const DOWN_ID = `llama-e2e-down-${RUN_ID}`;
const CREATED_IDS = [PANEL_ID, DELETE_ID, IN_USE_ID, WIZARD_ID, DISCOVERED_ID, DOWN_ID];
/** Discard port on loopback: nothing listens, so a probe of this endpoint is refused at once. */
const DEAD_BASE_URL = 'http://127.0.0.1:9/v1';
/** The role whose policy the in-use test points at an endpoint; restored before the test returns. */
const IN_USE_ROLE = 'analyst';

let double = null;

/** A JSON value that satisfies a JSON-schema node, so the double answers any validation schema. */
function sampleFor(schema) {
	if (!schema || typeof schema !== 'object') return 'ok';
	if (Array.isArray(schema.enum) && schema.enum.length) return schema.enum[0];
	switch (schema.type) {
		case 'object':
			return Object.fromEntries(
				Object.entries(schema.properties ?? {}).map(([name, node]) => [name, sampleFor(node)]),
			);
		case 'array':
			return [];
		case 'boolean':
			return true;
		case 'integer':
		case 'number':
			return 1;
		default:
			return 'ok';
	}
}

/** A llama.cpp router double: loaded/unloaded models, `/props`, `/health` and chat completions. */
function startLlamaDouble(port = 0) {
	return new Promise((resolve, reject) => {
		const server = createServer((incoming, response) => {
			const send = (status, body) => {
				response.writeHead(status, { 'Content-Type': 'application/json' });
				response.end(JSON.stringify(body));
			};
			const path = new URL(incoming.url, 'http://double.invalid').pathname;
			if (incoming.method === 'GET' && path === '/health') return send(200, { status: 'ok' });
			if (incoming.method === 'GET' && path === '/props') {
				return send(200, { role: 'router', max_instances: 1, models_autoload: true });
			}
			if (incoming.method === 'GET' && (path === '/v1/models' || path === '/models')) {
				return send(200, {
					object: 'list',
					data: DOUBLE_MODELS.map((model) => ({
						id: model.id,
						object: 'model',
						owned_by: 'llamacpp',
						status: { value: model.state },
					})),
				});
			}
			if (incoming.method === 'POST' && path === '/v1/chat/completions') {
				let raw = '';
				incoming.on('data', (chunk) => {
					raw += chunk;
				});
				incoming.on('end', () => {
					const request = JSON.parse(raw || '{}');
					const schema = request.response_format?.json_schema?.schema;
					send(200, {
						id: 'chatcmpl-double',
						object: 'chat.completion',
						model: request.model,
						choices: [
							{
								index: 0,
								finish_reason: 'stop',
								message: {
									role: 'assistant',
									content: JSON.stringify(schema ? sampleFor(schema) : { ok: true }),
								},
							},
						],
						usage: { prompt_tokens: 12, completion_tokens: 6, total_tokens: 18 },
					});
				});
				return undefined;
			}
			return send(404, { error: 'not found' });
		});
		server.once('error', reject);
		server.listen(port, '127.0.0.1', () => {
			resolve({ server, baseUrl: `http://127.0.0.1:${server.address().port}` });
		});
	});
}

function closeServer(server) {
	return new Promise((resolve) => server.close(resolve));
}

async function writeToken(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	return (await handshake.json()).token;
}

/** Registers a local endpoint through the write API so a test starts from a known state. */
async function seedLocalEndpoint(page, { id, baseUrl, sync }) {
	const token = await writeToken(page);
	const created = await page.request.post('/api/v1/local-endpoints', {
		headers: { 'X-Local-Control-Token': token },
		data: { catalogId: 'llama_cpp', instanceId: id, displayName: id, baseUrl },
	});
	expect(created.status()).toBe(201);
	if (!sync) return;
	const synced = await page.request.post(`/api/v1/provider-accounts/${id}/sync-models`, {
		headers: { 'X-Local-Control-Token': token },
	});
	expect(synced.status()).toBe(200);
}

async function rolePolicy(page, role) {
	const response = await page.request.get('/api/v1/model-gateway/role-policies');
	return (await response.json()).rolePolicies.find((policy) => policy.role === role);
}

async function setRolePreferred(page, policy, preferred) {
	const token = await writeToken(page);
	const response = await page.request.patch(`/api/v1/model-gateway/role-policies/${policy.id}`, {
		headers: { 'X-Local-Control-Token': token },
		data: { preferred },
	});
	expect(response.status()).toBe(200);
}

async function waitForControlPlane(page) {
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
}

/** Opens Settings at Providers & CLI and returns the Settings dialog once Local endpoints loaded. */
async function openProvidersSettings(page) {
	await page.goto('/#settings-runtime');
	await waitForControlPlane(page);
	const settings = page.getByRole('dialog', { name: 'Settings' });
	await expect(settings).toBeVisible();
	await expect(settings.getByText('Local endpoints', { exact: true })).toBeVisible();
	return settings;
}

function localCard(settings, endpointId) {
	return settings.locator('.card').filter({ hasText: endpointId });
}

test.beforeAll(async () => {
	double = await startLlamaDouble();
});

test.afterAll(async ({ playwright }, testInfo) => {
	const context = await playwright.request.newContext({ baseURL: testInfo.project.use.baseURL });
	try {
		const token = (await (await context.get('/api/v1/security/handshake')).json()).token;
		for (const id of CREATED_IDS) {
			await context.delete(`/api/v1/local-endpoints/${id}`, {
				headers: { 'X-Local-Control-Token': token },
			});
		}
	} finally {
		await context.dispose();
	}
	await closeServer(double.server);
});

test('Local endpoints: probing a synced server shows its health, loaded model and model count', async ({
	page,
}) => {
	await seedLocalEndpoint(page, { id: PANEL_ID, baseUrl: `${double.baseUrl}/v1`, sync: true });
	const settings = await openProvidersSettings(page);
	const card = localCard(settings, PANEL_ID);
	await expect(card).toBeVisible();
	await expect(card).toContainText('llama.cpp');
	await expect(card).toContainText(`${double.baseUrl}/v1`);
	await expect(card).toContainText('this machine');

	await card.getByRole('button', { name: 'Probe' }).click();

	await expect(page.getByText('Endpoint responded')).toBeVisible();
	await expect(card).toContainText('healthy');
	await expect(card.getByText('qwen3-8b', { exact: true })).toBeVisible();
	await expect(card.locator('dd.tnum')).toHaveText(String(DOUBLE_MODELS.length));

	// A disabled endpoint has no tracked load state (the backend omits loadedModels): never "none loaded".
	await card.getByRole('button', { name: 'Edit' }).click();
	const edit = page.getByRole('dialog', { name: 'Edit local endpoint' });
	await edit.getByLabel('Enable this endpoint').uncheck();
	await edit.getByRole('button', { name: 'Save changes' }).click();
	await expect(edit).toBeHidden();
	await expect(card).toContainText('disabled');
	await expect(card).toContainText('not tracked');
});

test('Local endpoints: deleting an unused endpoint removes its card and its account', async ({
	page,
}) => {
	await seedLocalEndpoint(page, { id: DELETE_ID, baseUrl: `${double.baseUrl}/v1`, sync: false });
	const settings = await openProvidersSettings(page);
	const card = localCard(settings, DELETE_ID);
	await expect(card).toBeVisible();

	await card.getByRole('button', { name: 'Delete', exact: true }).click();
	const dialog = page.getByRole('dialog', { name: 'Delete local endpoint' });
	await dialog.getByRole('button', { name: 'Delete endpoint' }).click();

	await expect(dialog).toBeHidden();
	await expect(card).toBeHidden();
	const listed = await (await page.request.get('/api/v1/local-endpoints')).json();
	expect(listed.endpoints.map((endpoint) => endpoint.id)).not.toContain(DELETE_ID);
});

test('Local endpoints: deleting an endpoint a role policy uses lists the reference and keeps it', async ({
	page,
}) => {
	await seedLocalEndpoint(page, { id: IN_USE_ID, baseUrl: `${double.baseUrl}/v1`, sync: true });
	const policy = await rolePolicy(page, IN_USE_ROLE);
	await setRolePreferred(page, policy, [
		{ provider: IN_USE_ID, model: DOUBLE_MODELS[0].id },
		...policy.preferred,
	]);
	try {
		const settings = await openProvidersSettings(page);
		const card = localCard(settings, IN_USE_ID);
		await card.getByRole('button', { name: 'Delete', exact: true }).click();
		const dialog = page.getByRole('dialog', { name: 'Delete local endpoint' });
		await dialog.getByRole('button', { name: 'Delete endpoint' }).click();

		await expect(dialog.getByText('This endpoint is still in use')).toBeVisible();
		await expect(dialog).toContainText('Role policy');
		await expect(dialog).toContainText(IN_USE_ROLE);
		await expect(dialog.getByRole('button', { name: 'Delete endpoint' })).toBeDisabled();
		await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
		await expect(card).toBeVisible();
	} finally {
		await setRolePreferred(page, policy, policy.preferred);
	}
});

test('Runtime access: Providers & CLI shows the local runtimes switch and saves the per-call ceiling', async ({
	page,
}) => {
	const token = await writeToken(page);
	await page.goto('/#settings-runtime');
	await waitForControlPlane(page);
	const settings = page.getByRole('dialog', { name: 'Settings' });
	await expect(
		settings.getByRole('checkbox', { name: 'Local model runtimes (platform-wide)' }),
	).toBeVisible();
	const ceiling = settings.getByLabel('Max seconds per local model call');
	await expect(ceiling).toBeVisible();
	try {
		await ceiling.fill('120');
		await settings
			.locator('.setting-row')
			.filter({ hasText: 'Max seconds per local model call' })
			.getByRole('button', { name: 'Save', exact: true })
			.click();
		await expect
			.poll(async () => {
				const general = (await (await page.request.get('/api/v1/settings')).json()).general;
				return general.find((item) => item.key === 'runtime.local.maxCallSeconds').value;
			})
			.toBe(120);
	} finally {
		const cleared = await page.request.delete(
			'/api/v1/settings/runtime.local.maxCallSeconds?scope=general',
			{ headers: { 'X-Local-Control-Token': token } },
		);
		expect(cleared.status()).toBe(204);
	}
});

test('Local runtime wizard: llama.cpp is added with an editable URL, a default model and a real validation', async ({
	page,
}) => {
	const settings = await openProvidersSettings(page);
	await settings.getByRole('button', { name: 'Add provider' }).click();
	const providerWizard = settings.getByRole('region', { name: 'Add provider' });
	await providerWizard.getByLabel('Provider', { exact: true }).selectOption('llama_cpp');
	await providerWizard.getByRole('button', { name: 'Next' }).click();

	const wizard = settings.getByRole('region', { name: 'Set up a local runtime' });
	await expect(wizard).toBeVisible();
	const baseUrl = wizard.getByLabel('Base URL');
	await expect(baseUrl).toHaveValue('http://127.0.0.1:8082/v1');
	await baseUrl.fill('http://203.0.113.10:8082/v1');
	await expect(wizard.getByText('Not a loopback host')).toBeVisible();
	await expect(wizard.getByLabel('Runs on this machine (WSL/Docker)')).toBeVisible();
	await baseUrl.fill(`${double.baseUrl}/v1`);
	await expect(wizard.getByText('Not a loopback host')).toBeHidden();
	await wizard.getByLabel('Instance name').fill(WIZARD_ID);
	await wizard.getByRole('button', { name: 'Next' }).click();

	const loadedRow = wizard.locator('.local-model-row[data-model="qwen3-8b"]');
	const unloadedRow = wizard.locator('.local-model-row[data-model="gemma-3-4b"]');
	await expect(loadedRow).toContainText('loaded', { timeout: 30_000 });
	await expect(unloadedRow).toContainText('unloaded');
	// Synced models start enabled (P19); the operator only unticks the ones AIDO must not use.
	await expect(loadedRow.getByLabel('Enabled')).toBeChecked();
	const unloadedEnabled = unloadedRow.getByLabel('Enabled');
	await expect(unloadedEnabled).toBeChecked();
	await unloadedEnabled.click();
	await expect(unloadedEnabled).not.toBeChecked();
	await wizard.getByLabel('Default model').selectOption('qwen3-8b');
	await expect(loadedRow).toContainText('default');
	await wizard.getByRole('button', { name: 'Next' }).click();

	await wizard.getByRole('button', { name: 'Validate default model' }).click();
	await expect(wizard.getByRole('status')).toContainText('Validated', { timeout: 60_000 });
	await wizard.getByRole('button', { name: 'Next' }).click();
	await expect(wizard).toContainText(WIZARD_ID);
	await wizard.getByRole('button', { name: 'Finish' }).click();

	await expect(wizard).toBeHidden();
	await expect(localCard(settings, WIZARD_ID)).toBeVisible();
	const listed = await (await page.request.get('/api/v1/local-endpoints')).json();
	const created = listed.endpoints.find((endpoint) => endpoint.id === WIZARD_ID);
	expect(created.baseUrl).toBe(`${double.baseUrl}/v1`);
	expect(created.models.find((model) => model.model === 'qwen3-8b')).toMatchObject({
		isDefault: true,
		validated: true,
	});
	expect(created.models.find((model) => model.model === 'gemma-3-4b').enabled).toBe(false);
});

test('Local runtime wizard: the llama.cpp catalog card opens the local wizard directly', async ({
	page,
}) => {
	const settings = await openProvidersSettings(page);
	const card = settings
		.locator('article.card')
		.filter({ has: page.locator('h4.card-title', { hasText: /^llama\.cpp$/ }) });
	await card.getByRole('button', { name: 'Configure' }).click();

	const wizard = settings.getByRole('region', { name: 'Set up a local runtime' });
	await expect(wizard).toBeVisible();
	await expect(settings.getByRole('region', { name: 'Add provider' })).toBeHidden();
	await expect(wizard.getByLabel('Base URL')).toHaveValue('http://127.0.0.1:8082/v1');
	await wizard.getByRole('button', { name: 'Cancel' }).click();
	await expect(wizard).toBeHidden();
});
