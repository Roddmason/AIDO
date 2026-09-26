/**
 * How pending thread decisions get answered after the rediseño: options (when the decision has
 * them) or free text (when it does not — the dead end this fixes) live in the execution panel
 * only, several choices are checkboxes for Product Owner questions/decisions (radio for the
 * mutually-exclusive ones), "Other answer" free text is always offered alongside a Product Owner
 * decision's options, a single "Send answers" button resolves everything the operator answered
 * together, and the chat gets one combined message — never the interactive card, never one message
 * per decision.
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
 * Fakes just enough of the thread endpoint to render pending decisions deterministically and
 * resolve them through the real batch endpoint contract, without driving the Product Loop:
 *   - GET /threads/{id} appends the given `decisions` (mutable status) and any message the mocked
 *     batch resolve queued, on top of whatever the real backend returns.
 *   - POST /threads/{id}/decisions/resolve-batch marks every answered decision resolved and queues
 *     the one combined chat message the real endpoint would have written, so the next poll shows it.
 * Returns a getter for the last batch payload the page sent.
 */
async function mockThreadDecisions(page, decisions) {
	const state = new Map(decisions.map((decision) => [decision.id, { ...decision }]));
	const queuedMessages = [];
	let lastPayload = null;
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
				metadata: decision.metadata ?? {},
				createdAt: '2026-07-09T00:00:00.000Z',
				updatedAt: '2026-07-09T00:00:00.000Z',
			});
		}
		for (const content of queuedMessages) {
			detail.messages.push({
				id: `answer-message-${detail.messages.length}`,
				threadId: detail.thread.id,
				projectId: detail.thread.projectId,
				sequence: detail.messages.length + 1,
				kind: 'user',
				author: 'user',
				content,
				metadata: {},
				createdAt: '2026-07-09T00:00:01.000Z',
			});
		}
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(detail),
		});
	});
	await page.route('**/api/v1/threads/*/decisions/resolve-batch', async (route) => {
		lastPayload = route.request().postDataJSON();
		const lines = lastPayload.answers.map((answer) => {
			const decision = state.get(answer.decisionId);
			decision.status = 'resolved';
			const parts = [...answer.selectedOptions];
			if (answer.freeText) parts.push(answer.freeText);
			return `${decision.title}: ${parts.join(', ')}`;
		});
		queuedMessages.push(lines.join('\n'));
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				thread: { id: 'thread-fixture', status: 'open' },
				decisions: lastPayload.answers.map((answer) => ({
					id: answer.decisionId,
					status: 'resolved',
				})),
			}),
		});
	});
	return () => lastPayload;
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

	// Intake is mutually exclusive (Diagnosis/Implementation/Research): a radio, not a checkbox.
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
	const getPayload = await mockThreadDecisions(page, [
		{
			id: 'decision-no-options',
			title: 'Frontend Framework',
			prompt: 'Which frontend framework should the team use?',
			options: [],
			metadata: { source: 'product_owner_agent' },
			status: 'pending',
		},
	]);

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Decision free text ${Date.now()}`);

	const answerCard = page
		.locator('.thread-execution-pane')
		.locator('.thread-decision-console', { hasText: 'Frontend Framework' });
	await expect(answerCard).toBeVisible({ timeout: 20_000 });
	// No options at all: a button/checkbox list would render nothing, which is the dead end being fixed.
	await expect(answerCard.locator('[role="radiogroup"]')).toHaveCount(0);
	await expect(answerCard.locator('fieldset')).toHaveCount(0);
	const submit = page
		.locator('.thread-execution-pane')
		.getByRole('button', { name: /Send answers|Enviar respuestas/ });
	await expect(submit).toBeDisabled();

	await answerCard.getByLabel(/Your answer|Tu respuesta/i).fill('React + TypeScript');
	await expect(submit).toBeEnabled();
	await submit.click();

	await expect
		.poll(() => getPayload()?.answers)
		.toEqual([{ decisionId: 'decision-no-options', selectedOptions: [], freeText: 'React + TypeScript' }]);
	await expect(answerCard).toBeHidden({ timeout: 20_000 });
});

test('Decisions: answering two pending decisions sends them together in one chat message', async ({
	page,
}) => {
	const getPayload = await mockThreadDecisions(page, [
		{
			id: 'decision-one',
			title: 'Frontend Framework',
			prompt: 'Which frontend framework?',
			options: ['React', 'Vue'],
			metadata: { source: 'product_owner_agent' },
			status: 'pending',
		},
		{
			id: 'decision-two',
			title: 'Backend Language',
			prompt: 'Which backend language?',
			options: [],
			metadata: { source: 'product_owner_agent' },
			status: 'pending',
		},
	]);

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

	// One "Send answers" button for the whole panel, not one per decision — and a single request.
	const submitButtons = page
		.locator('.thread-execution-pane')
		.getByRole('button', { name: /Send answers|Enviar respuestas/ });
	await expect(submitButtons).toHaveCount(1);

	await firstCard.getByLabel(/^React$/).check();
	await secondCard.getByLabel(/Your answer|Tu respuesta/i).fill('Python');
	await submitButtons.click();

	await expect
		.poll(() => getPayload()?.answers)
		.toEqual([
			{ decisionId: 'decision-one', selectedOptions: ['React'], freeText: undefined },
			{ decisionId: 'decision-two', selectedOptions: [], freeText: 'Python' },
		]);
	await expect(firstCard).toBeHidden({ timeout: 20_000 });
	await expect(secondCard).toBeHidden({ timeout: 20_000 });
	// One combined message with a line per decision — not two separate chat entries.
	const chat = page.locator('.thread-chat-transcript');
	await expect(chat.getByText('Frontend Framework: React')).toBeVisible({ timeout: 20_000 });
	await expect(chat.getByText('Backend Language: Python')).toBeVisible();
});

test('Decisions: checking several options for a Product Owner question sends them all together', async ({
	page,
}) => {
	const getPayload = await mockThreadDecisions(page, [
		{
			id: 'decision-multi',
			title: 'Frontend Framework',
			prompt: 'Which frontend framework(s) should the team evaluate?',
			options: ['React + TypeScript', 'Vue + TypeScript', 'Svelte'],
			metadata: { source: 'product_owner_agent' },
			status: 'pending',
		},
	]);

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Decision checkbox ${Date.now()}`);

	const answerCard = page
		.locator('.thread-execution-pane')
		.locator('.thread-decision-console', { hasText: 'Frontend Framework' });
	await expect(answerCard).toBeVisible({ timeout: 20_000 });
	// Product Owner questions/decisions answer with checkboxes (several choices), not a radiogroup.
	await expect(answerCard.locator('[role="radiogroup"]')).toHaveCount(0);
	await expect(answerCard.getByRole('checkbox', { name: /^React \+ TypeScript/ })).toBeVisible();

	await answerCard.getByRole('checkbox', { name: /^React \+ TypeScript/ }).check();
	await answerCard.getByRole('checkbox', { name: /^Svelte/ }).check();
	const submit = page
		.locator('.thread-execution-pane')
		.getByRole('button', { name: /Send answers|Enviar respuestas/ });
	await expect(submit).toBeEnabled();
	await submit.click();

	await expect
		.poll(() => getPayload()?.answers)
		.toEqual([
			{
				decisionId: 'decision-multi',
				selectedOptions: ['React + TypeScript', 'Svelte'],
				freeText: undefined,
			},
		]);
	await expect(answerCard).toBeHidden({ timeout: 20_000 });
	await expect(
		page.locator('.thread-chat-transcript').getByText('Frontend Framework: React + TypeScript, Svelte'),
	).toBeVisible({ timeout: 20_000 });
});

