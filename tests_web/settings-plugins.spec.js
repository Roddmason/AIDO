import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { expect, test } from '@playwright/test';

function sha256(payload) {
	return `sha256:${createHash('sha256').update(payload).digest('hex')}`;
}

/**
 * Writes a plugin directory with a checksummed SKILL.md contract and an
 * aido.plugin.json manifest, mirroring the backend install contract. Returns
 * the plugin directory so a test can install it through the API as well.
 */
function writePluginFixture(root, { dirName, id, name, permissions }) {
	const pluginDir = path.join(root, dirName);
	mkdirSync(path.join(pluginDir, 'skills', 'example'), { recursive: true });
	const skillBody = [
		'---',
		`name: ${id}-skill`,
		'description: End-to-end fixture skill contract.',
		'---',
		'',
		'# Fixture Skill',
		'',
		'Contract body.',
		'',
	].join('\n');
	writeFileSync(path.join(pluginDir, 'skills', 'example', 'SKILL.md'), skillBody, 'utf8');
	const manifest = {
		id,
		name,
		version: '1.0.0',
		publisher: 'AIDO E2E',
		trustLevel: 'third_party',
		capabilities: ['skill:example'],
		permissions,
		entrypoints: {
			skills: [{ id: `${id}-skill`, path: 'skills/example/SKILL.md' }],
			agents: [
				{
					id: `${id}-agent`,
					role: 'reviewer',
					capabilities: ['code_review'],
					schema: { type: 'object', properties: {} },
				},
			],
			tools: [
				{
					id: `${id}-tool`,
					name: 'Fixture Tool',
					brokerTool: 'shell',
					execute: false,
					schema: { type: 'object', properties: {} },
					policy: { decision: 'deny_by_default' },
				},
			],
		},
		checksums: { 'skills/example/SKILL.md': sha256(skillBody) },
		minAidoVersion: '0.1.0',
	};
	writeFileSync(path.join(pluginDir, 'aido.plugin.json'), JSON.stringify(manifest, null, 2), 'utf8');
	return pluginDir;
}

