/**
 * Thread user-story kanban: once the loop passes "branch ready" and enters "executing" the story board
 * takes the main area and the chat moves to a foldable side column (unread count while folded); the
 * Chat | Board choice and the fold are remembered per thread across reloads; each card says who works
 * the story (loop role, runtime, model) and its progress, the header the backlog progress; a card
 * opens a drawer with criteria, activity and — only while the loop awaits review — a real
 * `request_changes` feedback form. Desktop and phone. Routed fixture thread; the backend contract is
 * covered by tests_py/test_thread_board_api.py and tests_py/test_backlog_board.py.
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

const THREAD_ID = 'thread-story-kanban-fixture';
const LOOP_ID = 'loop-kanban-fixture';

function buildBoard({ loopState = 'executing', stage = 'executing' } = {}) {
	const base = {
		synthetic: false,
		asA: 'operations lead',
		soThat: 'setup ends without support',
		priority: 'high',
		blocked: false,
		blockedReason: null,
		outcome: null,
		description: 'Day-one checklist for new operators.',
	};
	const checklist = {
		...base,
		storyId: 'story-1',
		index: 1,
		title: 'Readiness checklist',
		iWant: 'to see a readiness checklist',
		status: 'in_progress',
		column: 'in_progress',
		runtime: 'codex_cli',
		acceptanceCriteria: ['Lists every step.', 'Marks finished steps.'],
		criteria: [
			{ id: 'c-1', text: 'Lists every step.', status: 'pending', met: false },
			{ id: 'c-2', text: 'Marks finished steps.', status: 'pending', met: false },
		],
		tasks: [{ id: 'task-1', title: 'Build checklist UI', role: 'frontend_engineer', status: 'in_progress' }],
		progressPercent: 33,
		assignee: {
			role: 'developer',
			runtime: 'codex_cli',
			model: 'gpt-5.5',
			active: loopState === 'executing',
			plannedRoles: ['frontend_engineer'],
		},
		qaVerdict: null,
		commit: null,
		runs: 1,
	};
	const reminders = {
		...base,
		storyId: 'story-2',
		index: 2,
		title: 'Setup reminders',
		iWant: 'to get setup reminders',
		status: 'done',
		column: 'done',
		runtime: 'codex_cli',
		acceptanceCriteria: ['Sends a reminder.'],
		criteria: [{ id: 'c-3', text: 'Sends a reminder.', status: 'pending', met: true }],
		tasks: [{ id: 'task-2', title: 'Schedule reminders', role: 'backend_engineer', status: 'done' }],
		progressPercent: 100,
		assignee: { role: 'developer', runtime: 'codex_cli', model: null, active: false, plannedRoles: ['backend_engineer'] },
		qaVerdict: 'passed',
		commit: '4f2c9d1a7b3e5f60',
		runs: 2,
	};
	const export_ = {
		...base,
		storyId: 'story-3',
		index: 3,
		title: 'Export report',
		iWant: 'to export a report',
		status: 'todo',
		column: 'todo',
		runtime: null,
		acceptanceCriteria: ['Exports CSV.'],
		criteria: [{ id: 'c-4', text: 'Exports CSV.', status: 'pending', met: false }],
		tasks: [{ id: 'task-3', title: 'CSV export', role: 'backend_engineer', status: 'todo' }],
		progressPercent: 0,
		assignee: { role: null, runtime: null, model: null, active: false, plannedRoles: ['backend_engineer'] },
		qaVerdict: null,
		commit: null,
		runs: 0,
	};
	const byColumn = { todo: [export_], in_progress: [checklist], qa: [], done: [reminders] };
	return {
		loopId: LOOP_ID,
		loopState,
		stage,
		progress: { done: 1, total: 3, percent: 44 },
		columns: ['todo', 'in_progress', 'qa', 'done'].map((id) => ({ id, cards: byColumn[id] })),
	};
}

async function openKanbanThread(page, { executing = true, loopState = 'executing', stage = 'executing' } = {}) {
	const { projects } = await (await page.request.get('/api/v1/projects')).json();
	const project = projects.find((entry) => entry.status === 'active');
	expect(project).toBeTruthy();
	const state = { feedback: null, messages: [], extraEvents: [] };
	const thread = {
		id: THREAD_ID,
		projectId: project.id,
		ownerId: project.id,
		ownerType: 'workspace',
		title: 'Story kanban fixture',
		summary: '',
		status: 'running',
		metadata: {},
		createdAt: '2026-09-27T10:00:00Z',
		updatedAt: '2026-09-27T10:00:00Z',
	};
	const event = (sequence, type, payload = {}, agentRole = null) => ({
		id: `kanban-${sequence}`,
		threadId: thread.id,
		projectId: project.id,
		sequence,
		type,
		payload,
		agentRole,
		metadata: {},
		createdAt: `2026-09-27T10:0${Math.min(sequence, 9)}:00Z`,
	});
	const planning = [
		event(1, 'run_queued', { status: 'queued' }),
		event(2, 'worker_claimed', { workerId: 'worker-kanban' }),
		event(3, 'branch_ready', { loopId: LOOP_ID }),
	];
	const execution = [
		event(4, 'executing', { loopId: LOOP_ID, toState: 'executing' }),
		event(5, 'story_progress', { loopId: LOOP_ID, storyId: 'story-2', status: 'in_progress', index: 2, total: 3 }, 'developer'),
		event(6, 'story_progress', { loopId: LOOP_ID, storyId: 'story-2', status: 'done', index: 2, total: 3 }, 'developer'),
		event(7, 'story_progress', { loopId: LOOP_ID, storyId: 'story-1', status: 'in_progress', index: 1, total: 3 }, 'developer'),
	];
	const currentEvents = () => [...planning, ...(executing ? execution : []), ...state.extraEvents];
	state.addMessage = (content) => {
		const sequence = 20 + state.messages.length;
		state.messages.push({
			id: `kanban-message-${sequence}`,
			threadId: thread.id,
			projectId: project.id,
			sequence,
			author: 'AIDO Lead',
			kind: 'aido_lead',
			content,
			metadata: {},
			createdAt: thread.createdAt,
		});
		state.extraEvents.push(event(sequence, 'agent_message', { note: content }));
	};
	await page.route('**/api/v1/overview', async (route) => {
		const response = await route.fetch();
		const overview = await response.json();
		await route.fulfill({ response, json: { ...overview, threads: [...overview.threads, thread] } });
	});
	await page.route(`**/api/v1/threads/${thread.id}`, (route) =>
		route.fulfill({ json: { thread, messages: state.messages, decisions: [], artifacts: [], events: currentEvents() } }),
	);
	await page.route(`**/api/v1/threads/${thread.id}/remediations`, (route) => route.fulfill({ json: { remediations: [] } }));
	await page.route(`**/api/v1/threads/${thread.id}/board`, (route) =>
		route.fulfill({
			json: executing
				? buildBoard({ loopState, stage })
				: { ...buildBoard({ loopState: 'branch_ready', stage: 'planning' }) },
		}),
	);
	await page.route(`**/api/v1/projects/${project.id}/product-loop/${LOOP_ID}/feedback`, (route) => {
		state.feedback = route.request().postDataJSON();
		return route.fulfill({ status: 202, json: { operationId: 'op-kanban', status: 'queued' } });
	});
	await page.route(`**/api/v1/threads/${thread.id}/events?*`, (route) => {
		const afterSeq = Number(new URL(route.request().url()).searchParams.get('afterSeq'));
		const events = currentEvents();
		return route.fulfill({
			json: {
				events: events.filter((item) => item.sequence > afterSeq),
				lastSeq: events.at(-1).sequence,
				running: true,
				threadStatus: 'running',
			},
		});
	});
	await gotoThread(page, project, thread);
	return { state, project, thread };
}

