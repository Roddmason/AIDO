import { test, expect } from './fixtures/operations.js';
import { mkdtemp, readdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

async function loaded(page, route) {
	await page.goto('/');
	await page.reload();
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible();
	await expect(page.getByText('Loading control plane...')).toBeHidden();
	if (route.startsWith('/#settings')) {
		await page.locator('.shell-sidebar-footer').getByRole('button', { name: /^(Open settings|Settings)$/ }).click();
		await page.getByRole('dialog', { name: 'Settings', exact: true }).getByRole('button', { name: route === '/#settings-credentials' ? 'Credentials' : 'Providers & CLI', exact: true }).first().click();
	}
}

async function capture(page, info, name) {
	const dialog = page.getByRole('dialog').last();
	await expect(dialog).toHaveCSS('opacity', '1');
	await page.screenshot({ path: info.outputPath(`${process.env.AIDO_UX_CAPTURE_PHASE || 'current'}-${name}.png`), animations: 'disabled', fullPage: true });
}

test('Post-smoke baseline connections and credentials', async ({ page }, info) => {
	await loaded(page, '/#settings-runtime');
	await expect(page.getByRole('dialog', { name: 'Settings', exact: true })).toBeVisible();
	await capture(page, info, 'connections');
	await page.getByRole('button', { name: 'Add provider', exact: true }).click();
	await expect(page.getByRole('region', { name: 'Add provider', exact: true })).toBeVisible();
	await capture(page, info, 'provider');
	await loaded(page, '/#settings-credentials');
	await expect(page.getByRole('dialog', { name: 'Settings', exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Credential Manager' })).toBeVisible();
	await capture(page, info, 'credentials');
});

test('Post-smoke provider setup uses one modal and configuration check never runs a prompt', async ({ page }) => {
	let prompts = 0;
	await page.route('**/api/v1/model-gateway/providers/*/health-check', route => route.fulfill({ json: { health: { healthStatus: 'healthy', message: 'Synthetic provider boundary only' } } }));
	await page.route('**/api/v1/model-gateway/providers/*/test-prompt', route => {
		prompts += 1;
		return route.fulfill({ json: { test: { ok: true } } });
	});
	await loaded(page, '/#settings-runtime');
	await page.getByRole('button', { name: 'Add provider', exact: true }).click();
	await page.getByLabel('Provider', { exact: true }).selectOption('ollama');
	await page.getByRole('button', { name: 'Next', exact: true }).click();
	await page.getByRole('button', { name: 'Next', exact: true }).click();
	await expect(page.getByRole('button', { name: 'Sync models', exact: true }).last()).toBeVisible();
	await page.getByRole('button', { name: 'Next', exact: true }).click();
	await page.getByRole('button', { name: /Validate connection|Check configuration \/ auth/ }).click();
	await expect(page.getByText('Provider responded', { exact: true })).toBeVisible();
	expect(prompts).toBe(0);
	await expect(page.getByRole('dialog')).toHaveCount(1);
});

test('Post-smoke real API registers an explicit new folder once without starting agents', async ({ page }, info) => {
	const base = await mkdtemp(path.join(os.tmpdir(), 'aido-ux-project-'));
	let creates = 0;
	const forbidden = [];
	page.on('request', request => {
		if (request.method() !== 'POST') return;
		if (new URL(request.url()).pathname === '/api/v1/projects') creates += 1;
		if (/test-prompt|smoke|product-loop|agent-runs|\/threads/.test(request.url())) forbidden.push(request.url());
	});
	await loaded(page, '/#threads');
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: /Open folder|Abrir carpeta/ }).click();
	await page.locator('.workspace-mode-card').filter({ hasText: 'Create workspace' }).click();
	await page.getByLabel('Workspace base path').fill(base);
	await page.getByLabel('Workspace name', { exact: true }).fill('Proyecto ñ con espacios');
	await page.getByRole('button', { name: 'Next', exact: true }).click();
	await page.getByLabel('Project name', { exact: true }).fill('Proyecto sintético sin agentes');
	await page.getByRole('button', { name: 'Next', exact: true }).click();
	await capture(page, info, 'workspace-review');
	await page.getByRole('button', { name: 'Open in workbench' }).dblclick();
	await expect(page.getByRole('dialog', { name: 'New workspace', exact: true })).toBeHidden();
	const response = await page.request.get('/api/v1/projects');
	const { projects } = await response.json();
	const project = projects.find(item => item.name === 'Proyecto sintético sin agentes');
	expect(project.path).toBe(path.join(base, 'Proyecto ñ con espacios'));
	expect(project.metadata.creationMode).toBe('new_under_workspace');
	expect(creates).toBe(1);
	expect(forbidden).toEqual([]);
	expect(await readdir(project.path)).not.toContain('.git');
});

test('Post-smoke baseline workspace journey', async ({ page }, info) => {
	await loaded(page, '/#threads');
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: /Open folder|Abrir carpeta/ }).click();
	await expect(page.getByRole('dialog', { name: 'New workspace', exact: true })).toBeVisible();
	await capture(page, info, 'workspace-open');
	await page.locator('.workspace-mode-card').filter({ hasText: 'Create workspace' }).click();
	await capture(page, info, 'workspace-create');
});

test('Post-smoke manual existing folder reaches review before a project name is requested', async ({ page }) => {
	await loaded(page, '/#threads');
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: /Open folder|Abrir carpeta/ }).click();
	await page.getByLabel('Workspace folder', { exact: true }).fill('H:\\Fixtures\\Carpeta con espacios ñ');
	await page.getByRole('button', { name: 'Next', exact: true }).click();
	await expect(page.getByLabel('Project name', { exact: true })).toBeVisible();
	await page.getByRole('button', { name: 'Next', exact: true }).click();
	await expect(page.getByRole('alert')).toContainText('Project name is required.');
});

