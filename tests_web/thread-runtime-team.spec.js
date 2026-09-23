/**
 * Thread AI team: the composer chip opens a drawer where only freshly validated runtimes can be
 * selected, expired ones are re-tested on open, the backend split preloads the role grid, and a new
 * thread persists its team before the first message. Candidates, validate-runtime and
 * run-configuration are mocked so the scenarios never depend on real provider credentials; the
 * thread and its first message go to the real control plane.
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 1280, height: 800 } });

test.afterEach(async ({ page }) => {
	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

const VALIDATED = {
	status: 'validated',
	checkedAt: '2026-09-22T10:00:00+00:00',
	latencyMs: 420,
	model: 'm',
	reason: null,
};
const STALE = {
	status: 'stale',
	checkedAt: '2026-09-22T08:00:00+00:00',
	latencyMs: 900,
	model: 'm',
	reason: 'runtime_validation_expired',
};

const NO_SPLIT = { developer: null, product_owner: null, architect: null, security: null };
// Backend split per sorted selection, as `auto_assign_roles` (local_control_center/runtime_team/roles.py)
// actually computes it: CLI ranks first, then ties break on provider_id; each role takes the first
// eligible runtime not yet used, recycling from the top of that ranking once every runtime is used.
const SPLITS = {
	claude_code_cli: {
		developer: 'claude_code_cli',
		product_owner: 'claude_code_cli',
		architect: 'claude_code_cli',
		security: null,
	},
	'claude_code_cli,omniroute': {
		developer: 'claude_code_cli',
		product_owner: 'omniroute',
		architect: 'claude_code_cli',
		security: 'omniroute',
	},
	'claude_code_cli,nvidia_nim,omniroute': {
		developer: 'claude_code_cli',
		product_owner: 'nvidia_nim',
		architect: 'omniroute',
		security: 'nvidia_nim',
	},
};

function splitFor(selected) {
	if (selected === null) return NO_SPLIT;
	const key = selected.split(',').filter(Boolean).sort().join(',');
	return SPLITS[key] ?? NO_SPLIT;
}

function candidatesBody(nimValidation, selected) {
	return {
		candidates: [
			{
				providerId: 'claude_code_cli',
				label: 'Claude Code CLI',
				kind: 'cli',
				validation: VALIDATED,
				eligibleRoles: ['product_owner', 'developer', 'architect'],
			},
			{
				providerId: 'omniroute',
				label: 'OmniRoute',
				kind: 'gateway',
				validation: VALIDATED,
				eligibleRoles: ['product_owner', 'developer', 'architect', 'security'],
			},
			{
				providerId: 'nvidia_nim',
				label: 'NVIDIA NIM',
				kind: 'api',
				validation: nimValidation,
				eligibleRoles: ['product_owner', 'architect', 'security'],
			},
		],
		freshnessSeconds: 1800,
		suggestedRoleRuntimes: splitFor(selected),
	};
}

async function mockRuntimeTeam(page, { probeSucceeds, holdPatch = false }) {
	const state = { nimValidation: STALE, probes: [], patches: [], order: [] };
	// When holdPatch is set, the run-configuration route parks on this promise before fulfilling, so a
	// test can prove the message POST really waits for the PATCH response instead of only being fired
	// after the PATCH request; state.releasePatch lets the test open the gate when it is ready to check
	// what happens next.
	state.patchGate = holdPatch
		? new Promise((resolve) => {
				state.releasePatch = resolve;
			})
		: Promise.resolve();
	page.on('request', (request) => {
		const url = request.url();
		if (url.includes('/run-configuration')) state.order.push('patch');
		if (url.includes('/messages') && request.method() === 'POST') state.order.push('message');
	});
	await page.route('**/api/v1/runtime/team-candidates**', async (route) => {
		const selected = new URL(route.request().url()).searchParams.get('selected');
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(candidatesBody(state.nimValidation, selected)),
		});
	});
	await page.route('**/api/v1/model-gateway/providers/*/validate-runtime', async (route) => {
		const providerId = route.request().url().split('/providers/')[1].split('/')[0];
		state.probes.push(providerId);
		if (probeSucceeds) state.nimValidation = VALIDATED;
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				validation: {
					providerId,
					kind: 'api',
					status: probeSucceeds ? 'validated' : 'failed',
					model: 'm',
					latencyMs: 300,
					reason: probeSucceeds ? null : 'runtime_auth_missing',
					checkedAt: '2026-09-22T10:05:00+00:00',
				},
			}),
		});
	});
	await page.route('**/api/v1/threads/*/run-configuration', async (route) => {
		const body = route.request().postDataJSON();
		state.patches.push(body);
		const threadId = route.request().url().split('/threads/')[1].split('/')[0];
		await state.patchGate;
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				threadId,
				runtimeTeam: { allowedRuntimes: body.allowedRuntimes, roleRuntimes: body.roleRuntimes },
			}),
		});
	});
	return state;
}

