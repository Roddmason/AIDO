/**
 * Thread story board: once a thread reaches execution with a planned backlog the center switches to
 * the development layout (board in the main area, chat as a column), the operator can flip back to
 * chat, the layout tripwires (execution pane, pipeline steps, composer) hold on desktop and on a
 * phone, and retrying a blocked story moves its card within the 2 s refresh SLA even when the event
 * stream had gone idle. Fixture thread with routed board/events/remediations; the backend contract
 * is covered by tests_py.
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

async function openFixtureThread(page, { blocked = false } = {}) {
	const { projects } = await (await page.request.get('/api/v1/projects')).json();
	const project = projects.find((entry) => entry.status === 'active');
	expect(project).toBeTruthy();
	const state = { retried: false, eventRequests: 0 };
	const thread = {
		id: 'thread-story-board-fixture',
		projectId: project.id,
		ownerId: project.id,
		ownerType: 'workspace',
		title: 'Story board fixture',
		summary: '',
		status: blocked ? 'blocked' : 'running',
		metadata: {},
		createdAt: '2026-09-22T00:00:00Z',
		updatedAt: '2026-09-22T00:00:00Z',
	};
	const event = (sequence, type, payload = {}) => ({
		id: `story-board-${sequence}`,
		threadId: thread.id,
		projectId: project.id,
		sequence,
		type,
		payload,
		metadata: {},
		createdAt: thread.createdAt,
	});
	const progress = (sequence, status) =>
		event(sequence, 'story_progress', { loopId: 'loop-board-fixture', storyId: 'story-1', status, index: 1, total: 2 });
	const startEvents = [
		event(1, 'run_queued', { status: 'queued' }),
		event(2, 'worker_claimed', { workerId: 'worker-board' }),
		event(3, 'branch_ready', { loopId: 'loop-board-fixture' }),
		event(4, 'executing', { loopId: 'loop-board-fixture', toState: 'executing' }),
	];
	const runningEvents = [...startEvents, progress(5, 'qa')];
	const blockedEvents = [
		...startEvents,
		progress(5, 'blocked'),
		event(6, 'blocked', { stage: 'qa_rework', reason: 'QA failed after 2 rework rounds.' }),
	];
	const retriedEvents = [
		...blockedEvents,
		event(7, 'run_queued', { status: 'queued' }),
		event(8, 'executing', { loopId: 'loop-board-fixture', toState: 'executing' }),
		progress(9, 'done'),
	];
	const currentEvents = () => (!blocked ? runningEvents : state.retried ? retriedEvents : blockedEvents);
	const card = (storyId, index, title, status, column, task, extra = {}) => ({
		storyId,
		index,
		synthetic: false,
		title,
		asA: 'operations lead',
		iWant: `to see ${title.toLowerCase()}`,
		soThat: 'setup ends without support',
		priority: 'medium',
		status,
		column,
		blocked: false,
		blockedReason: null,
		outcome: null,
		runtime: 'codex_cli',
		acceptanceCriteria: [`${title} is verifiable.`],
		tasks: [task],
		...extra,
	});
	const todoCard = card('story-2', 2, 'Setup reminders', 'todo', 'todo', {
		id: 'task-2',
		title: 'Build reminders',
		role: 'backend_engineer',
		status: 'todo',
	});
	const checklistTask = { id: 'task-1', title: 'Build checklist', role: 'frontend_engineer' };
	const boardWith = (column, status, extra = {}) => ({
		loopId: 'loop-board-fixture',
		loopState: status === 'blocked' ? 'blocked' : 'qa_running',
		stage: status === 'blocked' ? 'blocked' : 'executing',
		progress: { done: column === 'done' ? 1 : 0, total: 2 },
		columns: ['todo', 'in_progress', 'qa', 'done'].map((id) => ({
			id,
			cards: [
				...(id === 'todo' ? [todoCard] : []),
				...(id === column
					? [card('story-1', 1, 'Readiness checklist', status, column, { ...checklistTask, status }, extra)]
					: []),
			],
		})),
	});
	const currentBoard = () =>
		!blocked
			? boardWith('qa', 'qa')
			: state.retried
				? boardWith('done', 'done')
				: boardWith('in_progress', 'blocked', { blocked: true, blockedReason: 'QA failed after 2 rework rounds.' });
	const retryAction = {
		id: 'remediation-board-retry',
		projectId: project.id,
		threadId: thread.id,
		loopId: 'loop-board-fixture',
		stage: 'qa_rework',
		blockerType: 'qa_failed',
		title: 'QA failed',
		description: 'QA failed after 2 rework rounds.',
		actionType: 'retry_loop',
		payload: { reason: 'QA failed after 2 rework rounds.' },
		status: 'pending',
		createdAt: thread.createdAt,
		resolvedAt: null,
	};
	await page.route('**/api/v1/overview', async (route) => {
		const response = await route.fetch();
		const overview = await response.json();
		await route.fulfill({ response, json: { ...overview, threads: [...overview.threads, thread] } });
	});
	await page.route(`**/api/v1/threads/${thread.id}`, (route) =>
		route.fulfill({ json: { thread, messages: [], decisions: [], artifacts: [], events: [] } }),
	);
	await page.route(`**/api/v1/threads/${thread.id}/remediations`, (route) =>
		route.fulfill({ json: { remediations: blocked && !state.retried ? [retryAction] : [] } }),
	);
	await page.route(`**/api/v1/remediations/${retryAction.id}/execute`, (route) => {
		state.retried = true;
		return route.fulfill({
			json: { remediation: { ...retryAction, status: 'executed' }, execution: { status: 'queued' } },
		});
	});
	await page.route(`**/api/v1/threads/${thread.id}/board`, (route) => route.fulfill({ json: currentBoard() }));
	await page.route(`**/api/v1/threads/${thread.id}/events?*`, (route) => {
		state.eventRequests += 1;
		const afterSeq = Number(new URL(route.request().url()).searchParams.get('afterSeq'));
		const events = currentEvents();
		const running = !blocked || state.retried;
		return route.fulfill({
			json: {
				events: events.filter((item) => item.sequence > afterSeq),
				lastSeq: events.at(-1).sequence,
				running,
				threadStatus: running ? 'running' : 'blocked',
			},
		});
	});
	await page.goto('/#threads');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const threadButton = page.getByRole('button', { name: thread.title, exact: true });
	if (!(await threadButton.isVisible())) {
		await page.locator('.thread-workspace-head').filter({ hasText: project.name }).click();
	}
	await threadButton.click();
	return state;
}

