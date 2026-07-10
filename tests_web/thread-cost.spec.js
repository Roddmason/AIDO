/**
 * Cost tab of the Thread Inspector: cost/tokens/latency as an operational decision.
 *
 * Two things must hold on this surface, and neither is provable from the backend alone. First, a value
 * the provider never reported renders as the word "unknown" — never as `$0.00`, `0` tokens or `0 ms`,
 * because a fabricated zero is exactly the number that makes an expensive run look free. Second, the
 * controls that change the next run are real: the mode segmented control and the force-local switch
 * write project settings, and "Allow premium once" stays disabled until the backend actually persists
 * an `approve_resource_decision` remediation — the UI never invents an approval nobody asked for.
 *
 * The `/cost-performance` endpoint is mocked so each scenario is deterministic; the settings write it
 * triggers is asserted against the real control plane.
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

// The AI-manager inspector auto-opens only in the desktop IDE layout (min-width 981px); pin a
// desktop-width viewport so both Playwright projects exercise the inspector instead of the
// stacked mobile fallback that keeps it collapsed.
test.use({ viewport: { width: 1280, height: 800 } });

test.afterEach(async ({ page }) => {
	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

async function expectControlPlaneLoaded(page) {
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
}

/** Creates a real thread through the composer and waits for the live layout + inspector. */
async function createLiveThread(page, firstMessage) {
	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();
	await page.getByLabel('Message AIDO').fill(firstMessage);
	await page.getByRole('button', { name: 'Create thread' }).click();
	await expect(page.getByText(firstMessage).first()).toBeVisible({ timeout: 20_000 });
}

/**
 * A snapshot whose provider reported nothing: no cost, no tokens, no latency. Every honest field is
 * `null`, mirroring the backend contract where an unknown value is never coerced to zero.
 */
function unknownEverythingSnapshot(overrides = {}) {
	return {
		threadId: 'thread-fixture',
		loopIds: ['product-loop-abc123def456'],
		hasData: true,
		budgetUsed: {
			usedUsd: null,
			costStatus: 'unknown',
			callCount: 2,
			perRunCapUsd: null,
			capScope: null,
		},
		cost: { estimatedCostUsd: null, actualCostUsd: null },
		tokens: {
			totalTokens: 0,
			knownCalls: 0,
			unknownCalls: 2,
			tokenStatus: 'unknown',
			callCount: 2,
		},
		latency: {
			p50Ms: null,
			avgMs: null,
			maxMs: null,
			knownCalls: 0,
			unknownCalls: 2,
			latencyStatus: 'unknown',
			callCount: 2,
		},
		modelChosen: { provider: 'mystery_api', model: 'opaque-model', runtime: 'api', effort: null, mode: null },
		reasonSelected: 'Selected mystery_api/opaque-model for developer.',
		cheaperAlternative: null,
		qualityRework: {
			reworkRounds: null,
			maxReworkRounds: null,
			modelSuccessRate: null,
			modelQaPassRate: null,
			modelReworkRate: null,
			benchmarkInsufficientData: true,
			modelLabel: 'mystery_api/opaque-model',
		},
		policy: {
			mode: 'balanced',
			forceLocal: false,
			premiumApprovalOverUsd: 2.0,
			approvalRequired: false,
			premiumApproval: null,
		},
		...overrides,
	};
}

/** Serves a fixed cost/performance snapshot for whichever live thread the inspector queries. */
async function mockCostPerformance(page, costPerformance) {
	await page.route('**/api/v1/threads/*/cost-performance', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ costPerformance }),
		});
	});
}

/** Opens the inspector's Cost tab and returns its panel. */
async function openCostTab(page) {
	const inspector = page.locator('.inspector-panel');
	await expect(inspector.locator('.thread-inspector')).toBeVisible({ timeout: 20_000 });
	await inspector.getByRole('tab', { name: /Cost|Costo/ }).click();
	return inspector;
}

