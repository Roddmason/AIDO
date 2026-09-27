import { expect, test } from './fixtures/operations.js';

/**
 * Add-provider wizard: the operator is asked only for what the chosen provider actually needs.
 * A provider with a preconfigured endpoint asks for the API key alone; a remote or custom endpoint
 * asks for the URL; an optional-token provider asks for nothing. The API key the operator types is
 * masked and never serialized back into the page, not even on the validation-error path.
 *
 * Most cases stop before the credential is persisted, so they run against reads alone. The
 * reopen-an-existing-account case is the exception: it seeds a configured provider account through
 * the real write API and restores it afterwards, because the bug it pins (the wizard demanding the
 * API key again, and failing to save, when the operator only wanted to change something else) only
 * exists once an account already holds a credential the vault will never hand back.
 */

const SECRET = 'sk-e2e-secret-never-rendered-0123456789';
/** No `sk-` substring: secret redaction rewrites those and would corrupt assertions on this ref. */
const SEEDED_CREDENTIAL_REF = 'env:AIDO_E2E_GEMINI_KEY';

async function mockWizardModelCatalog(page, models, onPatch) {
	await page.route('/api/v1/provider-accounts/from-catalog', (route) => route.fulfill({ json: {} }));
	await page.route('/api/v1/model-gateway/role-policies', (route) =>
		route.fulfill({ json: { rolePolicies: [] } }),
	);
	await page.route('/api/v1/provider-accounts/ollama/sync-models', (route) =>
		route.fulfill({ json: { models } }),
	);
	await page.route('**/api/v1/model-gateway/models/*', async (route) => {
		if (route.request().method() !== 'PATCH') return route.continue();
		const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-1));
		const body = route.request().postDataJSON();
		const model = models.find((entry) => entry.id === id);
		if (onPatch && !onPatch(id, body.enabled)) {
			await route.fulfill({ status: 500, json: { detail: 'model_update_failed' } });
			return;
		}
		Object.assign(model, body);
		await route.fulfill({ json: { model } });
	});
}

async function openModelSelection(page) {
	const wizard = await openWizard(page);
	await chooseProvider(wizard, 'ollama');
	await wizard.getByRole('button', { name: 'Next', exact: true }).click();
	await wizard.getByRole('button', { name: 'Sync models', exact: true }).click();
	return wizard;
}

async function finishModelSelection(wizard) {
	await wizard.getByRole('button', { name: 'Next', exact: true }).click();
	await wizard.getByRole('button', { name: 'Next', exact: true }).click();
	await wizard.getByRole('button', { name: 'Save & finish', exact: true }).click();
}