async function expectComposerReachable(page) {
	const composer = page.locator('.thread-composer-dock');
	// On a phone the stacked shell leaves the scrolling body shorter than the composer, so a centering
	// scroll clips its top edge: scroll the composer's top into view the way an operator would.
	await composer.evaluate((node) => node.scrollIntoView({ block: 'start', inline: 'nearest' }));
	await expect(composer).toBeInViewport();
	const covered = await composer.evaluate((node) => {
		const rect = node.getBoundingClientRect();
		const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + Math.min(rect.height / 2, 20));
		return !node.contains(hit);
	});
	expect(covered).toBe(false);
}

async function expectComposerFits(page) {
	const composer = page.locator('.thread-composer-dock');
	expect(await composer.evaluate((node) => node.scrollWidth - node.clientWidth)).toBe(0);
}

test('Threads: a thread in execution switches to the story board and back to chat', async ({ page, isMobile }) => {
	test.skip(isMobile, 'desktop layout assertions');
	await page.setViewportSize({ width: 1440, height: 900 });
	try {
		await openFixtureThread(page);
		const body = page.locator('.thread-live-body');
		await expect(body).toHaveAttribute('data-mode', 'board', { timeout: 20_000 });
		const board = page.getByRole('region', { name: 'Story board' });
		await expect(board).toBeVisible();
		await expect(board.locator('.thread-board-column')).toHaveCount(4);
		await expect(board.locator('.thread-board-column[data-column="qa"]')).toContainText('Readiness checklist');
		await expect(board.locator('.thread-board-column[data-column="todo"]')).toContainText('Setup reminders');
		await expect(page.getByRole('complementary', { name: 'Inspector', exact: true })).toBeHidden();
		// The board section animates its layout while the inspector folds away: poll the settled boxes.
		const chat = page.locator('.thread-transcript-pane');
		await expect
			.poll(async () => {
				const boardBox = await board.boundingBox();
				const chatBox = await chat.boundingBox();
				return boardBox.x + boardBox.width - chatBox.x;
			})
			.toBeLessThanOrEqual(1);
		await expect(page.locator('.thread-pipeline-step')).toHaveCount(7);
		const pane = page.locator('.thread-execution-pane');
		await expect(pane).toBeVisible();
		expect(await pane.evaluate((node) => node.scrollWidth - node.clientWidth)).toBe(0);
		await expectComposerReachable(page);
		await expectComposerFits(page);
		// At 1440 px the board runs two lanes wide enough to show each task title, and the composer
		// leaves the chat column a usable transcript instead of a sliver.
		const titleWidths = await board
			.locator('.thread-board-task-title')
			.evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().width));
		expect(titleWidths.length).toBeGreaterThan(0);
		for (const width of titleWidths) expect(width).toBeGreaterThanOrEqual(96);
		const transcriptHeight = await page
			.locator('.thread-live-scroll')
			.evaluate((node) => node.getBoundingClientRect().height);
		expect(transcriptHeight).toBeGreaterThanOrEqual(240);

		await page.getByRole('radio', { name: 'Chat' }).click();
		await expect(body).toHaveAttribute('data-mode', 'chat');
		await expect(page.locator('.thread-board')).toHaveCount(0);
		await expectComposerReachable(page);
		await page.getByRole('radio', { name: 'Board' }).click();
		await expect(body).toHaveAttribute('data-mode', 'board');
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Threads: retrying a blocked story moves its card within the refresh SLA', async ({ page, isMobile }) => {
	test.skip(isMobile, 'desktop layout assertions');
	await page.setViewportSize({ width: 1440, height: 900 });
	try {
		const state = await openFixtureThread(page, { blocked: true });
		const board = page.getByRole('region', { name: 'Story board' });
		await expect(board.locator('.thread-board-column[data-column="in_progress"]')).toContainText(
			'Readiness checklist',
			{ timeout: 20_000 },
		);
		await expect.poll(() => state.eventRequests, { timeout: 20_000 }).toBeGreaterThanOrEqual(6);
		const idleRequests = state.eventRequests;
		const retry = page
			.locator('.thread-execution-pane .thread-remediation-card')
			.getByRole('button', { name: /Retry loop|Reintentar loop/ });
		await retry.click();
		const clickedAt = Date.now();
		await expect(board.locator('.thread-board-column[data-column="done"]')).toContainText('Readiness checklist', {
			timeout: 2_000,
		});
		expect(Date.now() - clickedAt).toBeLessThanOrEqual(2_000);
		expect(state.eventRequests).toBeGreaterThan(idleRequests);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Threads: the story board stacks above the chat on a phone without covering the composer', async ({ page, isMobile }) => {
	test.skip(!isMobile, 'stacked layout assertions');
	try {
		await openFixtureThread(page);
		const body = page.locator('.thread-live-body');
		await expect(body).toHaveAttribute('data-mode', 'board', { timeout: 20_000 });
		const board = page.getByRole('region', { name: 'Story board' });
		await expect(board).toBeVisible();
		const boardBox = await board.boundingBox();
		const chatBox = await page.locator('.thread-transcript-pane').boundingBox();
		expect(boardBox.y).toBeLessThan(chatBox.y);
		await expectComposerReachable(page);
		expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
		const pane = page.locator('.thread-execution-pane');
		expect(await pane.evaluate((node) => node.scrollWidth - node.clientWidth)).toBe(0);
		await expectComposerFits(page);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});
