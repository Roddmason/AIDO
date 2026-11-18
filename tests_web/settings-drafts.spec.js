import { expect, test } from '@playwright/test';

test('Settings: saving another row preserves an unfinished draft during and after refresh', async ({ page }) => {
	const descriptor = (key, value) => ({
		key, value, type: 'number', section: 'costs', editableScopes: ['general'],
		inherited: false, origin: 'general', source: 'general',
	});
	const general = [descriptor('draft-cost-limit', 10), descriptor('saved-cost-limit', 20)];
	general[1].origin = 'default';
	general[1].source = 'default';
	let releaseRefresh;
	let holdRefresh = false;
	const refreshGate = new Promise((resolve) => { releaseRefresh = resolve; });
	await page.route('**/api/v1/settings?*', async (route) => {
		if (holdRefresh) await refreshGate;
		await route.fulfill({ json: { general, project: [] } });
	});
	await page.route('**/api/v1/settings/saved-cost-limit', async (route) => {
		general[1].value = route.request().postDataJSON().value;
		general[1].origin = 'general';
		general[1].source = 'general';
		holdRefresh = true;
		await route.fulfill({ json: {} });
	});
	try {
		await page.goto('/#settings');
		const dialog = page.getByRole('dialog', { name: 'Settings' });
		await dialog.getByRole('button', { name: 'Costs', exact: true }).click();
		const draft = dialog.getByRole('spinbutton', { name: 'draft-cost-limit', exact: true });
		const saved = dialog.getByRole('spinbutton', { name: 'saved-cost-limit', exact: true });
		await expect(draft).toBeVisible({ timeout: 30_000 });
		await draft.fill('123');
		await saved.fill('42');
		const savedRow = dialog.locator('.setting-row').filter({
			has: page.getByRole('spinbutton', { name: 'saved-cost-limit', exact: true }),
		});
		await expect(savedRow).toHaveCount(1);
		const refreshStarted = page.waitForRequest((request) =>
			request.method() === 'GET' && new URL(request.url()).pathname === '/api/v1/settings',
		);
		await savedRow.getByRole('button', { name: 'Save', exact: true }).click();
		await refreshStarted;
		await expect(draft).toBeVisible();
		await expect(draft).toHaveValue('123');
		releaseRefresh();
		await expect(savedRow.locator('.setting-chip[data-origin="custom"]')).toBeVisible();
		await expect(saved).toHaveValue('42');
		await expect(dialog.locator('.settings-skeleton')).toHaveCount(0);
		await expect(draft).toHaveValue('123');
	} finally {
		releaseRefresh();
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});
