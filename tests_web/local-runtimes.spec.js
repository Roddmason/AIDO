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
	// A failed beforeAll leaves no double: guard it so its real error is the one reported.
	if (double) await closeServer(double.server);
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
	// Exact badge text: a substring match on 'loaded' would also pass on an 'unloaded' row.
	await expect(loadedRow.getByText('loaded', { exact: true })).toBeVisible({ timeout: 30_000 });
	await expect(unloadedRow.getByText('unloaded', { exact: true })).toBeVisible();
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

/** The real discovery scans fixed loopback ports (the host's own llama-server included): intercept it. */
async function interceptDiscovery(page, suggestion) {
	await page.route('**/api/v1/local-runtimes/discover', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ suggestions: [suggestion] }),
		}),
	);
}

function escapeRegExp(value) {
	return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

test('Local runtime discovery: a llama.cpp suggestion opens the wizard prefilled', async ({ page }) => {
	await interceptDiscovery(page, {
		catalogId: 'llama_cpp',
		baseUrl: `${double.baseUrl}/v1`,
		server: 'llama_cpp',
		models: DOUBLE_MODELS.map((model) => model.id),
		alreadyConfigured: false,
		requiresConfirmation: false,
	});
	const settings = await openProvidersSettings(page);
	await settings.getByRole('button', { name: 'Detect local runtimes' }).click();
	const found = settings.getByRole('region', { name: 'Local runtimes found' });
	const suggestion = found.locator('article').filter({ hasText: double.baseUrl });
	await expect(suggestion).toBeVisible({ timeout: 30_000 });
	await expect(suggestion).toContainText('llama.cpp');
	await expect(suggestion).toContainText(`${DOUBLE_MODELS.length} models listed`);
	await suggestion.getByRole('button', { name: 'Set up' }).click();

	const wizard = settings.getByRole('region', { name: 'Set up a local runtime' });
	await expect(wizard.getByLabel('Base URL')).toHaveValue(new RegExp(`^${escapeRegExp(double.baseUrl)}`));
	await wizard.getByLabel('Instance name').fill(DISCOVERED_ID);
	await wizard.getByRole('button', { name: 'Next' }).click();
	await expect(
		wizard.locator('.local-model-row[data-model="qwen3-8b"]').getByText('loaded', { exact: true }),
	).toBeVisible({ timeout: 30_000 });
	await wizard.getByRole('button', { name: 'Close local runtime setup' }).click();

	await expect(wizard).toBeHidden();
	await expect(localCard(settings, DISCOVERED_ID)).toBeVisible();
});

test('Local runtime discovery: an unrecognized server needs confirmation before the wizard opens', async ({
	page,
}) => {
	await interceptDiscovery(page, {
		catalogId: 'local_openai_compatible',
		baseUrl: `${double.baseUrl}/v1`,
		server: 'unknown_openai_compatible',
		models: DOUBLE_MODELS.map((model) => model.id),
		alreadyConfigured: false,
		requiresConfirmation: true,
	});
	const settings = await openProvidersSettings(page);
	await settings.getByRole('button', { name: 'Detect local runtimes' }).click();
	const found = settings.getByRole('region', { name: 'Local runtimes found' });
	const suggestion = found.locator('article').filter({ hasText: 'Unrecognized OpenAI-compatible server' });
	await expect(suggestion).toBeVisible({ timeout: 30_000 });
	const setUp = suggestion.getByRole('button', { name: 'Set up' });
	await expect(setUp).toBeDisabled();
	await suggestion.getByLabel('I confirm this is an OpenAI-compatible server I run and trust').check();
	await expect(setUp).toBeEnabled();
	await setUp.click();

	const wizard = settings.getByRole('region', { name: 'Set up a local runtime' });
	await expect(wizard.getByRole('heading', { level: 3 })).toContainText('Local OpenAI-compatible server');
	await expect(wizard.getByLabel('Base URL')).toHaveValue(new RegExp(`^${escapeRegExp(double.baseUrl)}`));
	await wizard.getByRole('button', { name: 'Close local runtime setup' }).click();
	await expect(wizard).toBeHidden();
});

const VALIDATED = {
	status: 'validated',
	checkedAt: '2026-09-23T10:00:00+00:00',
	latencyMs: 380,
	model: 'qwen3-8b',
	reason: null,
};
const NO_SPLIT = { product_owner: null, developer: null, architect: null, security: null };
const TEAM_LOCAL = {
	providerId: 'llama-lab',
	label: 'llama.cpp lab',
	kind: 'local',
	validation: VALIDATED,
	eligibleRoles: ['product_owner', 'developer', 'architect', 'security'],
	loadedModels: ['qwen3-8b'],
};

async function openNewThreadTeamPanel(page) {
	await page.goto('/#threads');
	await waitForControlPlane(page);
	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();
	await page.getByRole('button', { name: /AI team/ }).click();
	const panel = page.getByRole('dialog', { name: 'AI team for this thread' });
	await expect(panel).toBeVisible();
	return panel;
}

test('Thread team: a local runtime candidate shows its loaded model and the model resolved per role', async ({
	page,
}) => {
	await page.route('**/api/v1/runtime/team-candidates**', async (route) => {
		const selected = new URL(route.request().url()).searchParams.get('selected') ?? '';
		const chosen = selected.split(',').includes(TEAM_LOCAL.providerId);
		await route.fulfill({
			json: {
				candidates: [TEAM_LOCAL],
				freshnessSeconds: 1800,
				suggestedRoleRuntimes: chosen
					? {
							product_owner: TEAM_LOCAL.providerId,
							developer: TEAM_LOCAL.providerId,
							architect: TEAM_LOCAL.providerId,
							security: null,
						}
					: NO_SPLIT,
				suggestedRoleModels: chosen
					? { product_owner: 'qwen3-8b', developer: 'qwen3-8b', architect: 'gemma-3-4b' }
					: {},
			},
		});
	});
	try {
		const panel = await openNewThreadTeamPanel(page);
		const row = panel.locator('.runtime-team-row').filter({ hasText: TEAM_LOCAL.label });
		await expect(row).toContainText('Loaded model: qwen3-8b');

		await panel.getByRole('checkbox', { name: /llama\.cpp lab/ }).check();

		await expect(panel.getByRole('combobox', { name: 'Product Owner' })).toHaveValue(
			TEAM_LOCAL.providerId,
		);
		await expect(panel.getByText('Resolved model: qwen3-8b')).toHaveCount(2);
		await expect(panel.getByText('Resolved model: gemma-3-4b')).toBeVisible();
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Thread timeline: a local model switch reads as from and to model, role and reason', async ({
	page,
}) => {
	const { projects } = await (await page.request.get('/api/v1/projects')).json();
	const project = projects.find((entry) => entry.status === 'active');
	expect(project).toBeTruthy();
	const thread = {
		id: `thread-local-switch-${RUN_ID}`,
		projectId: project.id,
		ownerId: project.id,
		ownerType: 'workspace',
		title: `Local model switch ${RUN_ID}`,
		summary: '',
		status: 'queued',
		metadata: {},
		createdAt: '2026-09-23T00:00:00Z',
		updatedAt: '2026-09-23T00:00:00Z',
	};
	const event = (sequence, type, payload) => ({
		id: `local-switch-${sequence}`,
		threadId: thread.id,
		projectId: project.id,
		sequence,
		type,
		payload,
		metadata: {},
		createdAt: thread.createdAt,
	});
	const loopId = `loop-local-switch-${RUN_ID}`;
	const events = [
		event(1, 'run_queued', {}),
		event(2, 'local_model_switch', {
			loopId,
			runtimeId: 'llama-lab',
			fromModel: 'gemma-3-4b',
			toModel: 'qwen3-8b',
			role: 'product_owner',
			reason: 'default',
		}),
		event(3, 'local_model_switch', {
			loopId,
			runtimeId: 'llama-lab',
			fromModel: 'qwen3-8b',
			toModel: 'gemma-3-4b',
			role: 'developer',
			reason: 'sealed',
		}),
		event(4, 'local_model_switch', {
			loopId,
			runtimeId: 'llama-lab',
			fromModel: null,
			toModel: 'glm-4.7-flash',
			role: 'architect',
			reason: 'warm_pool',
		}),
	];
	await page.route('**/api/v1/overview', async (route) => {
		const response = await route.fetch();
		const overview = await response.json();
		await route.fulfill({ response, json: { ...overview, threads: [...overview.threads, thread] } });
	});
	await page.route(`**/api/v1/threads/${thread.id}`, (route) =>
		route.fulfill({
			json: { thread, messages: [], decisions: [], artifacts: [], events: [] },
		}),
	);
	await page.route(`**/api/v1/threads/${thread.id}/remediations`, (route) =>
		route.fulfill({ json: { remediations: [] } }),
	);
	await page.route(`**/api/v1/threads/${thread.id}/events?*`, (route) => {
		const afterSeq = Number(new URL(route.request().url()).searchParams.get('afterSeq'));
		return route.fulfill({
			json: {
				events: events.filter((item) => item.sequence > afterSeq),
				lastSeq: events.at(-1).sequence,
				running: true,
				threadStatus: 'queued',
			},
		});
	});
	try {
		await page.goto('/#threads');
		await waitForControlPlane(page);
		const threadButton = page.getByRole('button', { name: thread.title, exact: true });
		if (!(await threadButton.isVisible())) {
			await page.locator('.thread-workspace-head').filter({ hasText: project.name }).click();
		}
		await threadButton.click();
		const execution = page.getByRole('complementary', { name: 'Execution console' });
		const rows = execution.locator('.thread-console-row[data-type="local_model_switch"]');
		const row = rows.filter({ hasText: 'gemma-3-4b -> qwen3-8b' });

		await expect(row).toContainText('Local model switched');
		await expect(row).toContainText('Role: product_owner');
		await expect(row).toContainText("Runtime's default model");
		await expect(row).toContainText('llama-lab');
		await expect(rows.filter({ hasText: 'qwen3-8b -> gemma-3-4b' })).toContainText(
			'Model sealed for this role in the thread team',
		);
		await expect(rows.filter({ hasText: 'unknown model -> glm-4.7-flash' })).toContainText('warm_pool');
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

const DOWN_LOCAL_PROVIDER = {
	id: DOWN_ID,
	kind: 'local',
	displayName: DOWN_ID,
	installed: true,
	detected: true,
	configured: true,
	authenticated: true,
	available: false,
	executable: false,
	blockerType: 'runtime_not_executable',
	reason: 'local_server_unreachable: connection refused',
	lastError: 'local_server_unreachable: connection refused',
	healthStatus: 'offline',
	capabilities: ['chat'],
	requiredConfiguration: [],
	requiresApproval: false,
	version: null,
	detectedCommand: null,
	loginCommand: '',
};

test('AI health: a local runtime whose server is down explains the cause and opens its setup wizard', async ({
	page,
}) => {
	await seedLocalEndpoint(page, { id: DOWN_ID, baseUrl: DEAD_BASE_URL, sync: false });
	await page.route('**/api/v1/runtime/providers', async (route) => {
		const response = await route.fetch();
		const payload = await response.json();
		const others = payload.providers.filter((provider) => provider.id !== DOWN_ID);
		await route.fulfill({
			response,
			json: { ...payload, providers: [...others, DOWN_LOCAL_PROVIDER] },
		});
	});
	try {
		await page.goto('/#threads');
		await waitForControlPlane(page);
		await page.getByRole('button', { name: /need attention/ }).click();
		const modal = page.getByRole('dialog', { name: 'AI health' });
		const card = modal
			.locator('.thread-remediation-card')
			.filter({ hasText: DOWN_LOCAL_PROVIDER.displayName });

		await expect(card).toContainText('The local server is not answering');
		await expect(card).toContainText('.wslconfig');
		await expect(card).toContainText('(connection refused)');
		await card.locator('.thread-remediation-primary').click();

		const settings = page.getByRole('dialog', { name: 'Settings' });
		const wizard = settings.getByRole('region', { name: 'Set up a local runtime' });
		await expect(wizard).toBeVisible();
		await expect(wizard.getByLabel('Base URL')).toHaveValue(DEAD_BASE_URL);
		await expect(wizard).toContainText(DOWN_ID);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});
