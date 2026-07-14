import { expect, test } from '@playwright/test';

const NOW = '2026-07-14T00:00:00Z';

function nvidiaChatAccount(providerId, displayName, credentialRef) {
	return {
		id: `provider-account:${providerId}`,
		providerId,
		displayName,
		providerType: 'api',
		apiFormat: 'openai_compatible',
		providerFamily: 'nvidia_nim',
		deploymentMode: 'hosted_trial',
		apiFamily: 'chat_completions',
		adapterProfile: 'nvidia_openai_chat',
		termsMode: 'evaluation',
		pricingMode: 'unknown',
		baseUrl: 'https://integrate.api.nvidia.com/v1',
		credentialRef,
		credentialStatus: 'configured',
		enabled: true,
		quotaMode: 'explicit',
		healthStatus: 'healthy',
		lastError: '',
		lastHealthCheckAt: NOW,
		metadata: {},
		createdAt: NOW,
		updatedAt: NOW,
	};
}

function chatModel(providerId, model) {
	return {
		id: `${providerId}:${model}`,
		providerId,
		model,
		displayName: model,
		modelFamily: 'nvidia_nim',
		apiFamily: 'chat_completions',
		contextWindow: 131072,
		maxOutputTokens: 8192,
		supportsTools: true,
		supportsJson: true,
		supportsVision: false,
		supportsReasoning: true,
		supportsStreaming: true,
		supportsThinking: false,
		supportsEmbeddings: false,
		supportsRerank: false,
		supportsImageGeneration: false,
		supportsImageEditing: false,
		effortLevels: [],
		inputPricePerMtok: null,
		cachedInputPricePerMtok: null,
		outputPricePerMtok: null,
		reasoningPricePerMtok: null,
		freeTier: false,
		freeTierNotes: 'Hosted trial is evaluation access, not a production free tier.',
		source: 'explicit_manifest',
		enabled: true,
		createdAt: NOW,
		updatedAt: NOW,
	};
}

test('NVIDIA NIM setup exposes endpoint-scoped contracts and blocks unsupported hosted editing', async ({
	page,
}) => {
	await page.goto('/#models');
	await expect(page.getByRole('heading', { name: 'NVIDIA NIM endpoints' })).toBeVisible();
	await expect(page.getByText('Hosted trial: evaluation only')).toBeVisible();
	await expect(page.getByText('Local self-hosted preflight')).toBeVisible();
	await expect(page.getByText('read-only; no mutation')).toBeVisible();
	await expect(page.getByLabel('Credential reference')).toHaveAttribute('autocomplete', 'off');

	await page.getByLabel('API family').selectOption('image_editing');
	await expect(page.getByLabel('Adapter profile')).toBeDisabled();
	await expect(
		page.getByText('Hosted arbitrary image editing is not supported by this contract.'),
	).toBeVisible();
	await expect(page.getByRole('button', { name: 'Create endpoint account' })).toBeDisabled();

	await page.getByRole('tab', { name: 'Budgets' }).click();
	await expect(page.getByLabel('Max concurrency')).toBeVisible();
	await expect(page.getByLabel('Window timezone')).toBeVisible();
	await expect(page.getByLabel('Unknown limit strategy')).toHaveValue('conservative');
	await expect(page.getByRole('button', { name: 'Create policy' })).toBeVisible();
});