test('Cost: an unknown cost renders as "unknown", never as $0', async ({ page }) => {
	await mockCostPerformance(page, unknownEverythingSnapshot());
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Inspect what this run cost ${Date.now()}`);

	const inspector = await openCostTab(page);
	const panel = inspector.locator('.thread-inspector-stack').first();
	await expect(panel.locator('.thread-cost-model')).toBeVisible({ timeout: 20_000 });

	// The three money/telemetry readouts all say "unknown" — the word, not a zero.
	const unknown = /unknown|desconocido/i;
	for (const term of [/Estimated cost|Costo estimado/, /Actual cost|Costo real/, /Latency p50|Latencia p50/]) {
		const value = panel.locator('.thread-inspector-meta > div', { hasText: term }).locator('dd');
		await expect(value).toHaveText(unknown);
	}

	// The critical negative: nowhere on this panel does a fabricated zero appear as a price or a duration.
	const body = (await panel.innerText()).toLowerCase();
	expect(body).not.toContain('$0.00');
	expect(body).not.toContain('$0.0000');
	expect(body).not.toContain('0 ms');

	// The chosen model and the reason it was chosen are still stated: unknown cost is not unknown routing.
	await expect(panel.locator('.thread-cost-model')).toContainText('opaque-model');
	await expect(panel.locator('.thread-cost-model')).toContainText(/Selected mystery_api/);

	// With no known price there is nothing to compare against, and the panel says so instead of guessing.
	await expect(panel.locator('.thread-cost-alternative')).toHaveCount(0);
});

test('Cost: "Allow premium once" stays disabled until the backend raises a resource approval', async ({
	page,
}) => {
	await mockCostPerformance(
		page,
		unknownEverythingSnapshot({
			policy: {
				mode: 'balanced',
				forceLocal: false,
				premiumApprovalOverUsd: 2.0,
				approvalRequired: true,
				premiumApproval: {
					required: true,
					reason: 'premium_cost_over_policy_threshold',
					thresholdUsd: 2.0,
					estimatedCostUsd: 3.0,
					costTier: 'premium',
				},
			},
		}),
	);
	// No remediation is persisted, so the loop never asked a human to approve anything.
	await page.route('**/api/v1/threads/*/remediations', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ remediations: [] }),
		});
	});

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Gate the premium model ${Date.now()}`);

	const inspector = await openCostTab(page);
	const premium = inspector.getByRole('button', { name: /Allow premium once|Permitir premium una vez/ });
	await expect(premium).toBeVisible({ timeout: 20_000 });
	await expect(premium).toBeDisabled();
	await expect(inspector.getByText(/No premium selection is waiting|Ninguna selección premium/)).toBeVisible();

	// The policy verdict is still surfaced, so the operator knows why the run is gated. Scope to the
	// Cost strip by its accessible name: the pinned loop-vitals strip shares the same class.
	await expect(inspector.getByRole('group', { name: /Cost decision|Decisión de costo/ })).toContainText(
		/Approval required|Requiere aprobación/,
	);
});

test('Cost: switching to Economy mode persists the project setting that governs the next run', async ({
	page,
}) => {
	await mockCostPerformance(page, unknownEverythingSnapshot());
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Make the next run cheap ${Date.now()}`);

	const inspector = await openCostTab(page);
	const economy = inspector.getByRole('radio', { name: /Economy|Economía/ });
	await expect(economy).toBeVisible({ timeout: 20_000 });

	const settingWrite = page.waitForResponse(
		(response) =>
			response.url().includes('/api/v1/settings/project.loop.teamMode') &&
			response.request().method() === 'PUT',
	);
	await economy.click();
	expect((await settingWrite).status()).toBe(204);

	// The write landed on the real control plane, not just in component state.
	const projects = await (await page.request.get('/api/v1/projects')).json();
	const projectId = projects.projects[0].id;
	const resolved = await (await page.request.get(`/api/v1/settings?projectId=${projectId}`)).json();
	const teamMode = resolved.project.find((setting) => setting.key === 'project.loop.teamMode');
	expect(teamMode.value).toBe('economy');
	expect(teamMode.source).toBe('project');
});