async function openNewThreadTeamPanel(page) {
	await page.goto('/#threads');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();
	await page.getByRole('button', { name: /AI team/ }).click();
	const panel = page.getByRole('dialog', { name: 'AI team for this thread' });
	await expect(panel).toBeVisible();
	return panel;
}

test('an expired runtime is re-tested on open and stays disabled while its test fails', async ({ page }) => {
	const state = await mockRuntimeTeam(page, { probeSucceeds: false });
	const panel = await openNewThreadTeamPanel(page);
	await expect.poll(() => state.probes).toContain('nvidia_nim');
	expect(state.probes).not.toContain('claude_code_cli');
	await expect(panel.getByRole('checkbox', { name: /NVIDIA NIM/ })).toBeDisabled();
	await expect(panel.getByRole('checkbox', { name: /Claude Code CLI/ })).toBeEnabled();
});

test('a successful automatic re-test makes the runtime selectable', async ({ page }) => {
	await mockRuntimeTeam(page, { probeSucceeds: true });
	const panel = await openNewThreadTeamPanel(page);
	await expect(panel.getByRole('checkbox', { name: /NVIDIA NIM/ })).toBeEnabled();
});

test('saving and sending with a single runtime covering PO and Developer omits Security from the PATCH', async ({
	page,
}) => {
	const state = await mockRuntimeTeam(page, { probeSucceeds: false, holdPatch: true });
	const panel = await openNewThreadTeamPanel(page);
	await panel.getByRole('checkbox', { name: /Claude Code CLI/ }).check();
	await expect(panel.getByRole('combobox', { name: 'Product Owner' })).toHaveValue('claude_code_cli');
	await expect(panel.getByRole('combobox', { name: 'Developer' })).toHaveValue('claude_code_cli');
	await expect(panel.getByRole('combobox', { name: 'Architect' })).toHaveValue('claude_code_cli');
	// Security is optional: one runtime covering Product Owner and Developer is already a sendable team.
	await expect(panel.getByRole('combobox', { name: 'Security' })).toHaveValue('');
	await expect(panel.getByRole('button', { name: 'Save team' })).toBeEnabled();
	await panel.getByRole('button', { name: 'Save team' }).click();
	await expect(panel).toBeHidden();
	await expect(page.getByRole('button', { name: 'AI team · 1 of 3' })).toBeVisible();

	const objective = `Runtime team single-runtime spec ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(objective);
	await page.getByRole('button', { name: 'Create thread' }).click();
	// The PATCH request has fired but its response is parked behind patchGate: prove the message POST
	// really waits for it to resolve, not just for it to be dispatched.
	await expect.poll(() => state.patches.length).toBe(1);
	expect(state.order).not.toContain('message');
	state.releasePatch();
	// The draft textarea still shows the objective while the thread is being created, so wait for
	// the intake view itself to be replaced before trusting a match on that text.
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeHidden({
		timeout: 20_000,
	});
	await expect(page.getByText(objective).first()).toBeVisible({ timeout: 20_000 });
	expect(state.patches[0]).toEqual({
		allowedRuntimes: ['claude_code_cli'],
		roleRuntimes: {
			product_owner: 'claude_code_cli',
			developer: 'claude_code_cli',
			architect: 'claude_code_cli',
		},
	});
	expect(state.order.indexOf('patch')).toBeLessThan(state.order.indexOf('message'));
});

test('adding a runtime redistributes the automatic roles and a role pinned by hand survives it', async ({
	page,
}) => {
	const state = await mockRuntimeTeam(page, { probeSucceeds: false, holdPatch: true });
	const panel = await openNewThreadTeamPanel(page);
	await panel.getByRole('checkbox', { name: /Claude Code CLI/ }).check();
	await expect(panel.getByRole('combobox', { name: 'Product Owner' })).toHaveValue('claude_code_cli');
	// Security is optional: one runtime covering Product Owner and Developer is already a sendable team.
	await expect(panel.getByRole('combobox', { name: 'Security' })).toHaveValue('');
	await expect(panel.getByRole('button', { name: 'Save team' })).toBeEnabled();
	await panel.getByRole('checkbox', { name: /OmniRoute/ }).check();
	await expect(panel.getByRole('combobox', { name: 'Developer' })).toHaveValue('claude_code_cli');
	await expect(panel.getByRole('combobox', { name: 'Architect' })).toHaveValue('claude_code_cli');
	await expect(panel.getByRole('combobox', { name: 'Product Owner' })).toHaveValue('omniroute');
	await expect(panel.getByRole('combobox', { name: 'Security' })).toHaveValue('omniroute');
	// Pin Architect by hand to OmniRoute; the automatic split would keep it on the CLI.
	await panel.getByRole('combobox', { name: 'Architect' }).selectOption('omniroute');
	await panel.getByRole('button', { name: 'Save team' }).click();
	await expect(panel).toBeHidden();
	await expect(page.getByRole('button', { name: 'AI team · 2 of 3' })).toBeVisible();

	const objective = `Runtime team spec ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(objective);
	await page.getByRole('button', { name: 'Create thread' }).click();
	// The PATCH request has fired but its response is parked behind patchGate: prove the message POST
	// really waits for it to resolve, not just for it to be dispatched.
	await expect.poll(() => state.patches.length).toBe(1);
	expect(state.order).not.toContain('message');
	state.releasePatch();
	// The draft textarea still shows the objective while the thread is being created, so wait for
	// the intake view itself to be replaced before trusting a match on that text.
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeHidden({
		timeout: 20_000,
	});
	await expect(page.getByText(objective).first()).toBeVisible({ timeout: 20_000 });
	expect(state.patches[0]).toEqual({
		allowedRuntimes: ['claude_code_cli', 'omniroute'],
		roleRuntimes: {
			product_owner: 'omniroute',
			developer: 'claude_code_cli',
			architect: 'omniroute',
			security: 'omniroute',
		},
	});
	expect(state.order.indexOf('patch')).toBeLessThan(state.order.indexOf('message'));
});