test('Post-smoke secret is write-only and cleared when credential save fails', async ({ page }) => {
	await page.route('**/api/v1/credentials', async (route) => {
		if (route.request().method() === 'POST') return route.fulfill({ status: 503, json: { detail: 'Synthetic vault unavailable' } });
		return route.fulfill({ json: { credentials: [], backends: [{ kind: 'keyring', configured: true, readOnly: false, default: true }] } });
	});
	await loaded(page, '/#settings-credentials');
	await page.getByRole('textbox', { name: 'Label', exact: true }).fill('Synthetic test');
	await page.getByRole('textbox', { name: 'Credential ref', exact: true }).fill('synthetic/test-only');
	const secret = page.getByLabel(/^Credential(?: \*)?$/);
	await secret.fill('SYNTHETIC_SECRET_NO_PROVIDER_123456789');
	await expect.poll(() => page.content()).not.toContain('SYNTHETIC_SECRET_NO_PROVIDER_123456789');
	await page.getByRole('button', { name: 'Add', exact: true }).click();
	await expect(secret).toHaveValue('');
});

test('Post-smoke credential invalid, delete cancellation and rotation preserve keyboard and secret boundaries', async ({ page }) => {
	let deletions = 0;
	const credential = { id: 'synthetic-credential', label: 'Synthetic metadata', source: 'keyring', credentialRef: 'synthetic/local', status: 'invalid', providerUsages: [] };
	await page.route('**/api/v1/credentials', route => route.fulfill({ json: { credentials: [credential], backends: [{ kind: 'keyring', configured: true, readOnly: false }] } }));
	await page.route('**/api/v1/credentials/synthetic-credential**', route => {
		if (route.request().method() === 'DELETE') deletions += 1;
		if (route.request().url().endsWith('/validate')) return route.fulfill({ json: { validation: { valid: false } } });
		return route.fulfill({ status: 503, json: { detail: 'Synthetic vault unavailable' } });
	});
	await loaded(page, '/#settings-credentials');
	await page.getByRole('button', { name: 'Validate', exact: true }).click();
	await expect(page.locator('[data-tone="danger"]').filter({ hasText: 'Credential invalid' })).toBeVisible();
	const trigger = page.getByRole('button', { name: 'Delete', exact: true });
	await trigger.focus();
	await page.keyboard.press('Enter');
	const confirmation = page.getByRole('region', { name: 'Delete this credential reference?' });
	await expect(confirmation).toBeFocused();
	await expect(page.getByRole('dialog')).toHaveCount(1);
	await page.keyboard.press('Escape');
	await expect(confirmation).toBeHidden();
	await expect(trigger).toBeFocused();
	expect(deletions).toBe(0);
	await page.getByText('Advanced · rotation and audit', { exact: true }).click();
	const secret = page.getByLabel('New credential', { exact: true });
	await secret.fill('SYNTHETIC_ROTATION_NO_PROVIDER');
	await expect.poll(() => page.content()).not.toContain('SYNTHETIC_ROTATION_NO_PROVIDER');
	await page.getByRole('button', { name: 'Rotate', exact: true }).click();
	await expect(secret).toHaveValue('');
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Settings', exact: true })).toBeHidden();
});

test('Post-smoke provider keyboard focus excludes hidden catalog and returns to its trigger', async ({ page }) => {
	await loaded(page, '/#settings-runtime');
	const trigger = page.getByRole('button', { name: 'Add provider', exact: true });
	await trigger.focus();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('heading', { name: 'Add provider', exact: true })).toBeFocused();
	for (let index = 0; index < 18; index += 1) {
		await page.keyboard.press('Tab');
		expect(await page.evaluate(() => Boolean(document.activeElement?.getClientRects().length && !document.activeElement?.closest('[hidden]')))).toBe(true);
	}
	await page.getByRole('button', { name: 'Close provider setup' }).focus();
	await page.keyboard.press('Enter');
	await expect(trigger).toBeFocused();
});

test('Post-smoke opening a missing folder is refused before project registration', async ({ page }) => {
	const parent = await mkdtemp(path.join(os.tmpdir(), 'aido-ux-missing-'));
	let creates = 0;
	page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/projects') creates += 1; });
	await loaded(page, '/#threads');
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: /Open folder|Abrir carpeta/ }).click();
	await page.getByLabel('Workspace folder', { exact: true }).fill(path.join(parent, 'missing ñ'));
	await page.getByRole('button', { name: 'Next', exact: true }).click();
	await page.getByLabel('Project name', { exact: true }).fill('Missing synthetic folder');
	await page.getByRole('button', { name: 'Next', exact: true }).click();
	await page.getByRole('button', { name: 'Open in workbench' }).click();
	await expect(page.getByRole('alert')).toContainText('The selected path is not an existing directory.');
	expect(creates).toBe(0);
});

test('Post-smoke project setup from Settings suspends the underlying modal and restores it on cancel', async ({ page }, info) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await loaded(page, '/#settings-runtime');
	await page.getByRole('dialog', { name: 'Settings', exact: true }).getByRole('button', { name: 'Goal', exact: true }).click();
	await page.getByRole('button', { name: 'New project', exact: true }).click();
	await expect(page.getByRole('dialog')).toHaveCount(1);
	await capture(page, info, 'workspace-from-settings');
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Settings', exact: true })).toBeVisible();
	expect(await page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]')))).toBe(true);
});
