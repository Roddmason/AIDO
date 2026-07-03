/**
 * Actionable blocker remediations in the Thread Inspector.
 *
 * When a thread is blocked, `/threads/{id}/remediations` returns persisted repair actions that the
 * inspector renders as blocker cards pinned above the manager tabs. This spec drives the three
 * headline blocker types against a mocked remediations endpoint (so each blocker is deterministic)
 * and asserts the card exposes the right primary repair: a runtime block opens Providers & CLI, an
 * uninitialized Git repo offers "Initialize Git", and a stopped worker offers "Run now".
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

// The AI-manager inspector auto-opens only in the desktop IDE layout (min-width 981px); pin a
// desktop-width viewport so both Playwright projects exercise the inspector and its blocker cards.
test.use({ viewport: { width: 1280, height: 800 } });

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

/** One persisted remediation record shaped like the backend contract. */
function remediation(overrides) {
	return {
		id: 'remediation-fixture',
		projectId: 'project-fixture',
		threadId: 'thread-fixture',
		loopId: 'loop-fixture',
		stage: 'runtime',
		blockerType: 'runtime_not_executable',
		title: 'Repair action',
		description: 'A persisted repair action.',
		actionType: 'validate_runtime',
		payload: {},
		status: 'pending',
		createdAt: '2026-07-03T00:00:00.000Z',
		resolvedAt: null,
		...overrides,
	};
}

/** Serves a fixed remediations list for whichever live thread the inspector queries. */
async function mockRemediations(page, remediations) {
	await page.route('**/api/v1/threads/*/remediations', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ remediations }),
		});
	});
}

/** The blocker card that carries the given title text, scoped to the inspector pane. */
function blockerCard(page, titlePattern) {
	return page
		.locator('.inspector-panel')
		.locator('.thread-remediation-card', { hasText: titlePattern });
}

test('Remediations: a blocked runtime card opens Providers & CLI', async ({ page }) => {
	await mockRemediations(page, [
		remediation({
			stage: 'runtime',
			blockerType: 'runtime_not_executable',
			actionType: 'validate_runtime',
			title: 'Validate runtime',
			description: 'Re-check local runtime availability.',
			payload: {
				reason: 'No executable runtime is currently available.',
				details: { selectedRuntimeId: 'codex-cli' },
			},
		}),
	]);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Runtime blocker remediation ${Date.now()}`);

	// The runtime blocker card renders with its plain-language title and the "Configure runtime"
	// primary that routes into the Providers & CLI settings section.
	const card = blockerCard(page, /No executable runtime|Sin runtime ejecutable/);
	await expect(card).toBeVisible({ timeout: 20_000 });
	const configure = card.getByRole('button', { name: /Configure runtime|Configurar runtime/ });
	await expect(configure).toBeVisible();

	await configure.click();

	// Clicking it opens the Settings dialog at Providers & CLI — the runtime's real recovery path.
	const settings = page.getByRole('dialog', { name: 'Settings' });
	await expect(settings).toBeVisible({ timeout: 10_000 });
	await expect(settings.getByText(/Providers & CLI/).first()).toBeVisible();
});

test('Remediations: git_not_initialized offers Initialize Git', async ({ page }) => {
	await mockRemediations(page, [
		remediation({
			stage: 'git',
			blockerType: 'git_not_initialized',
			actionType: 'git_init',
			title: 'Initialize Git repository',
			description: 'Create Git metadata in the project folder.',
			payload: { reason: 'Not a git repository.' },
		}),
	]);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Git init blocker remediation ${Date.now()}`);

	const card = blockerCard(page, /Git is not initialized|Git no está inicializado/);
	await expect(card).toBeVisible({ timeout: 20_000 });
	await expect(
		card.getByRole('button', { name: /Initialize Git|Inicializar Git/ }),
	).toBeVisible();
});

test('Remediations: a stopped worker offers Run now', async ({ page }) => {
	await mockRemediations(page, [
		remediation({
			stage: 'worker',
			blockerType: 'worker_not_running',
			actionType: 'run_worker_once',
			title: 'Run worker once',
			description: 'This thread is queued, but the local worker is not running.',
			payload: { reason: 'Local worker runtime is not running.' },
		}),
	]);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Worker stopped remediation ${Date.now()}`);

	const card = blockerCard(page, /Worker is not running|El worker no está corriendo/);
	await expect(card).toBeVisible({ timeout: 20_000 });
	await expect(card.getByRole('button', { name: /Run now|Ejecutar ahora/ })).toBeVisible();
});