test('a role edited by hand stays pinned while the automatic roles follow the new split', async ({ page }) => {
	await mockRuntimeTeam(page, { probeSucceeds: true });
	const panel = await openNewThreadTeamPanel(page);
	await expect(panel.getByRole('checkbox', { name: /NVIDIA NIM/ })).toBeEnabled();
	await panel.getByRole('checkbox', { name: /Claude Code CLI/ }).check();
	await panel.getByRole('checkbox', { name: /OmniRoute/ }).check();
	await expect(panel.getByRole('combobox', { name: 'Product Owner' })).toHaveValue('omniroute');
	// Pin Product Owner by hand to a value that differs from the current automatic split — a genuine
	// edit, not a no-op re-selection of the value the combobox already holds.
	await panel.getByRole('combobox', { name: 'Product Owner' }).selectOption('claude_code_cli');
	await panel.getByRole('checkbox', { name: /NVIDIA NIM/ }).check();
	// The wider selection would automatically move Product Owner to NVIDIA NIM (see SPLITS); the
	// manual pin holds instead, while Architect (never touched by hand) follows the new split.
	await expect(panel.getByRole('combobox', { name: 'Architect' })).toHaveValue('omniroute');
	await expect(panel.getByRole('combobox', { name: 'Product Owner' })).toHaveValue('claude_code_cli');
	await panel.getByRole('button', { name: 'Assign automatically' }).click();
	await expect(panel.getByRole('combobox', { name: 'Product Owner' })).toHaveValue('nvidia_nim');
});

test('switching back to automatic routing after a failed first message clears the saved team', async ({
	page,
}) => {
	const state = await mockRuntimeTeam(page, { probeSucceeds: false });
	let rejectedMessages = 0;
	await page.route('**/api/v1/threads/*/messages', async (route) => {
		if (route.request().method() === 'POST' && rejectedMessages === 0) {
			rejectedMessages += 1;
			await route.fulfill({
				status: 422,
				contentType: 'application/json',
				body: JSON.stringify({ detail: 'Roles without a freshly validated runtime: developer.' }),
			});
			return;
		}
		await route.fallback();
	});
	const panel = await openNewThreadTeamPanel(page);
	await panel.getByRole('checkbox', { name: /Claude Code CLI/ }).check();
	await expect(panel.getByRole('combobox', { name: 'Developer' })).toHaveValue('claude_code_cli');
	await panel.getByRole('button', { name: 'Save team' }).click();
	await expect(panel).toBeHidden();

	const objective = `Runtime team automatic retry spec ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(objective);
	await page.getByRole('button', { name: 'Create thread' }).click();
	await expect(page.getByText('Could not send the message. Try again.')).toBeVisible({ timeout: 20_000 });
	expect(state.patches).toHaveLength(1);

	await page.getByRole('button', { name: /AI team/ }).click();
	const reopened = page.getByRole('dialog', { name: 'AI team for this thread' });
	await reopened.getByRole('button', { name: 'Use automatic routing' }).click();
	await reopened.getByRole('button', { name: 'Save team' }).click();
	await expect(reopened).toBeHidden();
	await expect(page.getByRole('button', { name: 'AI team · automatic' })).toBeVisible();
	await page.getByRole('button', { name: 'Create thread' }).click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeHidden({
		timeout: 20_000,
	});
	expect(state.patches).toHaveLength(2);
	expect(state.patches[1]).toEqual({ allowedRuntimes: [], roleRuntimes: {} });
	expect(state.order.lastIndexOf('patch')).toBeLessThan(state.order.lastIndexOf('message'));
});