test('Configure provider: model selection survives sync and saves only changed enabled flags', async ({ page }) => {
	const model = (id, enabled) => ({ id, providerId: 'ollama', model: id, enabled, freeTier: true });
	const models = [model('enabled-first', true), model('disabled-second', false), model('unchanged', true)];
	const writes = [];
	await mockWizardModelCatalog(page, models, (id, enabled) => {
		writes.push({ id, enabled });
		return true;
	});
	try {
		let wizard = await openModelSelection(page);
		await expect(wizard.getByRole('checkbox', { name: 'enabled-first', exact: true })).toBeChecked();
		await expect(wizard.getByRole('checkbox', { name: 'disabled-second', exact: true })).not.toBeChecked();
		await wizard.getByRole('checkbox', { name: 'enabled-first', exact: true }).uncheck();
		await wizard.getByRole('checkbox', { name: 'disabled-second', exact: true }).check();
		models.push(model('new-disabled', false));
		await wizard.getByRole('button', { name: 'Sync models', exact: true }).click();
		await expect(wizard.getByText('2/4 selected', { exact: true })).toBeVisible();
		await expect(wizard.getByRole('checkbox', { name: 'enabled-first', exact: true })).not.toBeChecked();
		await expect(wizard.getByRole('checkbox', { name: 'disabled-second', exact: true })).toBeChecked();
		await expect(wizard.getByRole('checkbox', { name: 'new-disabled', exact: true })).not.toBeChecked();
		await finishModelSelection(wizard);
		await expect(wizard).toBeHidden();
		expect(writes).toEqual([
			{ id: 'enabled-first', enabled: false },
			{ id: 'disabled-second', enabled: true },
		]);

		wizard = await openModelSelection(page);
		await expect(wizard.getByRole('checkbox', { name: 'enabled-first', exact: true })).not.toBeChecked();
		await expect(wizard.getByRole('checkbox', { name: 'disabled-second', exact: true })).toBeChecked();
		await finishModelSelection(wizard);
		await expect(wizard).toBeHidden();
		expect(writes).toHaveLength(2);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Configure provider: a failed model save stays open and retries only unsaved changes', async ({ page }) => {
	const models = [
		{ id: 'enable-first', providerId: 'ollama', model: 'enable-first', enabled: false, freeTier: true },
		{ id: 'disable-second', providerId: 'ollama', model: 'disable-second', enabled: true, freeTier: true },
	];
	let failSave = true;
	const writes = [];
	await mockWizardModelCatalog(page, models, (id, enabled) => {
		writes.push({ id, enabled });
		return !(id === 'disable-second' && failSave);
	});
	try {
		const wizard = await openModelSelection(page);
		await wizard.getByRole('checkbox', { name: 'enable-first', exact: true }).check();
		await wizard.getByRole('checkbox', { name: 'disable-second', exact: true }).uncheck();
		await finishModelSelection(wizard);
		await expect(wizard.getByRole('alert')).toContainText('model_update_failed');
		await expect(wizard).toBeVisible();
		expect(writes).toEqual([
			{ id: 'enable-first', enabled: true },
			{ id: 'disable-second', enabled: false },
		]);
		failSave = false;
		await wizard.getByRole('button', { name: 'Save & finish', exact: true }).click();
		await expect(wizard).toBeHidden();
		expect(writes).toEqual([
			{ id: 'enable-first', enabled: true },
			{ id: 'disable-second', enabled: false },
			{ id: 'disable-second', enabled: false },
		]);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

/** Opens Settings at "Providers & CLI" and returns the Add-provider wizard dialog. */
async function openWizard(page) {
	await page.goto('/#settings-runtime');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const settings = page.getByRole('dialog', { name: 'Settings' });
	await expect(settings).toBeVisible();
	await settings.getByRole('button', { name: 'Add provider' }).click();
	const wizard = page.getByRole('region', { name: 'Add provider' });
	await expect(wizard).toBeVisible();
	return wizard;
}

/** Picks a catalog provider on step 01 and advances to the credential step. */
async function chooseProvider(wizard, providerId) {
	await wizard.getByLabel('Provider', { exact: true }).selectOption(providerId);
	await wizard.getByRole('button', { name: 'Next' }).click();
}

/** Opens Settings and returns the settings dialog, without touching the wizard. */
async function openSettings(page) {
	await page.goto('/#settings-runtime');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const settings = page.getByRole('dialog', { name: 'Settings' });
	await expect(settings).toBeVisible();
	return settings;
}

/** Reopens the wizard on an already-configured provider card (the edit path, not the create path). */
async function openConfigureWizard(page, displayName) {
	const settings = await openSettings(page);
	const card = settings.locator('.card').filter({ hasText: displayName });
	await expect(card.first()).toBeVisible({ timeout: 30_000 });
	await card.first().getByRole('button', { name: 'Configure' }).click();
	const wizard = page.getByRole('region', { name: 'Add provider' });
	await expect(wizard).toBeVisible();
	return wizard;
}

async function patchGemini(page, token, data) {
	const response = await page.request.patch('/api/v1/model-gateway/providers/gemini', {
		headers: { 'X-Local-Control-Token': token },
		data,
	});
	expect(response.ok()).toBeTruthy();
}

test('Add provider: DeepSeek asks only for an API key, its endpoint is preconfigured', async ({
	page,
}) => {
	const wizard = await openWizard(page);
	await wizard.getByLabel('Provider', { exact: true }).selectOption('deepseek');

	// Step 01 states the endpoint, the declared capabilities and what will be asked for.
	await expect(wizard.getByText('https://api.deepseek.com', { exact: true })).toBeVisible();
	await expect(wizard.getByText('API key required', { exact: true })).toBeVisible();
	await expect(wizard.getByText('reasoning', { exact: true })).toBeVisible();

	await wizard.getByRole('button', { name: 'Next' }).click();

	// Step 02 asks for the key and nothing else: there is no Base URL input to fill in.
	await expect(wizard.getByLabel('API key')).toBeVisible();
	await expect(wizard.getByRole('textbox', { name: 'Base URL' })).toHaveCount(0);
	await expect(wizard.getByText('Preconfigured — no URL needed.')).toBeVisible();
	await expect(wizard.getByText('https://api.deepseek.com', { exact: true })).toBeVisible();
});

test('Add provider: Ollama remote asks for the URL and treats the token as optional', async ({
	page,
}) => {
	const wizard = await openWizard(page);
	await wizard.getByLabel('Provider', { exact: true }).selectOption('ollama_remote');

	await expect(wizard.getByText('You provide the endpoint', { exact: true })).toBeVisible();
	await expect(wizard.getByText('Token optional', { exact: true })).toBeVisible();

	await wizard.getByRole('button', { name: 'Next' }).click();

	// The endpoint is required and empty; the credential defaults to "none", so no key field shows.
	const baseUrl = wizard.getByRole('textbox', { name: 'Base URL' });
	await expect(baseUrl).toBeVisible();
	await expect(baseUrl).toHaveValue('');
	await expect(wizard.getByRole('radio', { name: 'No credential' })).toHaveAttribute(
		'aria-checked',
		'true',
	);
	await expect(wizard.getByLabel('API key')).toHaveCount(0);

	// Advancing without the endpoint is refused before any write leaves the browser.
	await wizard.getByRole('button', { name: 'Next' }).click();
	await expect(wizard.getByRole('alert')).toHaveText('Enter the provider base URL.');
});

test('Add provider: the custom OpenAI-compatible provider asks for a base URL', async ({ page }) => {
	const wizard = await openWizard(page);
	await wizard.getByLabel('Provider', { exact: true }).selectOption('openai_compatible');

	await expect(wizard.getByText('You provide the endpoint', { exact: true })).toBeVisible();

	await wizard.getByRole('button', { name: 'Next' }).click();

	const baseUrl = wizard.getByRole('textbox', { name: 'Base URL' });
	await expect(baseUrl).toBeVisible();
	await expect(baseUrl).toHaveValue('');
	await expect(wizard.getByLabel('API key')).toBeVisible();
});

test('Add provider: the API key is masked and never rendered back into the page', async ({
	page,
}) => {
	const wizard = await openWizard(page);
	// The custom provider needs a base URL, so leaving it blank exercises the error path with a
	// secret already typed — the classic place a wizard leaks the key back into the DOM.
	await chooseProvider(wizard, 'openai_compatible');

	const apiKey = wizard.getByLabel('API key');
	await apiKey.fill(SECRET);
	await expect(apiKey).toHaveAttribute('type', 'password');
	await expect(apiKey).toHaveAttribute('autocomplete', 'new-password');
	expect(await page.content()).not.toContain(SECRET);

	await wizard.getByRole('button', { name: 'Next' }).click();
	const alert = wizard.getByRole('alert');
	await expect(alert).toHaveText('Enter the provider base URL.');
	expect(await alert.textContent()).not.toContain(SECRET);
	expect(await page.content()).not.toContain(SECRET);

	// Switching to an existing credential reference must not pre-fill it with the typed secret.
	await wizard.getByRole('radio', { name: 'Reference' }).click();
	await expect(wizard.getByLabel('Credential reference')).toHaveValue('');
	await expect(wizard.getByLabel('API key')).toHaveCount(0);
	expect(await page.content()).not.toContain(SECRET);
});

test('Add provider: the last step routes the chosen model to the selected roles', async ({
	page,
}) => {
	const model = 'llama3.1:8b';
	const token = (await (await page.request.get('/api/v1/security/handshake')).json()).token;
	const policiesUrl = '/api/v1/model-gateway/role-policies';
	const before = (await (await page.request.get(policiesUrl)).json()).rolePolicies;
	// Ollama already routes to some roles, so the wizard must pre-check exactly those.
	const preselected = before.filter((policy) =>
		policy.preferred.some((candidate) => candidate.provider === 'ollama'),
	);

	// Only the provider-facing calls are stubbed; the role write goes to the real control plane.
	await page.route('/api/v1/provider-accounts/from-catalog', (route) => route.fulfill({ json: {} }));
	await page.route('/api/v1/provider-accounts/ollama/sync-models', (route) =>
		route.fulfill({
			json: {
				models: [
					{ id: 'ollama:llama31', providerId: 'ollama', model, freeTier: true, enabled: true },
				],
			},
		}),
	);

	const wizard = await openWizard(page);
	// Ollama local needs no credential and no endpoint: step 02 asks for nothing at all.
	await chooseProvider(wizard, 'ollama');
	await expect(wizard.getByText('This provider needs no API key; saving enables it.')).toBeVisible();

	await wizard.getByRole('button', { name: 'Next' }).click();
	await wizard.getByRole('button', { name: 'Sync models' }).click();
	await expect(wizard.getByText('1/1 selected')).toBeVisible();
	await expect(wizard.getByText('free tier', { exact: true })).toBeVisible();

	await wizard.getByRole('button', { name: 'Next' }).click(); // models  -> validate
	await wizard.getByRole('button', { name: 'Next' }).click(); // validate -> roles

	expect(preselected.length).toBeGreaterThan(1);
	const assigned = preselected[0].role;
	const removed = preselected[preselected.length - 1].role;
	await expect(wizard.getByRole('checkbox', { name: assigned, exact: true })).toBeChecked();
	await wizard.getByRole('checkbox', { name: removed, exact: true }).uncheck();
	await wizard.getByRole('button', { name: 'Save & finish' }).click();
	await expect(wizard).toBeHidden();

	const after = (await (await page.request.get(policiesUrl)).json()).rolePolicies;
	const byRole = (policies, role) => policies.find((policy) => policy.role === role);
	const withoutOllama = (policy) => policy.preferred.filter((c) => c.provider !== 'ollama');

	// The kept role prefers the chosen model first; its other candidates ride along unchanged.
	expect(byRole(after, assigned).preferred[0]).toEqual({ provider: 'ollama', model });
	expect(byRole(after, assigned).preferred.slice(1)).toEqual(withoutOllama(byRole(before, assigned)));
	// The cleared role stops routing here, and loses nothing else.
	expect(byRole(after, removed).preferred).toEqual(withoutOllama(byRole(before, removed)));
	// Roles that never routed to this provider are left strictly alone — no blanket rewrite.
	for (const policy of before) {
		if (policy.preferred.some((c) => c.provider === 'ollama')) continue;
		expect(byRole(after, policy.role).preferred).toEqual(policy.preferred);
	}

	// Restore the seeded routing so later specs observe the untouched control plane.
	for (const policy of before) {
		await page.request.patch(`${policiesUrl}/${policy.id}`, {
			headers: { 'X-Local-Control-Token': token },
			data: { preferred: policy.preferred },
		});
	}
});

test('Add provider: OmniRoute syncs its models on its own and starts with every role checked', async ({
	page,
}) => {
	const token = (await (await page.request.get('/api/v1/security/handshake')).json()).token;
	const policiesUrl = '/api/v1/model-gateway/role-policies';
	const before = (await (await page.request.get(policiesUrl)).json()).rolePolicies;
	// The seeded control plane routes no role to the gateway yet, so the all-checked default applies.
	expect(
		before.some((policy) => policy.preferred.some((c) => c.provider === 'omniroute')),
	).toBe(false);

	// Switch the platform-wide remote kill switch off: the wizard must surface it and only turn it
	// back on with the operator's explicit, pre-checked consent — never silently.
	const disableRemote = await page.request.put('/api/v1/settings/runtime.remote.enabled', {
		headers: { 'X-Local-Control-Token': token },
		data: { scope: 'general', value: false },
	});
	expect(disableRemote.ok()).toBeTruthy();

	// The OmniRoute account write goes to the real control plane on purpose: the role-policy PATCH
	// only accepts candidates whose provider account exists, exactly the ordering the wizard
	// guarantees. Only the gateway-facing sync is stubbed — no OmniRoute process runs during the
	// suite. The ollama leg below is fully stubbed instead: it exists to dirty the wizard's model
	// state before the provider switch, never to persist anything.
	await page.route('/api/v1/provider-accounts/from-catalog', (route) => route.fulfill({ json: {} }));
	await page.route('/api/v1/provider-accounts/ollama/sync-models', (route) =>
		route.fulfill({
			json: {
				models: [
					{ id: 'ollama:llama31', providerId: 'ollama', model: 'llama3.1:8b', freeTier: true, enabled: true },
				],
			},
		}),
	);
	await page.route('/api/v1/provider-accounts/omniroute/sync-models', (route) =>
		route.fulfill({
			json: {
				models: [
					{
						id: 'omniroute:deepseek',
						providerId: 'omniroute',
						model: 'oc/deepseek-v4-flash-free',
						freeTier: true,
						enabled: true,
					},
					{
						id: 'omniroute:pickle',
						providerId: 'omniroute',
						model: 'oc/big-pickle',
						freeTier: true,
						enabled: true,
					},
				],
			},
		}),
	);

	// First leg: sync another provider's models, then switch back — the stale catalog must not
	// survive into the OmniRoute flow (it would suppress the auto-sync and mislabel the models).
	const wizard = await openWizard(page);
	await chooseProvider(wizard, 'ollama');
	await wizard.getByRole('button', { name: 'Next' }).click();
	await wizard.getByRole('button', { name: 'Sync models' }).click();
	await expect(wizard.getByText('1/1 selected')).toBeVisible();
	await wizard.getByRole('button', { name: 'Back' }).click(); // models     -> credential
	await wizard.getByRole('button', { name: 'Back' }).click(); // credential -> provider
	await page.unroute('/api/v1/provider-accounts/from-catalog');

	await chooseProvider(wizard, 'omniroute');
	// Step 02: the local gateway endpoint comes prefilled and the bearer token stays optional.
	await expect(wizard.getByRole('textbox', { name: 'Base URL' })).toHaveValue(
		'http://localhost:20128/v1',
	);
	// The kill switch is off, so the credential step offers — pre-checked — to re-enable it before
	// the models step, where a vetoed sync would otherwise fail.
	await expect(
		wizard.getByRole('checkbox', { name: /Remote APIs are switched off/ }),
	).toBeChecked();
	await wizard.getByRole('button', { name: 'Next' }).click();

	// Step 03 syncs without a click — the gateway owns model choice, the operator only curates —
	// and shows OmniRoute's two models, not the one left over from the ollama leg.
	await expect(wizard.getByText('2/2 selected')).toBeVisible();

	await wizard.getByRole('button', { name: 'Next' }).click(); // models  -> validate
	await wizard.getByRole('button', { name: 'Next' }).click(); // validate -> roles

	// The wildcard leads the model select and every role starts checked.
	await expect(wizard.getByLabel('Model for these roles')).toHaveValue('*');
	for (const policy of before) {
		await expect(wizard.getByRole('checkbox', { name: policy.role, exact: true })).toBeChecked();
	}
	await wizard.getByRole('button', { name: 'Save & finish' }).click();
	await expect(wizard).toBeHidden();

	// Every role now prefers the gateway wildcard first; other candidates ride along unchanged.
	const after = (await (await page.request.get(policiesUrl)).json()).rolePolicies;
	for (const policy of before) {
		const updated = after.find((row) => row.role === policy.role);
		expect(updated.preferred[0]).toEqual({ provider: 'omniroute', model: '*' });
		expect(updated.preferred.slice(1)).toEqual(
			policy.preferred.filter((c) => c.provider !== 'omniroute'),
		);
	}

	// The account carries the remote-endpoint marker: the gateway listens on localhost but forwards
	// prompts to external providers, so it must never pass as local_private work.
	const accounts = (await (await page.request.get('/api/v1/model-gateway/providers')).json())
		.providers;
	expect(accounts.find((provider) => provider.providerId === 'omniroute').metadata.endpointKind).toBe(
		'remote',
	);

	// The consented re-enable was applied: the platform-wide remote flag is back on.
	const settings = (await (await page.request.get('/api/v1/settings')).json()).general;
	expect(settings.find((item) => item.key === 'runtime.remote.enabled').value).toBe(true);

	// Restore the seeded routing and disable the created account so later specs observe the
	// untouched control plane (there is no DELETE endpoint for provider accounts).
	for (const policy of before) {
		await page.request.patch(`${policiesUrl}/${policy.id}`, {
			headers: { 'X-Local-Control-Token': token },
			data: { preferred: policy.preferred },
		});
	}
	await page.request.patch('/api/v1/model-gateway/providers/omniroute', {
		headers: { 'X-Local-Control-Token': token },
		data: { enabled: false },
	});
});

test('Configure provider: an account with a stored credential is edited without re-entering the key', async ({
	page,
}) => {
	// The vault never returns the secret, so a wizard that always demands it makes every other
	// setting of a configured account uneditable. pricingMode 'configured' is deliberate: leaving
	// it 'free' would divert the failure to the free-tier attestation gate and mask this bug.
	const token = (await (await page.request.get('/api/v1/security/handshake')).json()).token;
	await patchGemini(page, token, {
		credentialRef: SEEDED_CREDENTIAL_REF,
		enabled: true,
		pricingMode: 'configured',
	});

	try {
		const seeded = (await (await page.request.get('/api/v1/model-gateway/providers')).json()).providers;
		const gemini = seeded.find((provider) => provider.providerId === 'gemini');
		expect(gemini.credentialRef).toBe(SEEDED_CREDENTIAL_REF);

		const wizard = await openConfigureWizard(page, 'Google Gemini');
		// Reopening resets the wizard to step 01 pre-seeded on this account; do NOT reselect the
		// provider, that would exercise the create path instead of the reopen path under test.
		await wizard.getByRole('button', { name: 'Next' }).click();

		await expect(wizard.getByRole('radio', { name: 'Keep current' })).toHaveAttribute(
			'aria-checked',
			'true',
		);
		await expect(
			wizard.getByText(
				'Keeping the stored credential — change any other setting without re-entering the API key.',
			),
		).toBeVisible();
		// The decisive assertion: no API key is demanded, which is exactly what blocked the operator.
		await expect(wizard.getByLabel('API key')).toHaveCount(0);

		// Changing something else and saving must advance instead of erroring.
		await wizard.getByRole('radio', { name: 'Free tier' }).click();
		await wizard
			.getByRole('checkbox', { name: /I verified in Google AI Studio/ })
			.check();
		await wizard.getByRole('button', { name: 'Next' }).click();
		await expect(wizard.getByRole('alert')).toHaveCount(0);
		await expect(wizard.getByRole('button', { name: 'Sync models' })).toBeVisible();

		// The credential the operator never retyped survived the save.
		const after = (await (await page.request.get('/api/v1/model-gateway/providers')).json()).providers;
		const saved = after.find((provider) => provider.providerId === 'gemini');
		expect(saved.credentialRef).toBe(SEEDED_CREDENTIAL_REF);
		expect(saved.pricingMode).toBe('free');
	} finally {
		// No DELETE endpoint exists and the runner shares one database per chunk, so revert to the
		// migration default or later specs inherit a configured Gemini account.
		await patchGemini(page, token, {
			credentialRef: '',
			enabled: false,
			pricingMode: 'unknown',
		});
	}
});

test('Configure provider: switching to another provider drops the stored credential reference', async ({
	page,
}) => {
	// A credential must never leak across accounts: picking a different provider in step 01 has to
	// clear the reference seeded from the account the wizard was opened on.
	const token = (await (await page.request.get('/api/v1/security/handshake')).json()).token;
	await patchGemini(page, token, {
		credentialRef: SEEDED_CREDENTIAL_REF,
		enabled: true,
		pricingMode: 'configured',
	});

	try {
		const wizard = await openConfigureWizard(page, 'Google Gemini');
		await chooseProvider(wizard, 'deepseek');

		await expect(wizard.getByRole('radio', { name: 'Keep current' })).toHaveCount(0);
		await expect(wizard.getByLabel('API key')).toBeVisible();
		expect(await page.content()).not.toContain(SEEDED_CREDENTIAL_REF);
	} finally {
		await patchGemini(page, token, {
			credentialRef: '',
			enabled: false,
			pricingMode: 'unknown',
		});
	}
});

test('Providers & CLI: each provider card has a switch that patches enabled and the status bar counts active providers', async ({
	page,
}) => {
	const patches = [];
	await page.route('**/api/v1/model-gateway/providers/*', async (route) => {
		if (route.request().method() !== 'PATCH') return route.continue();
		const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-1));
		const body = route.request().postDataJSON();
		patches.push({ id, ...body });
		await route.fulfill({ json: { provider: { providerId: id, enabled: body.enabled } } });
	});
	try {
		const settings = await openSettings(page);
		const card = settings.locator('.card').filter({ hasText: 'Gemini' }).first();
		await expect(card).toBeVisible({ timeout: 30_000 });
		const toggle = card.getByRole('checkbox', { name: 'Use Google Gemini in threads' });
		await expect(toggle).toBeVisible();
		const wasChecked = await toggle.isChecked();
		await toggle.click();
		await expect.poll(() => patches).toEqual([{ id: 'gemini', enabled: !wasChecked }]);
		await expect(page.getByText(/\d+ active providers/)).toBeVisible();
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Providers & CLI: a provider used by a running thread keeps its switch locked as "In use"', async ({ page }) => {
	await page.goto('/#settings-runtime');
	const token = (await (await page.request.get('/api/v1/security/handshake')).json()).token;
	await patchGemini(page, token, { enabled: true });
	await page.route('**/api/v1/runtime/providers*', async (route) => {
		const response = await route.fetch();
		const body = await response.json();
		body.providers = body.providers.map((item) => (item.id === 'gemini' ? { ...item, inUse: true } : item));
		await route.fulfill({ response, json: body });
	});
	try {
		const settings = await openSettings(page);
		const card = settings.locator('.card').filter({ hasText: 'Google Gemini' }).first();
		await expect(card).toBeVisible({ timeout: 30_000 });
		const toggle = card.getByRole('checkbox', { name: 'Use Google Gemini in threads' });
		await expect(toggle).toBeChecked();
		await expect(toggle).toBeDisabled();
		await expect(card.getByText('In use', { exact: true })).toBeVisible();
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Providers & CLI: a machine reason code reads as plain copy and keeps the raw reason in details', async ({
	page,
}) => {
	const raw =
		'provider_disabled: the operator switched this provider off (provider_accounts.enabled is false); no thread, agent or failover uses it until it is switched back on.';
	await page.route('**/api/v1/runtime/providers*', async (route) => {
		const response = await route.fetch();
		const body = await response.json();
		body.providers = body.providers.map((item) => (item.id === 'gemini' ? { ...item, reason: raw } : item));
		await route.fulfill({ response, json: body });
	});
	try {
		const settings = await openSettings(page);
		const card = settings.locator('.card').filter({ hasText: 'Google Gemini' }).first();
		await expect(card).toBeVisible({ timeout: 30_000 });
		const reason = card.locator('.card-reason');
		await expect(reason).toHaveText('Switched off for AIDO. Turn it on to use it in threads.');
		await expect(reason).toHaveAttribute('title', raw);
		// Details stay collapsed until asked for, and then show the reason as the backend reported it.
		await expect(card.getByText(raw, { exact: true })).toBeHidden();
		await card.getByRole('button', { name: 'Configuration details' }).click();
		await expect(card.getByText(raw, { exact: true })).toBeVisible();
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Configure provider: an unresolved seeded placeholder asks for the API key instead of keeping it', async ({
	page,
}) => {
	// NIM is seeded with the bare placeholder NVIDIA_NIM_API_KEY; without that variable it is not a stored
	// credential, and offering "Keep current" hid the key field while NIM rejected every request.
	const token = (await (await page.request.get('/api/v1/security/handshake')).json()).token;
	await page.request.patch('/api/v1/model-gateway/providers/nvidia_nim', {
		headers: { 'X-Local-Control-Token': token },
		data: { credentialRef: 'NVIDIA_NIM_API_KEY' },
	});
	const wizard = await openConfigureWizard(page, 'NVIDIA NIM');
	await wizard.getByRole('button', { name: 'Next' }).click();
	await expect(wizard.getByRole('radio', { name: 'Keep current' })).toHaveCount(0);
	await expect(wizard.getByLabel('API key')).toBeVisible();
});

test('Configure provider: a reference AIDO cannot read stops at the credential step with the fix', async ({
	page,
}) => {
	// Windows: a variable created with setx after AIDO started is invisible to its process. The wizard
	// used to accept the reference and fail two steps later at "Sync models"; it now stops here.
	const wizard = await openConfigureWizard(page, 'NVIDIA NIM');
	await wizard.getByRole('button', { name: 'Next' }).click();
	await wizard.getByRole('radio', { name: 'Reference' }).check();
	await wizard.getByLabel('Credential reference').fill('env:AIDO_E2E_NOT_SET_ANYWHERE');
	await wizard.getByRole('button', { name: 'Next' }).click();
	await expect(wizard.getByText(/AIDO cannot read env:AIDO_E2E_NOT_SET_ANYWHERE \(missing\)/)).toBeVisible();
	await expect(wizard.getByText(/restart AIDO from a new terminal/)).toBeVisible();
	await expect(wizard.getByLabel('Credential reference')).toBeVisible();
});

test('Providers & CLI: an active gateway without enabled models asks to sync and select them', async ({
	page,
}) => {
	await page.route('**/api/v1/model-gateway/providers', async (route) => {
		const response = await route.fetch();
		const body = await response.json();
		const others = body.providers.filter((item) => item.providerId !== 'omniroute');
		const omniroute = body.providers.find((item) => item.providerId === 'omniroute') ?? {};
		body.providers = [
			...others,
			{
				...omniroute,
				providerId: 'omniroute',
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
		body.models = [
			...body.models.filter((item) => item.providerId !== 'omniroute'),
			{ id: 'omniroute:cc/claude-x', providerId: 'omniroute', model: 'cc/claude-x', enabled: false },
		];
		await route.fulfill({ response, json: body });
	});
	try {
		const settings = await openSettings(page);
		const card = settings.locator('.card').filter({ hasText: 'OmniRoute' }).first();
		await expect(card).toBeVisible({ timeout: 30_000 });
		await expect(card.locator('.card-reason')).toHaveText(
			'No model is enabled for this gateway. Sync models, then select the ones to use in Configure.',
		);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Add provider: the validate step saves the selection and shows which gateway model passed or failed', async ({
	page,
}) => {
	const gatewayModel = (model, enabled) => ({
		id: `omniroute:${model}`,
		providerId: 'omniroute',
		model,
		enabled,
		freeTier: true,
	});
	// The backend preselects the curated allowlist: the upstream without an account arrives unchecked.
	const models = [
		gatewayModel('cc/claude-x', false),
		gatewayModel('oc/big-pickle', true),
		gatewayModel('oc/deepseek-v4-flash-free', true),
	];
	const events = [];
	await page.route('/api/v1/provider-accounts/from-catalog', (route) => route.fulfill({ json: {} }));
	await page.route('/api/v1/settings/runtime.remote.enabled', (route) => route.fulfill({ json: {} }));
	await page.route('/api/v1/model-gateway/role-policies', (route) =>
		route.fulfill({ json: { rolePolicies: [] } }),
	);
	await page.route('/api/v1/provider-accounts/omniroute/sync-models', (route) =>
		route.fulfill({ json: { models } }),
	);
	await page.route('**/api/v1/model-gateway/models/*', async (route) => {
		if (route.request().method() !== 'PATCH') return route.continue();
		const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-1));
		const body = route.request().postDataJSON();
		events.push(`patch:${id}:${body.enabled}`);
		const model = models.find((entry) => entry.id === id);
		Object.assign(model, body);
		await route.fulfill({ json: { model } });
	});
	await page.route('**/api/v1/model-gateway/providers/omniroute/validate-runtime', async (route) => {
		events.push(`validate:${JSON.stringify(route.request().postDataJSON())}`);
		await route.fulfill({
			json: {
				validation: {
					providerId: 'omniroute',
					kind: 'gateway',
					status: 'validated',
					model: 'oc/big-pickle',
					latencyMs: 120,
					reason: null,
					evidence: null,
					checkedAt: '2026-09-27T10:00:00+00:00',
					attempts: [
						{
							model: 'cc/claude-x',
							status: 'failed',
							reason: 'runtime_validation_failed',
							evidence: 'HTTPError: HTTP Error 400: Bad Request: No active credentials for provider: cc',
							latencyMs: 5,
						},
						{ model: 'oc/big-pickle', status: 'validated', reason: null, evidence: null, latencyMs: 120 },
					],
				},
			},
		});
	});
	try {
		const wizard = await openWizard(page);
		await chooseProvider(wizard, 'omniroute');
		await wizard.getByRole('button', { name: 'Next', exact: true }).click();
		await expect(wizard.getByText('2/3 selected', { exact: true })).toBeVisible();
		await expect(wizard.getByRole('checkbox', { name: 'cc/claude-x', exact: true })).not.toBeChecked();

		// With nothing selected the real validation is refused before any request is sent.
		await wizard.getByRole('checkbox', { name: 'oc/big-pickle', exact: true }).uncheck();
		await wizard.getByRole('checkbox', { name: 'oc/deepseek-v4-flash-free', exact: true }).uncheck();
		await wizard.getByRole('button', { name: 'Next', exact: true }).click();
		await wizard.getByRole('button', { name: 'Validate with a real request', exact: true }).click();
		await expect(wizard.getByRole('alert')).toContainText('Select at least one model before validating.');
		expect(events).toEqual([]);

		// The operator keeps the broken upstream and one curated model: the selection is saved first,
		// then the backend picks and reports every model it tried.
		await wizard.getByRole('button', { name: 'Back', exact: true }).click();
		await wizard.getByRole('checkbox', { name: 'cc/claude-x', exact: true }).check();
		await wizard.getByRole('checkbox', { name: 'oc/big-pickle', exact: true }).check();
		await wizard.getByRole('button', { name: 'Next', exact: true }).click();
		await wizard.getByRole('button', { name: 'Validate with a real request', exact: true }).click();
		const summary = wizard.getByTestId('wizard-runtime-validation');
		await expect(summary).toContainText('Validated with oc/big-pickle');
		// One row per model tried: the id, its status word and the reason with the backend evidence.
		const failedRow = summary.getByRole('listitem').filter({ hasText: 'cc/claude-x' });
		await expect(failedRow).toContainText('failed');
		await expect(failedRow).toContainText('runtime_validation_failed');
		await expect(failedRow).toContainText('No active credentials for provider: cc');
		await expect(summary.getByRole('listitem').filter({ hasText: 'oc/big-pickle' })).toContainText(
			'passed',
		);
		expect(events).toEqual([
			'patch:omniroute:cc/claude-x:true',
			'patch:omniroute:oc/deepseek-v4-flash-free:false',
			'validate:{"projectId":null}',
		]);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Providers & CLI: a provider over its usage threshold shows the suspension, its own threshold and a resume', async ({
	page,
}) => {
	const resetsAt = new Date(Date.now() + 2 * 86_400_000).toISOString();
	const claude = {
		providerId: 'claude_code_cli',
		thresholdPercent: 80,
		thresholdSource: 'general',
		ownThresholdPercent: null,
		maxUsedPercent: 91,
		windows: [
			{ providerId: 'claude_code_cli', window: 'five_hour', label: '5h', usedPercent: 33, resetsAt, source: 'claude_oauth_usage', status: 'ok', observedAt: resetsAt },
			{ providerId: 'claude_code_cli', window: 'seven_day', label: '7d', usedPercent: 91, resetsAt, source: 'claude_oauth_usage', status: 'ok', observedAt: resetsAt },
		],
		suspended: true,
		suspendedUntil: resetsAt,
		suspendedWindow: 'seven_day',
		ignoreUntil: null,
		lastPollAt: resetsAt,
		lastPollError: null,
		hasRemoteSource: true,
	};
	const calls = [];
	const body = () => ({ generalThresholdPercent: 80, providers: [claude] });
	await page.route('**/api/v1/runtime/provider-usage', (route) => route.fulfill({ json: body() }));
	await page.route('**/api/v1/runtime/provider-usage/claude_code_cli/policy', async (route) => {
		const threshold = route.request().postDataJSON().thresholdPercent;
		calls.push(`policy:${threshold}`);
		Object.assign(claude, { thresholdPercent: threshold ?? 80, ownThresholdPercent: threshold, thresholdSource: threshold === null ? 'general' : 'provider', suspended: (threshold ?? 80) <= 91, suspendedUntil: (threshold ?? 80) <= 91 ? resetsAt : null });
		await route.fulfill({ json: body() });
	});
	await page.route('**/api/v1/runtime/provider-usage/claude_code_cli/resume', async (route) => {
		calls.push('resume');
		Object.assign(claude, { suspended: false, suspendedUntil: null, ignoreUntil: resetsAt });
		await route.fulfill({ json: body() });
	});
	try {
		await page.goto('/#settings-runtime');
		const settings = await openSettings(page);
		await expect(settings.getByText(/1 provider\(s\) suspended by usage quota/)).toBeVisible({ timeout: 30_000 });
		const card = settings.locator('.card').filter({ hasText: 'Claude Code' }).first();
		const usage = card.getByRole('region', { name: 'Usage quota' });
		await expect(usage).toBeVisible();
		await expect(usage.getByText(/^Suspended until /)).toBeVisible();
		const weekly = usage.getByRole('meter', { name: 'Weekly' });
		await expect(weekly).toHaveAttribute('aria-valuenow', '91');
		await expect(weekly).toHaveAttribute('data-tone', 'danger');
		await expect(usage.getByRole('meter', { name: '5-hour window' })).toHaveAttribute('data-tone', 'ok');
		await expect(usage.getByText(/Uses the general 80%/)).toBeVisible();

		const field = usage.getByLabel('Suspend at');
		await field.fill('150');
		await expect(usage.getByText('Enter a whole number from 1 to 100.')).toBeVisible();
		await expect(usage.getByRole('button', { name: 'Save' })).toBeDisabled();
		await field.fill('95');
		await usage.getByRole('button', { name: 'Save' }).click();
		await expect(usage.getByText(/Own threshold/)).toBeVisible();
		await expect(usage.getByText(/^Suspended until /)).toHaveCount(0);

		await field.fill('');
		await usage.getByRole('button', { name: 'Save' }).click();
		await expect(usage.getByText(/^Suspended until /)).toBeVisible();
		await usage.getByRole('button', { name: 'Resume until reset' }).click();
		await expect(usage.getByText(/^Suspended until /)).toHaveCount(0);
		expect(calls).toEqual(['policy:95', 'policy:null', 'resume']);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Providers & CLI: the general usage threshold is editable there and the provider cards pick it up', async ({
	page,
}) => {
	await page.goto('/#settings-runtime');
	const token = (await (await page.request.get('/api/v1/security/handshake')).json()).token;
	let usageReads = 0;
	await page.route('**/api/v1/runtime/provider-usage', async (route) => {
		usageReads += 1;
		await route.continue();
	});
	const label = 'Suspend a provider for AIDO at this % of its usage quota (all providers; each one can override it)';
	try {
		const settings = await openSettings(page);
		const field = settings.getByLabel(label);
		await expect(field).toBeVisible({ timeout: 30_000 });
		await expect.poll(() => usageReads).toBeGreaterThan(0);
		const before = usageReads;
		await field.fill('90');
		await settings.locator('.setting-row').filter({ hasText: label }).getByRole('button', { name: 'Save', exact: true }).click();
		await expect
			.poll(async () => {
				const general = (await (await page.request.get('/api/v1/settings')).json()).general;
				return general.find((item) => item.key === 'runtime.quota.suspendThresholdPercent').value;
			})
			.toBe(90);
		await expect.poll(() => usageReads).toBeGreaterThan(before);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
		await page.request.delete('/api/v1/settings/runtime.quota.suspendThresholdPercent?scope=general', {
			headers: { 'X-Local-Control-Token': token },
		});
	}
});

test('Providers & CLI: every CLI card, including OpenHands and SWE-agent, carries its own switch', async ({
	page,
}) => {
	const patches = [];
	await page.route('**/api/v1/model-gateway/providers/*', async (route) => {
		if (route.request().method() !== 'PATCH') return route.continue();
		const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-1));
		const body = route.request().postDataJSON();
		patches.push({ id, ...body });
		await route.fulfill({ json: { provider: { providerId: id, enabled: body.enabled } } });
	});
	try {
		const settings = await openSettings(page);
		for (const name of ['OpenHands', 'SWE-agent', 'Manual Operator']) {
			const card = settings.locator('.card').filter({ hasText: name }).first();
			await expect(card.getByRole('checkbox', { name: `Use ${name} in threads` })).toBeVisible({
				timeout: 30_000,
			});
		}
		// Switching Claude Code alone off/on patches only its own account.
		const card = settings.locator('.card').filter({ hasText: 'Claude Code CLI' }).first();
		const toggle = card.getByRole('checkbox', { name: 'Use Claude Code CLI in threads' });
		await expect(toggle).toBeVisible();
		const wasChecked = await toggle.isChecked();
		await toggle.click();
		await expect.poll(() => patches).toEqual([{ id: 'claude_code_cli', enabled: !wasChecked }]);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});
