/**
 * A single product-loop block leaves 3-4 separate console records milliseconds apart (the generic
 * `state_changed` event, one or more `blocked` events with progressively more detail, and the
 * worker's `kind=error` message) — all sharing the same loop and the same reason text. This spec
 * seeds that exact shape against the real control plane and verifies the execution console folds it
 * into one failure row, while two genuinely distinct failures (different reason, different loop)
 * stay two separate rows.
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 1280, height: 800 } });

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

/** The 3 thread-agent-events a real `_block_run` leaves for one failure (state_changed + 2 blocked). */
function blockedFailureEvents({ loopId, reason, stage, startSequence, startAtMs }) {
	const iso = (offsetMs) => new Date(startAtMs + offsetMs).toISOString();
	return [
		{
			id: `${loopId}-state-changed`,
			projectId: 'grouping-project',
			sequence: startSequence,
			type: 'state_changed',
			agentRole: 'aido_lead',
			payload: { loopId, fromState: 'discovery', toState: 'blocked', reason, trigger: `${stage}_blocked` },
			metadata: {},
			createdAt: iso(0),
		},
		{
			id: `${loopId}-blocked-transition`,
			projectId: 'grouping-project',
			sequence: startSequence + 1,
			type: 'blocked',
			agentRole: 'aido_lead',
			payload: {
				loopId,
				fromState: 'discovery',
				toState: 'blocked',
				reason,
				trigger: `${stage}_blocked`,
				status: 'blocked',
			},
			metadata: {},
			createdAt: iso(60),
		},
		{
			id: `${loopId}-blocked-detail`,
			projectId: 'grouping-project',
			sequence: startSequence + 2,
			type: 'blocked',
			agentRole: 'aido_lead',
			payload: { loopId, stage, reason, evidencePackageId: `evidence-${loopId}` },
			metadata: {},
			createdAt: iso(140),
		},
	];
}

/** Routes the incremental event stream from a fixed, already-computed list of events. */
async function routeFixedEventStream(page, events) {
	const lastSeq = events.reduce((max, event) => Math.max(max, event.sequence), 0);
	await page.route('**/api/v1/threads/*/events?*', async (route) => {
		const url = new URL(route.request().url());
		const afterSeq = Number(url.searchParams.get('afterSeq'));
		const pending = events.filter((event) => event.sequence > afterSeq);
		await route.fulfill({ json: { events: pending, lastSeq, running: false, threadStatus: 'blocked' } });
	});
}

/** Appends a synthetic `kind=error` execution message (the worker's finishing message) to the real thread detail. */
async function routeDetailWithErrorMessage(page, firstMessage, { reason, loopId, jobId, createdAt }) {
	await page.route('**/api/v1/threads/*', async (route) => {
		if (route.request().method() !== 'GET') {
			await route.continue();
			return;
		}
		const response = await route.fetch();
		const detail = await response.json();
		if (detail.thread?.title !== firstMessage.slice(0, 80)) {
			await route.fulfill({ response });
			return;
		}
		const nextSequence = (detail.messages.at(-1)?.sequence ?? 0) + 1;
		await route.fulfill({
			response,
			json: {
				...detail,
				thread: { ...detail.thread, status: 'blocked' },
				messages: [
					...detail.messages,
					{
						id: 'message-error-grouping',
						threadId: detail.thread.id,
						projectId: detail.thread.projectId,
						sequence: nextSequence,
						kind: 'error',
						author: 'product_loop',
						content: reason,
						metadata: { jobId, loopId, status: 'blocked', reason, evidencePackageId: `evidence-${loopId}` },
						createdAt,
					},
				],
			},
		});
	});
}

test('Threads: console folds one blocked failure (state_changed + blocked x2 + error message) into a single row', async ({
	page,
}) => {
	const firstMessage = `Console grouping single failure ${Date.now()}`;
	const reason = 'ProductOwnerAgent runtime output is not valid JSON.';
	const loopId = 'loop-console-grouping-single';
	const startAtMs = Date.now();
	const events = blockedFailureEvents({ loopId, reason, stage: 'product_owner', startSequence: 1, startAtMs });

	await routeFixedEventStream(page, events);
	await routeDetailWithErrorMessage(page, firstMessage, {
		reason,
		loopId,
		jobId: 'job-console-grouping',
		createdAt: new Date(startAtMs + 200).toISOString(),
	});

	try {
		await page.goto('/#threads');
		await expectControlPlaneLoaded(page);
		await createLiveThread(page, firstMessage);

		const log = page.locator('.thread-execution-log');
		const failureRows = log.locator('.thread-console-row[data-type="failure-group"]');
		await expect(failureRows).toHaveCount(1);
		// The reason renders exactly once inside the grouped row, not once per underlying record.
		await expect(failureRows.getByText(reason, { exact: true })).toHaveCount(1);
		await expect(failureRows.getByText('×4')).toBeVisible();
		// Every original record stays reachable behind the existing technical-details disclosure.
		await failureRows.getByText('Technical details').click();
		const rawPayload = await failureRows.locator('pre').textContent();
		const parsed = JSON.parse(rawPayload ?? '[]');
		expect(parsed).toHaveLength(4);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Threads: console keeps two distinct blocked failures (different reason and loop) as two rows', async ({
	page,
}) => {
	const firstMessage = `Console grouping two failures ${Date.now()}`;
	const reasonA = 'ProductOwnerAgent runtime output is not valid JSON.';
	const reasonB = 'NVIDIA NIM execution failed: provider_request_failed:http_status=404';
	const startAtMs = Date.now();
	const eventsA = blockedFailureEvents({
		loopId: 'loop-console-grouping-a',
		reason: reasonA,
		stage: 'product_owner',
		startSequence: 1,
		startAtMs,
	});
	const eventsB = blockedFailureEvents({
		loopId: 'loop-console-grouping-b',
		reason: reasonB,
		stage: 'runtime',
		startSequence: eventsA.length + 1,
		// Well outside the 2s grouping window, and a genuinely different loop/reason regardless.
		startAtMs: startAtMs + 10_000,
	});

	await routeFixedEventStream(page, [...eventsA, ...eventsB]);

	try {
		await page.goto('/#threads');
		await expectControlPlaneLoaded(page);
		await createLiveThread(page, firstMessage);

		const log = page.locator('.thread-execution-log');
		const failureRows = log.locator('.thread-console-row[data-type="failure-group"]');
		await expect(failureRows).toHaveCount(2);
		await expect(log.getByText(reasonA, { exact: true })).toHaveCount(1);
		await expect(log.getByText(reasonB, { exact: true })).toHaveCount(1);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});
