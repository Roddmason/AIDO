import { expect, test } from '@playwright/test';

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
	const wizard = page.getByRole('dialog', { name: 'Add provider' });
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
	const wizard = page.getByRole('dialog', { name: 'Add provider' });
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