test('Decisions: a Product Owner question with options still offers free text for "none of the above"', async ({
	page,
}) => {
	const getPayload = await mockThreadDecisions(page, [
		{
			id: 'decision-other-answer',
			title: 'Frontend Framework',
			prompt: 'Which frontend framework should the team use?',
			options: ['React + TypeScript', 'Vue + TypeScript'],
			metadata: { source: 'product_owner_agent' },
			status: 'pending',
		},
	]);

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Decision other answer ${Date.now()}`);

	const answerCard = page
		.locator('.thread-execution-pane')
		.locator('.thread-decision-console', { hasText: 'Frontend Framework' });
	await expect(answerCard).toBeVisible({ timeout: 20_000 });
	const otherAnswer = answerCard.getByLabel(/Other answer|Otra respuesta/i);
	await expect(otherAnswer).toBeVisible();
	const submit = page
		.locator('.thread-execution-pane')
		.getByRole('button', { name: /Send answers|Enviar respuestas/ });
	await expect(submit).toBeDisabled();

	await otherAnswer.fill('Actually, use Svelte instead');
	await expect(submit).toBeEnabled();
	await submit.click();

	await expect
		.poll(() => getPayload()?.answers)
		.toEqual([
			{
				decisionId: 'decision-other-answer',
				selectedOptions: [],
				freeText: 'Actually, use Svelte instead',
			},
		]);
	await expect(answerCard).toBeHidden({ timeout: 20_000 });
});