async function gotoThread(page, project, thread) {
	await page.goto('/#threads');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const threadButton = page.getByRole('button', { name: thread.title, exact: true });
	if (!(await threadButton.isVisible())) {
		await page.locator('.thread-workspace-head').filter({ hasText: project.name }).click();
	}
	await threadButton.click();
}

test.beforeEach(async ({ page }) => {
	await page.addInitScript(() => {
		// Each test starts from the automatic layout; reloads inside a test keep what it stored.
		if (!sessionStorage.getItem('kanban-spec-started')) {
			sessionStorage.setItem('kanban-spec-started', '1');
			localStorage.removeItem('aido:threads:layout:v1');
		}
	});
});

test('Threads kanban: before executing the chat keeps the main area', async ({ page }) => {
	try {
		await openKanbanThread(page, { executing: false });
		const body = page.locator('.thread-live-body');
		await expect(page.locator('.thread-pipeline-step')).toHaveCount(7, { timeout: 20_000 });
		await expect(body).toHaveAttribute('data-mode', 'chat');
		await expect(page.locator('.thread-board')).toHaveCount(0);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Threads kanban: executing swaps the board in with assignee, progress and a remembered layout', async ({
	page,
	isMobile,
}) => {
	test.skip(isMobile, 'desktop layout assertions');
	await page.setViewportSize({ width: 1440, height: 900 });
	try {
		const { state, project, thread } = await openKanbanThread(page);
		const body = page.locator('.thread-live-body');
		await expect(body).toHaveAttribute('data-mode', 'board', { timeout: 20_000 });
		const board = page.getByRole('region', { name: 'Story board' });
		await expect(board.getByRole('heading', { name: 'User stories' })).toBeVisible();
		const overall = board.getByRole('progressbar', { name: 'Backlog progress' });
		await expect(overall).toHaveAttribute('aria-valuenow', '44');
		await expect(board.locator('.thread-board-overall .thread-progress-value')).toHaveText('44%');

		const working = board.locator('.thread-board-card[data-story-id="story-1"]');
		await expect(working).toHaveAttribute('data-active', 'true');
		await expect(working.locator('.thread-board-card-assignee')).toContainText('Working now');
		await expect(working.locator('.thread-board-card-assignee')).toContainText('DeveloperAgent · codex_cli · gpt-5.5');
		await expect(working.getByRole('progressbar', { name: 'Story progress' })).toHaveAttribute('aria-valuenow', '33');
		await expect(working).toContainText('0/2 criteria');
		const done = board.locator('.thread-board-card[data-story-id="story-2"]');
		await expect(done).toContainText('QA passed');
		await expect(done).toContainText('1/1 criteria');
		await expect(board.locator('.thread-board-card[data-story-id="story-3"]')).toContainText(
			'Unassigned · planned: backend_engineer',
		);

		// Chat stays usable beside the board: fold it, a new transcript message raises the unread count.
		const dock = page.locator('.thread-chat-dock-toggle');
		await expect(dock).toHaveAttribute('aria-expanded', 'true');
		await expect(page.locator('.thread-composer-dock')).toBeVisible();
		const sideBySideWidth = (await board.boundingBox()).width;
		await dock.click();
		await expect(dock).toHaveAttribute('aria-expanded', 'false');
		await expect(page.locator('.thread-composer-dock')).toBeHidden();
		// The folded column gives its width back to the board (after the grid transition settles).
		await expect
			.poll(async () => (await board.boundingBox()).width)
			.toBeGreaterThan(sideBySideWidth + 200);
		state.addMessage('Story 1 is halfway there.');
		await expect(page.locator('.thread-chat-unread')).toContainText('1', { timeout: 10_000 });
		await dock.click();
		await expect(page.locator('.thread-chat-unread')).toHaveCount(0);
		await expect(page.locator('.thread-live-scroll')).toContainText('Story 1 is halfway there.');

		// A manual swap back to chat is remembered for this thread across a reload.
		await page.getByRole('radio', { name: 'Chat' }).click();
		await expect(body).toHaveAttribute('data-mode', 'chat');
		const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('aido:threads:layout:v1')));
		expect(stored[thread.id].mode).toBe('chat');
		await page.reload();
		await gotoThread(page, project, thread);
		await expect(page.locator('.thread-pipeline-step')).toHaveCount(7, { timeout: 20_000 });
		await expect(page.locator('.thread-live-body')).toHaveAttribute('data-mode', 'chat');
		await page.getByRole('radio', { name: 'Board' }).click();
		await expect(page.locator('.thread-live-body')).toHaveAttribute('data-mode', 'board');
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Threads kanban: a card opens a read-only story drawer while the loop executes', async ({ page }) => {
	if (!test.info().project.use.isMobile) await page.setViewportSize({ width: 1440, height: 900 });
	try {
		await openKanbanThread(page);
		await expect(page.locator('.thread-live-body')).toHaveAttribute('data-mode', 'board', { timeout: 20_000 });
		const opener = page.locator('.thread-board-card[data-story-id="story-2"] .thread-board-card-open');
		await opener.focus();
		await page.keyboard.press('Enter');
		const drawer = page.getByRole('dialog', { name: 'User story #2' });
		await expect(drawer).toBeVisible();
		await expect(drawer).toContainText('Setup reminders');
		await expect(drawer).toContainText('Day-one checklist for new operators.');
		await expect(drawer.locator('.thread-story-criterion[data-met="true"]')).toHaveCount(1);
		await expect(drawer).toContainText('DeveloperAgent · codex_cli');
		await expect(drawer).toContainText('4f2c9d1a7b3e');
		const timeline = drawer.locator('.thread-story-event');
		await expect(timeline).toHaveCount(2);
		await expect(timeline.first()).toContainText('Marked done');
		await expect(drawer.getByRole('button', { name: 'Request changes' })).toHaveCount(0);
		await expect(drawer).toContainText('Read-only while the loop works.');
		await page.keyboard.press('Escape');
		await expect(drawer).toBeHidden();
		await expect(opener).toBeFocused();
		if (test.info().project.use.isMobile) {
			expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
		}
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Threads kanban: awaiting approval, the drawer requests changes on the story task', async ({ page, isMobile }) => {
	test.skip(isMobile, 'form flow covered once');
	await page.setViewportSize({ width: 1440, height: 900 });
	try {
		const { state } = await openKanbanThread(page, { loopState: 'awaiting_approval', stage: 'approval' });
		await expect(page.locator('.thread-live-body')).toHaveAttribute('data-mode', 'board', { timeout: 20_000 });
		const done = page.locator('.thread-board-card[data-story-id="story-2"]');
		await expect(done).toContainText('Awaiting your review');
		await done.click();
		const drawer = page.getByRole('dialog', { name: 'User story #2' });
		await drawer.getByLabel('What should change').fill('Send the reminder one day earlier.');
		await drawer.getByRole('button', { name: 'Request changes' }).click();
		await expect(drawer.getByRole('status')).toContainText('Changes requested');
		expect(state.feedback).toEqual({
			action: 'request_changes',
			feedback: 'Send the reminder one day earlier.',
			targetType: 'task',
			targetId: 'task-2',
		});
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Threads kanban: on a phone the board leads and the folded chat is a tab bar', async ({ page, isMobile }) => {
	test.skip(!isMobile, 'phone layout assertions');
	await page.setViewportSize({ width: 360, height: 780 });
	try {
		const { state } = await openKanbanThread(page);
		const body = page.locator('.thread-live-body');
		await expect(body).toHaveAttribute('data-mode', 'board', { timeout: 20_000 });
		const board = page.getByRole('region', { name: 'Story board' });
		await expect(board.getByRole('progressbar', { name: 'Backlog progress' })).toHaveAttribute('aria-valuenow', '44');
		await expect(board.locator('.thread-board-card[data-story-id="story-1"]')).toContainText('DeveloperAgent');
		const dock = page.locator('.thread-chat-dock-toggle');
		await dock.click();
		await expect(body).toHaveAttribute('data-chat', 'collapsed');
		const dockBox = await dock.boundingBox();
		const boardBox = await board.boundingBox();
		expect(dockBox.y).toBeGreaterThan(boardBox.y);
		expect(dockBox.width).toBeGreaterThan(300);
		state.addMessage('Checklist is in review.');
		await expect(page.locator('.thread-chat-unread')).toContainText('1', { timeout: 10_000 });
		await dock.click();
		await expect(page.locator('.thread-live-scroll')).toContainText('Checklist is in review.');
		expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});