test('Settings Plugins console installs, enables, inspects, and audits local plugins', async ({
	page,
}) => {
	const root = mkdtempSync(path.join(tmpdir(), 'aido-plugins-e2e-'));
	const stamp = Date.now();
	const pluginId = `e2e.plugin.${stamp}`;
	const pluginName = `E2E Plugin ${stamp}`;
	const dangerId = `e2e.danger.${stamp}`;
	const dangerName = `E2E Danger ${stamp}`;
	// The installable plugin carries a scoped-but-mutation-capable permission, so it must
	// surface the "Dangerous permission" review badge once installed.
	writePluginFixture(root, {
		dirName: 'valid-plugin',
		id: pluginId,
		name: pluginName,
		permissions: ['workspace.read', 'filesystem.write:reports'],
	});
	const dangerDir = writePluginFixture(root, {
		dirName: 'dangerous-plugin',
		id: dangerId,
		name: dangerName,
		permissions: ['filesystem.write:/'],
	});

	// Seed a real blocked install attempt through the write API so the Blocked and Events
	// tabs render an auditable rejection with its validator reason (the UI disables Install
	// on invalid candidates by design, so a blocked event cannot be produced from the row).
	const token = (await (await page.request.get('/api/v1/security/handshake')).json()).token;
	const blocked = await page.request.post('/api/v1/plugins/install-local', {
		headers: { 'X-Local-Control-Token': token },
		data: { path: dangerDir },
	});
	expect(blocked.status()).toBe(422);

	await page.goto('/');
	await page.locator('.shell-sidebar-footer').getByRole('button', { name: 'Settings' }).click();
	const dialog = page.getByRole('dialog', { name: 'Settings' });
	await expect(dialog).toBeVisible();
	// Both scopes list a Plugins section; the general-scope registry entry comes first.
	await dialog.getByRole('button', { name: 'Plugins', exact: true }).first().click();
	await expect(dialog.getByRole('heading', { name: 'Plugin registry' })).toBeVisible();

	// The empty installed view routes straight to the folder installer.
	await dialog.getByRole('button', { name: 'Install from folder' }).click();
	await expect(dialog.getByRole('tab', { name: 'Available (local folder)' })).toHaveAttribute(
		'aria-selected',
		'true',
	);

	// Scan is read-only: the dangerous sibling surfaces as blocked and cannot be installed.
	await dialog.getByLabel('Local plugin folder').fill(root);
	await dialog.getByRole('button', { name: 'Scan folder' }).click();
	const validRow = dialog.getByRole('row').filter({ hasText: pluginName });
	const dangerRow = dialog.getByRole('row').filter({ hasText: dangerName });
	await expect(validRow.getByText('valid', { exact: true })).toBeVisible();
	await expect(dangerRow.getByText('blocked', { exact: true })).toBeVisible();
	await expect(dangerRow.getByRole('button', { name: 'Install', exact: true })).toBeDisabled();

	// Install the validated candidate; the rescan marks it as already installed.
	await validRow.getByRole('button', { name: 'Install', exact: true }).click();
	await expect(page.getByText('Plugin installed', { exact: true })).toBeVisible();
	await expect(validRow.getByText('installed', { exact: true })).toBeVisible();

	// Installed tab: the plugin reads as third-party, disabled, and carrying a dangerous permission.
	await dialog.getByRole('tab', { name: 'Installed', exact: true }).click();
	const pluginRow = dialog.getByRole('row').filter({ hasText: pluginId });
	await expect(pluginRow.getByText('Third-party', { exact: true })).toBeVisible();
	await expect(pluginRow.getByText('Disabled', { exact: true })).toBeVisible();
	await expect(pluginRow.getByText('Dangerous permission', { exact: true })).toBeVisible();

	// Enabling revalidates the manifest and flips the lifecycle badge to Enabled.
	await pluginRow.getByRole('button', { name: 'Enable', exact: true }).click();
	await expect(page.getByText('Plugin enabled', { exact: true })).toBeVisible();
	await expect(pluginRow.getByText('Enabled', { exact: true })).toBeVisible();

	await pluginRow.getByRole('button', { name: 'Validate', exact: true }).click();
	await expect(page.getByText('Manifest valid', { exact: true })).toBeVisible();

	// Inspect: one per-plugin surface with manifest, permissions, skills, and tools sub-views.
	await pluginRow.getByRole('button', { name: 'Inspect', exact: true }).click();
	const inspector = page.getByRole('dialog', { name: 'Plugin inspector' });
	await expect(inspector.getByText('Manifest hash')).toBeVisible();
	await expect(inspector.getByText(`"id": "${pluginId}"`)).toBeVisible();
	await inspector.getByRole('tab', { name: 'Permissions', exact: true }).click();
	await expect(inspector.getByText('workspace.read')).toBeVisible();
	await inspector.getByRole('tab', { name: 'Skills', exact: true }).click();
	await expect(inspector.getByText('skills/example/SKILL.md')).toBeVisible();
	await inspector.getByRole('tab', { name: 'Tools', exact: true }).click();
	await expect(inspector.getByText('Fixture Tool')).toBeVisible();
	await expect(inspector.getByText('policy required', { exact: true })).toBeVisible();

	// Layered Escape (WCAG 2.2 layered dismiss): backing out of the nested inspector with
	// a single Escape closes only the topmost dialog; the Settings modal beneath survives.
	await page.keyboard.press('Escape');
	await expect(inspector).toBeHidden();
	await expect(dialog).toBeVisible();

	// Blocked tab: the seeded rejection is listed with its exact validator reason.
	await dialog.getByRole('tab', { name: 'Blocked', exact: true }).click();
	const blockedRow = dialog.getByRole('row').filter({ hasText: dangerId });
	await expect(blockedRow.getByText('Blocked', { exact: true })).toBeVisible();
	await expect(blockedRow.getByText(/dangerous/)).toBeVisible();

	// Events tab: the full lifecycle trail (install + enable) is auditable.
	await dialog.getByRole('tab', { name: 'Events', exact: true }).click();
	await expect(dialog.getByText('enable', { exact: true })).toBeVisible();
	await expect(dialog.getByText('install_local', { exact: true }).first()).toBeVisible();

	// Single-layer behaviour is unchanged: with the inspector already gone, one Escape
	// dismisses the lone Settings modal.
	await page.keyboard.press('Escape');
	await expect(dialog).toBeHidden();
});
