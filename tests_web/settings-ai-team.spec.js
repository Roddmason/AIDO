/**
 * Global AI team settings: per role, an ordered list of active providers (first = assigned, rest =
 * fallback) editable at general and project scope; "Automatic" clears the override. The team
 * endpoint and the settings writes are mocked; the Settings modal is the real one.
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 1280, height: 800 } });

test.afterEach(async ({ page }) => {
	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

const CANDIDATE = (providerId, label, kind, eligibleRoles) => ({
	providerId,
	label,
	kind,
	validation: {
		status: 'validated',
		checkedAt: '2026-09-26T10:00:00+00:00',
		latencyMs: 300,
		model: 'm',
		reason: null,
	},
	eligibleRoles,
	loadedModels: [],
});

function teamBody(developerOrder, source, developerInvalid = []) {
	const role = (name, required, effective, roleSource, candidates, invalid = []) => ({
		role: name,
		required,
		configured:
			roleSource === 'automatic_fallback'
				? invalid
				: roleSource === 'automatic' || roleSource === 'inherited'
					? []
					: effective,
		effective,
		assigned: effective[0] ?? null,
		source: roleSource,
		invalid,
		candidates,
	});
	const all = ['claude_code_cli', 'codex_cli', 'llama_cpp'];
	return {
		roles: [
			role('product_owner', true, ['codex_cli', 'llama_cpp'], 'automatic', all),
			role('developer', true, developerOrder, source, all, developerInvalid),
			role('architect', false, ['claude_code_cli'], 'automatic', ['claude_code_cli']),
			role('security', false, ['llama_cpp'], 'automatic', ['llama_cpp']),
			role('technical_lead', false, ['codex_cli', 'llama_cpp'], 'inherited', all),
			role('researcher', false, ['codex_cli', 'llama_cpp'], 'inherited', all),
		],
		allowedRuntimes: all,
		activeProviders: 3,
		candidates: [
			CANDIDATE('claude_code_cli', 'Claude Code CLI', 'cli', ['product_owner', 'developer', 'architect']),
			CANDIDATE('codex_cli', 'Codex CLI', 'cli', ['product_owner', 'developer']),
			CANDIDATE('llama_cpp', 'llama.cpp', 'local', ['product_owner', 'developer', 'security']),
		],
	};
}

async function openGeneralAiTeam(page, state) {
	// Regex, not a glob: `**/runtime/team**` would also swallow `/runtime/team-candidates`.
	await page.route(/\/api\/v1\/runtime\/team(\?.*)?$/, (route) =>
		route.fulfill({ json: teamBody(state.developer, state.source, state.invalid ?? []) }),
	);
	await page.route('**/api/v1/settings/team.role.*', async (route) => {
		const key = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-1));
		if (route.request().method() === 'PUT') {
			const body = route.request().postDataJSON();
			state.writes.push({ key, scope: body.scope, value: body.value });
			state.developer = body.value;
			state.source = 'general';
		} else if (route.request().method() === 'DELETE') {
			state.writes.push({ key, scope: 'general', value: null });
			state.developer = ['claude_code_cli', 'codex_cli', 'llama_cpp'];
			state.source = 'automatic';
			state.invalid = [];
		}
		await route.fulfill({ status: 204, body: '' });
	});
	await page.goto('/#settings');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const settings = page.getByRole('dialog', { name: 'Settings' });
	await expect(settings).toBeVisible();
	await settings
		.locator('nav[aria-label="Settings sections"]')
		.getByRole('button', { name: 'AI team', exact: true })
		.click();
	return settings.getByRole('region', { name: 'AI team' });
}

test('the AI team panel lists each role with its assigned provider and source', async ({ page }) => {
	const state = { developer: ['claude_code_cli', 'codex_cli', 'llama_cpp'], source: 'automatic', writes: [] };
	const panel = await openGeneralAiTeam(page, state);
	const developer = panel.getByRole('group', { name: 'Developer' });
	await expect(developer).toContainText('Claude Code CLI');
	await expect(developer.getByText('automatic', { exact: true })).toBeVisible();
	await expect(
		panel.getByRole('group', { name: 'Technical Lead' }).getByText('inherits the Product Owner', { exact: true }),
	).toBeVisible();
});

test('adding a provider to a role writes the ordered list and "Automatic" clears it', async ({ page }) => {
	const state = { developer: ['claude_code_cli', 'codex_cli', 'llama_cpp'], source: 'automatic', writes: [] };
	const panel = await openGeneralAiTeam(page, state);
	const developer = panel.getByRole('group', { name: 'Developer' });
	await developer.getByRole('combobox', { name: 'Add provider' }).selectOption('codex_cli');
	await developer.getByRole('button', { name: 'Add', exact: true }).click();
	await expect
		.poll(() => state.writes)
		.toEqual([{ key: 'team.role.developer', scope: 'general', value: ['codex_cli'] }]);
	await expect(developer.getByText('general', { exact: true })).toBeVisible();
	await developer.getByRole('button', { name: 'Automatic', exact: true }).click();
	await expect.poll(() => state.writes.length).toBe(2);
	expect(state.writes[1]).toEqual({ key: 'team.role.developer', scope: 'general', value: null });
});

test('a role whose chosen providers are all switched off says it fell back to automatic', async ({ page }) => {
	const state = {
		developer: ['claude_code_cli', 'codex_cli', 'llama_cpp'],
		source: 'automatic_fallback',
		invalid: ['gemini'],
		writes: [],
	};
	const panel = await openGeneralAiTeam(page, state);
	const developer = panel.getByRole('group', { name: 'Developer' });
	await expect(developer.getByText('automatic (your selection is off)', { exact: true })).toBeVisible();
	await expect(developer.getByText('Your selection is switched off; using automatic.')).toBeVisible();
	await expect(developer.getByText('Ignored (inactive or not eligible): gemini')).toBeVisible();
	await expect(developer).toContainText('Claude Code CLI');
	// The dead selection can be cleared even though none of it is shown as an editable row.
	await developer.getByRole('button', { name: 'Automatic', exact: true }).click();
	await expect
		.poll(() => state.writes)
		.toEqual([{ key: 'team.role.developer', scope: 'general', value: null }]);
	await expect(developer.getByText('Your selection is switched off; using automatic.')).toBeHidden();
});