test('parallel execution console builds explicit unique branches and bounded parallelism', async ({
	page,
}) => {
	const providers = [
		nvidiaChatAccount('nvidia-chat-a', 'NVIDIA Chat A', 'env:NVIDIA_CHAT_A'),
		nvidiaChatAccount('nvidia-chat-b', 'NVIDIA Chat B', 'env:NVIDIA_CHAT_B'),
	];
	const models = [
		chatModel('nvidia-chat-a', 'minimaxai/minimax-m3'),
		chatModel('nvidia-chat-b', 'deepseek-ai/deepseek-v4-pro'),
	];
	await page.route('**/api/v1/model-gateway/providers', (route) =>
		route.fulfill({ json: { providers } }),
	);
	await page.route('**/api/v1/model-gateway/models', (route) =>
		route.fulfill({ json: { models } }),
	);

	await page.goto('/#models');
	await page.getByRole('tab', { name: 'Catalog' }).click();
	await expect(page.getByRole('heading', { name: 'NVIDIA model manifest and pricing' })).toBeVisible();
	await expect(page.getByLabel('Exact model id')).toBeVisible();
	await expect(page.getByLabel('Verified provider free tier')).toBeDisabled();
	await expect(page.getByLabel('Input USD / MTok', { exact: true })).toBeDisabled();

	await page.getByRole('tab', { name: 'Routing' }).click();
	await expect(page.getByRole('heading', { name: 'One-or-many model execution' })).toBeVisible();
	await expect(page.getByLabel('Endpoint')).toHaveCount(1);
	await expect(page.getByRole('combobox', { name: 'Model', exact: true })).toHaveValue(
		'minimaxai/minimax-m3',
	);

	await page.getByRole('button', { name: 'Add model branch' }).click();
	await expect(page.getByLabel('Endpoint')).toHaveCount(2);
	await expect(page.getByLabel('Strategy')).toHaveValue('parallel_compare');
	await expect(page.getByLabel('Max parallelism')).toHaveValue('2');
	await expect(page.getByRole('combobox', { name: 'Model', exact: true }).nth(1)).toHaveValue(
		'deepseek-ai/deepseek-v4-pro',
	);
});

test('paid NVIDIA model pricing is sourced, applied to the catalog and marks only that endpoint configured', async ({
	page,
}) => {
	const paidAccount = {
		...nvidiaChatAccount('nvidia-paid', 'NVIDIA Partner Paid', 'env:NVIDIA_PAID'),
		deploymentMode: 'partner_paid',
		termsMode: 'accepted',
		pricingMode: 'unknown',
		baseUrl: 'https://partner.example.test/v1',
	};
	let modelPayload;
	let pricingPayload;
	let providerPatch;
	await page.route('**/api/v1/model-gateway/providers', (route) =>
		route.fulfill({ json: { providers: [paidAccount] } }),
	);
	await page.route('**/api/v1/model-gateway/models', async (route) => {
		if (route.request().method() === 'POST') {
			modelPayload = route.request().postDataJSON();
			await route.fulfill({
				status: 201,
				json: { model: { ...chatModel('nvidia-paid', modelPayload.model), ...modelPayload } },
			});
			return;
		}
		await route.fulfill({ json: { models: [] } });
	});
	await page.route('**/api/v1/model-gateway/pricing-snapshots', async (route) => {
		pricingPayload = route.request().postDataJSON();
		await route.fulfill({
			status: 201,
			json: { pricingSnapshot: { id: 'pricing-paid', ...pricingPayload, createdAt: NOW } },
		});
	});
	await page.route('**/api/v1/model-gateway/providers/nvidia-paid', async (route) => {
		providerPatch = route.request().postDataJSON();
		await route.fulfill({ json: { provider: { ...paidAccount, ...providerPatch } } });
	});

	await page.goto('/#models');
	await page.getByRole('tab', { name: 'Catalog' }).click();
	await page.getByLabel('Exact model id').fill('deepseek-ai/deepseek-v4-pro');
	await page.getByLabel('Context window').fill('131072');
	await page.getByLabel('Max output tokens').fill('8192');
	await page.getByLabel('supportsReasoning').check();
	await page.getByLabel('Input USD / MTok', { exact: true }).fill('1.25');
	await page.getByLabel('Output USD / MTok').fill('5');
	await page.getByLabel('Pricing source / contract ref').fill('contract:nvidia-partner-2026');
	await page.getByRole('button', { name: 'Save model manifest' }).click();
	await expect(page.getByText('Model manifest saved. Unknown prices remain null; sourced prices are active for cost admission.')).toBeVisible();

	expect(modelPayload).toMatchObject({
		providerId: 'nvidia-paid',
		model: 'deepseek-ai/deepseek-v4-pro',
		apiFamily: 'chat_completions',
		contextWindow: 131072,
		maxOutputTokens: 8192,
		supportsReasoning: true,
		inputPricePerMtok: 1.25,
		outputPricePerMtok: 5,
		freeTier: false,
	});
	expect(pricingPayload).toMatchObject({
		providerId: 'nvidia-paid',
		model: 'deepseek-ai/deepseek-v4-pro',
		inputPricePerMtok: 1.25,
		outputPricePerMtok: 5,
		freeTier: false,
		sourceRef: 'contract:nvidia-partner-2026',
		applyToCatalog: true,
	});
	expect(providerPatch).toEqual({ pricingMode: 'configured' });
});
