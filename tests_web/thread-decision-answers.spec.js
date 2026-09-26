/**
 * How pending thread decisions get answered after the rediseño: options (when the decision has
 * them) or free text (when it does not — the dead end this fixes) live in the execution panel
 * only, a single "Send answers" button resolves everything the operator answered, and the chat
 * shows only the plain-language answer, never the interactive card.
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

// A route handler can still have a fetch in flight when Playwright closes the page at teardown
// (background thread polling). Drop the handlers first so that in-flight call fails quietly
// instead of surfacing as "Target page, context or browser has been closed".
test.afterEach(async ({ page }) => {
	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

async function expectControlPlaneLoaded(page) {
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
}

/** Creates a real thread through the composer and waits for the live layout. */
async function createLiveThread(page, firstMessage) {
	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();
	await page.getByLabel('Message AIDO').fill(firstMessage);
	await page.getByRole('button', { name: 'Create thread' }).click();
	await expect(page.getByText(firstMessage).first()).toBeVisible({ timeout: 20_000 });
}

/**
 * Appends the given `decisions` to whatever the real thread endpoint returns, so the answer form
 * renders deterministically without driving the Product Loop. Returns a setter a test calls once
 * its mocked resolve endpoint answers a decision, so the next poll reports it resolved.
 */
async function injectPendingDecisions(page, decisions) {
	const state = new Map(decisions.map((decision) => [decision.id, { ...decision }]));
	await page.route('**/api/v1/threads/*', async (route) => {
		const response = await route.fetch();
		const detail = await response.json();
		if (!Array.isArray(detail.decisions)) {
			await route.fulfill({ response });
			return;
		}
		for (const decision of state.values()) {
			detail.decisions.push({
				id: decision.id,
				threadId: detail.thread.id,
				projectId: detail.thread.projectId,
				messageId: null,
				title: decision.title,
				status: decision.status,
				prompt: decision.prompt,
				options: decision.options,
				resolution: null,
				decidedBy: null,
				decidedAt: null,
				metadata: {},
				createdAt: '2026-07-09T00:00:00.000Z',
				updatedAt: '2026-07-09T00:00:00.000Z',
			});
		}
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(detail),
		});
	});
	return (id, nextStatus) => {
		const decision = state.get(id);
		if (decision) decision.status = nextStatus;
	};
}

test('Decisions: an intake decision answers from the execution panel and the chat shows only the answer', async ({
	page,
}) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	// An ambiguous first message blocks with a real thread_decision (Diagnosis/Implementation/Research).
	await createLiveThread(page, 'help');

	// The card no longer renders in the chat transcript — only in the execution panel.
	await expect(page.locator('.thread-transcript-pane .thread-decision-console')).toHaveCount(0);
	const answerCard = page.locator('.thread-execution-pane .thread-decision-console').first();
	await expect(answerCard).toBeVisible({ timeout: 20_000 });
	const submit = page
		.locator('.thread-execution-pane')
		.getByRole('button', { name: /Send answers|Enviar respuestas/ });
	await expect(submit).toBeDisabled();

	// decisionOptionLabel maps the "Implementation" option value to the visible label "Implement".
	await answerCard.getByRole('radio', { name: /Implement/i }).check({ force: true });
	await expect(submit).toBeEnabled();
	await submit.click();

	await expect(answerCard).toBeHidden({ timeout: 20_000 });
	// The chat gets the plain-language answer ("<decision title>: Implementation"), not the card.
	await expect(page.locator('.thread-chat-transcript').getByText(/: Implementation/)).toBeVisible({
		timeout: 20_000,
	});
	await expect(page.locator('.thread-transcript-pane .thread-decision-console')).toHaveCount(0);
});

test('Decisions: a decision without options answers with free text instead of a dead end', async ({
	page,
}) => {
	let resolvePayload = null;
	const setStatus = await injectPendingDecisions(page, [
		{
			id: 'decision-no-options',
			title: 'Frontend Framework',
			prompt: 'Which frontend framework should the team use?',
			options: [],
			status: 'pending',
		},
	]);
	await page.route('**/api/v1/threads/*/decisions/decision-no-options/resolve', async (route) => {
		resolvePayload = route.request().postDataJSON();
		setStatus('decision-no-options', 'resolved');
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				thread: { id: 'thread-fixture', status: 'open' },
				decision: {
					id: 'decision-no-options',
					status: 'resolved',
					resolution: 'React + TypeScript',
				},
			}),
		});
	});

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Decision free text ${Date.now()}`);

	const answerCard = page
		.locator('.thread-execution-pane')
		.locator('.thread-decision-console', { hasText: 'Frontend Framework' });
	await expect(answerCard).toBeVisible({ timeout: 20_000 });
	// No options at all: a button list would render nothing, which is the dead end being fixed.
	await expect(answerCard.locator('[role="radiogroup"]')).toHaveCount(0);
	const submit = page
		.locator('.thread-execution-pane')
		.getByRole('button', { name: /Send answers|Enviar respuestas/ });
	await expect(submit).toBeDisabled();

	await answerCard.getByLabel(/Your answer|Tu respuesta/i).fill('React + TypeScript');
	await expect(submit).toBeEnabled();
	await submit.click();

	await expect.poll(() => resolvePayload?.freeText).toBe('React + TypeScript');
	await expect.poll(() => resolvePayload?.selectedOptions).toEqual([]);
	await expect(answerCard).toBeHidden({ timeout: 20_000 });
});

test('Decisions: answering two pending decisions sends them together from one button', async ({
	page,
}) => {
	const payloads = {};
	const setStatus = await injectPendingDecisions(page, [
		{
			id: 'decision-one',
			title: 'Frontend Framework',
			prompt: 'Which frontend framework?',
			options: ['React', 'Vue'],
			status: 'pending',
		},
		{
			id: 'decision-two',
			title: 'Backend Language',
			prompt: 'Which backend language?',
			options: ['Python', 'Go'],
			status: 'pending',
		},
	]);
	for (const id of ['decision-one', 'decision-two']) {
		await page.route(`**/api/v1/threads/*/decisions/${id}/resolve`, async (route) => {
			payloads[id] = route.request().postDataJSON();
			setStatus(id, 'resolved');
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					thread: { id: 'thread-fixture', status: 'open' },
					decision: { id, status: 'resolved' },
				}),
			});
		});
	}

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Decision batch ${Date.now()}`);

	const firstCard = page
		.locator('.thread-execution-pane')
		.locator('.thread-decision-console', { hasText: 'Frontend Framework' });
	const secondCard = page
		.locator('.thread-execution-pane')
		.locator('.thread-decision-console', { hasText: 'Backend Language' });
	await expect(firstCard).toBeVisible({ timeout: 20_000 });
	await expect(secondCard).toBeVisible({ timeout: 20_000 });

	// One "Send answers" button for the whole panel, not one per decision.
	const submitButtons = page
		.locator('.thread-execution-pane')
		.getByRole('button', { name: /Send answers|Enviar respuestas/ });
	await expect(submitButtons).toHaveCount(1);

	await firstCard.getByLabel(/^React$/).check();
	await secondCard.getByLabel(/^Python$/).check();
	await submitButtons.click();

	await expect.poll(() => payloads['decision-one']?.selectedOptions).toEqual(['React']);
	await expect.poll(() => payloads['decision-two']?.selectedOptions).toEqual(['Python']);
	await expect(firstCard).toBeHidden({ timeout: 20_000 });
	await expect(secondCard).toBeHidden({ timeout: 20_000 });
});
