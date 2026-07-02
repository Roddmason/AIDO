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
 * aido.plugin.json manifest, mirroring the backend install contract.
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

test('Settings Plugins section installs, enables and validates a local plugin end to end', async ({
	page,
}) => {
	const root = mkdtempSync(path.join(tmpdir(), 'aido-plugins-e2e-'));
	const stamp = Date.now();
	const pluginId = `e2e.plugin.${stamp}`;
	const pluginName = `E2E Plugin ${stamp}`;
	const dangerName = `E2E Danger ${stamp}`;
	writePluginFixture(root, {
		dirName: 'valid-plugin',
		id: pluginId,
		name: pluginName,
		permissions: ['workspace.read'],
	});
	writePluginFixture(root, {
		dirName: 'dangerous-plugin',
		id: `e2e.danger.${stamp}`,
		name: dangerName,
		permissions: ['filesystem.write:/'],
	});

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

	// Plugins install disabled by contract; enabling revalidates the manifest.
	await dialog.getByRole('tab', { name: 'Installed', exact: true }).click();
	const pluginRow = dialog.getByRole('row').filter({ hasText: pluginId });
	await expect(pluginRow.getByText('disabled', { exact: true })).toBeVisible();
	await pluginRow.getByRole('button', { name: 'Enable', exact: true }).click();
	await expect(pluginRow.getByText('enabled', { exact: true })).toBeVisible();

	await pluginRow.getByRole('button', { name: 'Validate', exact: true }).click();
	await expect(page.getByText('Manifest valid', { exact: true })).toBeVisible();

	// View permissions: the declared manifest permission is listed with its risk level.
	await pluginRow.getByRole('button', { name: 'View permissions' }).click();
	const permissionsDialog = page.getByRole('dialog', { name: 'Plugin permissions' });
	await expect(permissionsDialog.getByText('workspace.read')).toBeVisible();
	await permissionsDialog.getByRole('button', { name: 'Close Plugin permissions' }).click();

	// View manifest: hashes and the raw manifest JSON are exposed for audit.
	await pluginRow.getByRole('button', { name: 'View manifest' }).click();
	const manifestDialog = page.getByRole('dialog', { name: 'Plugin manifest' });
	await expect(manifestDialog.getByText('Manifest hash')).toBeVisible();
	await expect(manifestDialog.getByText(`"id": "${pluginId}"`)).toBeVisible();
	await manifestDialog.getByRole('button', { name: 'Close Plugin manifest' }).click();

	// Entrypoint views aggregate the declared contract across installed plugins.
	await dialog.getByRole('tab', { name: 'Skills', exact: true }).click();
	await expect(dialog.getByText('skills/example/SKILL.md')).toBeVisible();
	await dialog.getByRole('tab', { name: 'Tools', exact: true }).click();
	await expect(dialog.getByText('Fixture Tool')).toBeVisible();
	await expect(dialog.getByText('policy required', { exact: true })).toBeVisible();
});
