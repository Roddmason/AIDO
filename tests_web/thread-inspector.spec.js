/**
 * Thread Inspector (threads area): the 8-tab "AI manager" console in the right pane.
 *
 * Verifies against the real control plane that the inspector opens with the live thread,
 * pins the loop vitals above the tabs, exposes the eight manager views as real ARIA tabs,
 * renders the Goal view from actual thread data, and never strands a view on its loading
 * skeleton after rapid tab switching (regression: the in-flight dedup marker must reset
 * synchronously when a tab unmounts mid-fetch).
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

// The AI-manager inspector auto-opens only in the desktop IDE layout (min-width 981px):
// on the narrow mobile project the shell keeps it collapsed so it never overlays the
// composer (AppShell: `if (!isDesktop) return`). Pin a desktop-width viewport so both
// Playwright projects exercise the inspector instead of the stacked mobile fallback.
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

test('Threads: the inspector opens as the AI-manager console with pinned loop vitals', async ({
	page,
}) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	const firstMessage = `Show the AI manager console over this goal ${Date.now()}`;
	await createLiveThread(page, firstMessage);

	// Selecting a live thread auto-expands the inspector with the manager console inside.
	const inspector = page.locator('.inspector-panel');
	await expect(inspector.locator('.thread-inspector')).toBeVisible({ timeout: 20_000 });

	// The eight manager views exist as real tabs in one tablist.
	const tablist = inspector.getByRole('tablist');
	const tabNames = [
		/Goal|Objetivo/,
		/Team|Equipo/,
		/Plan/,
		/Backlog/,
		/Memory|Memoria/,
		/Research|Investigaci/,
		/Artifacts|Artefactos/,
		/Settings|Configuraci/,
	];
	for (const name of tabNames) {
		await expect(tablist.getByRole('tab', { name })).toBeAttached();
	}

	// Loop vitals stay pinned above the tabs: the chip mirrors the thread's real status
	// (queued or waiting_decision depending on how the coordinator classified the goal).
	const vitals = inspector.locator('.thread-inspector-summary');
	await expect(vitals).toBeVisible({ timeout: 20_000 });
	const projectsResponse = await page.request.get('/api/v1/projects');
	const { projects } = await projectsResponse.json();
	const project = projects.find((item) => item.status === 'active');
	expect(project).toBeTruthy();
	const listed = await page.request.get(`/api/v1/threads?projectId=${project.id}`);
	const { threads } = await listed.json();
	const created = threads.find((thread) => thread.title === firstMessage.slice(0, 80));
	expect(created).toBeTruthy();
	const statusText = created.status.replace(/_/g, ' ');
	await expect(vitals.getByText(new RegExp(statusText, 'i')).first()).toBeVisible({
		timeout: 20_000,
	});

	// Goal (default view) renders the real objective plus its metadata, no placeholders.
	// `.first()`: the goal tab also renders a second `.thread-inspector-goal-text` inside the
	// Summary disclosure once the pipeline populates thread.summary; target the primary one.
	await expect(inspector.locator('.thread-inspector-goal-text').first()).toContainText(firstMessage, {
		timeout: 20_000,
	});
	await expect(inspector.getByText(/Owner|Responsable/).first()).toBeVisible();
});

test('Threads: inspector views survive rapid tab switching without a stuck skeleton', async ({
	page,
}) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	const firstMessage = `Exercise inspector tab switching ${Date.now()}`;
	await createLiveThread(page, firstMessage);

	const inspector = page.locator('.inspector-panel');
	await expect(inspector.locator('.thread-inspector')).toBeVisible({ timeout: 20_000 });
	const tablist = inspector.getByRole('tablist');

	// Rapid away-and-back while fetches are in flight: each view must settle on real
	// content or its honest empty state, never a permanent loading skeleton.
	await tablist.getByRole('tab', { name: /Team|Equipo/ }).click();
	await tablist.getByRole('tab', { name: /Plan/ }).click();
	await tablist.getByRole('tab', { name: /Team|Equipo/ }).click();
	await tablist.getByRole('tab', { name: /Plan/ }).click();
	await expect(inspector.locator('.thread-inspector-loading')).toBeHidden({ timeout: 20_000 });
	await expect(
		inspector.locator('.thread-inspector-stack, .thread-inspector-list, .empty-state').first(),
	).toBeVisible({ timeout: 20_000 });

	// Team view: agent roster rows or the honest "no agents" empty state.
	await tablist.getByRole('tab', { name: /Team|Equipo/ }).click();
	await expect(inspector.locator('.thread-inspector-loading')).toBeHidden({ timeout: 20_000 });
	await expect(
		inspector.locator('.thread-inspector-row, .empty-state').first(),
	).toBeVisible({ timeout: 20_000 });

	// Settings view: resolved read-only configuration or its empty state.
	await tablist.getByRole('tab', { name: /Settings|Configuraci/ }).click();
	await expect(
		inspector.locator('.thread-inspector-row, .empty-state').first(),
	).toBeVisible({ timeout: 20_000 });
});

test('Threads: Research tab shows blocked recovery instead of an empty artifact', async ({
	page,
}) => {
	const firstMessage = `Blocked source recovery card ${Date.now()}`;
	await page.route('**/api/v1/threads/*', async (route) => {
		const url = new URL(route.request().url());
		const pathPrefix = '/api/v1/threads/';
		const threadPath = url.pathname.slice(url.pathname.indexOf(pathPrefix) + pathPrefix.length);
		if (route.request().method() !== 'GET' || threadPath.includes('/')) {
			await route.continue();
			return;
		}
		const response = await route.fetch();
		const detail = await response.json();
		const matchesFixture =
			detail.thread?.title === firstMessage.slice(0, 80) ||
			detail.messages?.some((message) => message.content === firstMessage);
		if (!matchesFixture) {
			await route.fulfill({ response });
			return;
		}
		const blockedReport = {
			id: 'thread-artifact-research-blocked',
			artifactId: 'artifact-research-blocked',
			threadId: detail.thread.id,
			projectId: detail.thread.projectId,
			messageId: null,
			kind: 'research_report',
			title: 'Research',
			createdAt: new Date().toISOString(),
			metadata: {
				status: 'research_blocked',
				reason: 'ResearchAgent web search failed: urlopen timed out.',
				recommendation: {
					title: 'Research blocked',
					decision: 'Check network access before retrying ResearchAgent.',
					sourceCitations: [],
				},
				remediation: {
					action: 'check_network_access',
					summary: 'Check network access and retry ResearchAgent after official sources are reachable.',
				},
				sources: [],
				discrepancies: [],
			},
		};
		await route.fulfill({
			response,
			json: { ...detail, artifacts: [...detail.artifacts, blockedReport] },
		});
	});
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, firstMessage);

	const inspector = page.locator('.inspector-panel');
	await expect(inspector.locator('.thread-inspector')).toBeVisible({ timeout: 20_000 });
	await inspector.getByRole('tablist').getByRole('tab', { name: /Research|Investigaci/ }).click();
	const researchPanel = inspector.locator('.thread-inspector-research');

	await expect(researchPanel.getByText('Research blocked', { exact: true })).toBeVisible({
		timeout: 20_000,
	});
	await expect(researchPanel.getByText(/Check network access before retrying ResearchAgent/)).toBeVisible();
	await expect(researchPanel.getByText(/ResearchAgent web search failed/)).toBeVisible();
	await expect(researchPanel.getByText('No sources persisted yet.')).toBeHidden();
	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

test('Threads: all eight tabs stay reachable, un-cut and keyboard-navigable in a 320px pane', async ({
	page,
}) => {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	const firstMessage = `Narrow inspector tab reachability ${Date.now()}`;
	await createLiveThread(page, firstMessage);

	const inspector = page.locator('.inspector-panel');
	const threadInspector = inspector.locator('.thread-inspector');
	await expect(threadInspector).toBeVisible({ timeout: 20_000 });

	// Container queries key off the inspector's own width, not the viewport. Constrain it to
	// what it would be inside a 320px pane (minus the panel's 16px inline padding per side) to
	// faithfully exercise the narrow, icon-only regime.
	await threadInspector.evaluate((el) => el.style.setProperty('width', '288px', 'important'));

	const tablist = inspector.getByRole('tablist');
	const tabNames = [
		/Goal|Objetivo/,
		/Team|Equipo/,
		/Plan/,
		/Backlog/,
		/Memory|Memoria/,
		/Research|Investigaci/,
		/Artifacts|Artefactos/,
		/Settings|Configuraci/,
	];

	// Every view keeps its accessible name in icon-only mode and stays visible — none is hidden
	// behind a mask or clipped past the fold. This is the "Equipo is not cut" guarantee too.
	for (const name of tabNames) {
		await expect(tablist.getByRole('tab', { name })).toBeVisible();
	}
	await expect(tablist.getByRole('tab')).toHaveCount(8);

	// Icon-only compaction is genuinely engaged: the label collapses to a visually-hidden node
	// (position:absolute sr-only) while the tab keeps its accessible name (asserted above).
	const teamText = inspector.locator('#thread-inspector-tab-team .thread-inspector-tab-text');
	await expect(teamText).toHaveCount(1);
	expect(await teamText.evaluate((el) => getComputedStyle(el).position)).toBe('absolute');

	// No horizontal cut/scroll at 320px: the tablist wraps instead of overflowing its width.
	const overflow = await tablist.evaluate((el) => el.scrollWidth - el.clientWidth);
	expect(overflow).toBeLessThanOrEqual(1);

	// Keyboard reachability: the roving tabindex moves selection with Arrow/End/Home.
	const goal = tablist.getByRole('tab', { name: /Goal|Objetivo/ });
	await goal.focus();
	await page.keyboard.press('ArrowRight');
	await expect(tablist.getByRole('tab', { name: /Team|Equipo/ })).toHaveAttribute(
		'aria-selected',
		'true',
	);

	// Keyboard-focus discoverability: reached by keyboard (so :focus-visible engages), the icon-only
	// tab surfaces its label via a CSS tooltip — the native `title` never shows on keyboard focus.
	// The ::after content mirrors the tab's data-label.
	const teamTip = await page.evaluate(
		() =>
			getComputedStyle(
				document.querySelector('#thread-inspector-tab-team .thread-inspector-tab-label'),
				'::after',
			).content,
	);
	expect(teamTip).toContain('Team');

	await page.keyboard.press('End');
	await expect(tablist.getByRole('tab', { name: /Settings|Configuraci/ })).toHaveAttribute(
		'aria-selected',
		'true',
	);
	await page.keyboard.press('Home');
	await expect(goal).toHaveAttribute('aria-selected', 'true');

	// Pointer reachability: the last view is clickable at this width and renders its panel.
	await tablist.getByRole('tab', { name: /Settings|Configuraci/ }).click();
	await expect(
		inspector.locator('.thread-inspector-row, .empty-state').first(),
	).toBeVisible({ timeout: 20_000 });

	// Full labels return once the pane is genuinely wide: the label node leaves sr-only.
	await threadInspector.evaluate((el) => el.style.setProperty('width', '900px', 'important'));
	expect(await teamText.evaluate((el) => getComputedStyle(el).position)).toBe('static');
});

/** A roster of `count` idle agents — long enough to overflow the inspector's tab panel. */
function longRoster(count) {
	return Array.from({ length: count }, (_, index) => ({
		id: `agent-bench-${index}`,
		name: `Bench agent ${index}`,
		role: 'developer',
		runtimeType: 'api',
		runtimeMode: 'api',
		runtimeAvailability: {
			status: 'available',
			available: true,
			selectedProviderId: 'anthropic_api',
			requiredCapabilities: ['chat'],
		},
		reviewerPolicy: {},
		allowedProviders: ['anthropic_api'],
		maxCostPerRun: 1,
		maxTokensPerRun: 80000,
		status: 'active',
	}));
}

/** Serves one pending remediation so the tall repair card above the tabs renders deterministically. */
async function mockPendingRemediation(page) {
	await page.route('**/api/v1/threads/*/remediations', (route) =>
		route.fulfill({
			json: {
				remediations: [
					{
						id: 'remediation-inspector-fixture',
						projectId: 'project-fixture',
						threadId: 'thread-fixture',
						loopId: 'loop-plan-fixture',
						stage: 'runtime',
						blockerType: 'runtime_not_executable',
						title: 'Validate runtime',
						description: 'Re-check local runtime availability.',
						actionType: 'validate_runtime',
						payload: { reason: 'No executable runtime is currently available.' },
						status: 'pending',
						createdAt: '2026-07-08T11:30:00.000Z',
						resolvedAt: null,
					},
				],
			},
		}),
	);
}

test('Threads: a long Team roster scrolls inside its view and never pushes the tabs out of reach', async ({
	page,
}) => {
	// Regression: the repair card pinned above the tabs is tall, and the tablist was the only flexible
	// sibling — so a blocked thread squeezed `.tabs-root` down to a single pixel, cutting the eight tabs
	// off and spilling the pane into its own scroll. The card must scroll inside its own region instead.
	const overview = await (await page.request.get('/api/v1/overview')).json();
	await page.route('**/api/v1/overview', (route) =>
		route.fulfill({ json: { ...overview, agentProfiles: longRoster(24) } }),
	);
	await mockPendingRemediation(page);

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Long roster must not bury the tabs ${Date.now()}`);

	const inspectorPanel = page.locator('.inspector-panel');
	await expect(inspectorPanel.locator('.thread-inspector')).toBeVisible({ timeout: 20_000 });
	await expect(inspectorPanel.locator('.thread-remediation-card')).toBeVisible({ timeout: 20_000 });

	const tablist = inspectorPanel.getByRole('tablist');
	await tablist.getByRole('tab', { name: /Team|Equipo/ }).click();
	// An all-idle bench keeps its group open, so the full-height roster renders without a click.
	await expect(inspectorPanel.locator('.thread-inspector-row').first()).toBeVisible({
		timeout: 20_000,
	});

	// The repair card keeps its pinned spot but is capped, and scrolls inside its own region.
	const repair = inspectorPanel.locator('.thread-inspector-repair');
	const repairBox = await repair.evaluate((el) => ({
		client: el.clientHeight,
		scroll: el.scrollHeight,
		console: el.parentElement.clientHeight,
	}));
	expect(repairBox.scroll).toBeGreaterThan(repairBox.client);
	expect(repairBox.client).toBeLessThanOrEqual(repairBox.console / 2 + 1);

	// The tabs keep their real height — never the 1px sliver the old flex layout left them.
	const tabsRootHeight = await inspectorPanel
		.locator('.thread-inspector .tabs-root')
		.evaluate((el) => el.clientHeight);
	expect(tabsRootHeight).toBeGreaterThan(repairBox.console / 3);

	// The active view — not the pane — is the scroll container: it overflows, the pane does not.
	const tabPanel = inspectorPanel.locator('.thread-inspector .tabs-root > [role="tabpanel"]');
	const view = await tabPanel.evaluate((el) => ({
		client: el.clientHeight,
		scroll: el.scrollHeight,
	}));
	expect(view.scroll).toBeGreaterThan(view.client);
	expect(view.client).toBeGreaterThan(100);
	const paneOverflow = await inspectorPanel.evaluate((el) => el.scrollHeight - el.clientHeight);
	expect(paneOverflow).toBeLessThanOrEqual(1);

	// Scrolling the roster to its end leaves every tab where it was, fully inside the pane.
	const before = await tablist.boundingBox();
	await tabPanel.evaluate((el) => el.scrollTo(0, el.scrollHeight));
	await expect.poll(() => tabPanel.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);

	const after = await tablist.boundingBox();
	expect(Math.abs(after.y - before.y)).toBeLessThanOrEqual(1);
	const pane = await inspectorPanel.boundingBox();
	expect(after.y).toBeGreaterThanOrEqual(pane.y - 1);
	expect(after.y + after.height).toBeLessThanOrEqual(pane.y + pane.height + 1);
	for (const name of [/Goal|Objetivo/, /Team|Equipo/, /Settings|Configuraci/]) {
		await expect(tablist.getByRole('tab', { name })).toBeVisible();
	}

	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

/** A product-loop payload whose single active loop sits in `state`, with the given transitions. */
async function mockProductLoop(page, state, transitions) {
	const overview = await (await page.request.get('/api/v1/overview')).json();
	const project = overview.projects.find((item) => item.status === 'active') ?? overview.projects[0];
	expect(project).toBeTruthy();
	const base = await (await page.request.get(`/api/v1/projects/${project.id}/product-loop`)).json();
	const fixture = {
		...base,
		loops: [
			{
				id: 'loop-plan-fixture',
				projectId: project.id,
				title: 'Ship the checkout flow',
				state,
				status: 'active',
				createdAt: '2026-07-08T10:00:00.000Z',
				updatedAt: '2026-07-08T12:00:00.000Z',
			},
		],
		transitions,
	};
	await page.route('**/api/v1/projects/*/product-loop', (route) => route.fulfill({ json: fixture }));
}

/** One FSM transition row shaped like the backend contract. */
function transition(overrides) {
	return {
		id: 'transition-fixture',
		loopId: 'loop-plan-fixture',
		fromState: 'executing',
		toState: 'blocked',
		trigger: 'runtime_unavailable',
		reason: '',
		actor: 'system',
		createdAt: '2026-07-08T11:00:00.000Z',
		...overrides,
	};
}

test('Threads: a blocked Plan tab leads with the blocker and hands the operator the repair', async ({
	page,
}) => {
	await mockProductLoop(page, 'blocked', [
		transition({ id: 'transition-old', reason: 'An earlier block, already history.' }),
		transition({
			id: 'transition-current',
			reason: 'Ollama runtime offline: connection refused on 127.0.0.1:11434.',
			createdAt: '2026-07-08T11:30:00.000Z',
		}),
	]);
	await mockPendingRemediation(page);

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Plan must lead with the blocker ${Date.now()}`);

	const inspector = page.locator('.inspector-panel');
	await expect(inspector.locator('.thread-inspector')).toBeVisible({ timeout: 20_000 });
	await inspector.getByRole('tablist').getByRole('tab', { name: /Plan|Planificaci/ }).click();

	// The current block leads the view: it renders above the phase timeline, states the machine reason,
	// and older blocks stay folded away instead of burying it.
	const blocker = inspector.locator('.thread-plan-blocker');
	await expect(blocker).toBeVisible({ timeout: 20_000 });
	await expect(blocker).toContainText(/Ollama runtime offline/);
	await expect(blocker).not.toContainText(/already history/);
	const blockerBox = await blocker.boundingBox();
	const timelineBox = await inspector.locator('.thread-plan-timeline').boundingBox();
	expect(blockerBox.y).toBeLessThan(timelineBox.y);

	// Its CTA does not re-implement the repair: it hands focus to the card that carries the backend's
	// recommended action, which is pinned above the tabs.
	await blocker.getByRole('button', { name: /Go to the repair|Ir a la reparación/ }).click();
	const repairCard = inspector.locator('.thread-remediation-card');
	await expect(repairCard).toBeVisible();
	const focusInsideRepair = await page.evaluate(() => {
		const card = document.querySelector('.inspector-panel .thread-remediation-card');
		return Boolean(
			document.activeElement?.contains(card) || card?.contains(document.activeElement),
		);
	});
	expect(focusInsideRepair).toBe(true);

	// A halted loop marks no phase as reached — the timeline never fabricates progress.
	await expect(inspector.locator('.thread-plan-timeline li[data-state="current"]')).toHaveCount(0);
	await expect(inspector.locator('.thread-plan-timeline li[data-state="done"]')).toHaveCount(0);

	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

test('Threads: the Plan phases render as a compact vertical timeline that marks the live phase', async ({
	page,
}) => {
	await mockProductLoop(page, 'executing', [
		transition({ id: 'transition-run', fromState: 'iteration_planning', toState: 'executing' }),
	]);

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Plan phases as a vertical timeline ${Date.now()}`);

	const inspector = page.locator('.inspector-panel');
	await expect(inspector.locator('.thread-inspector')).toBeVisible({ timeout: 20_000 });
	await inspector.getByRole('tablist').getByRole('tab', { name: /Plan|Planificaci/ }).click();

	// A healthy loop shows no blocker banner at all — no fabricated "no blockers recorded" filler.
	await expect(inspector.locator('.thread-plan-timeline')).toBeVisible({ timeout: 20_000 });
	await expect(inspector.locator('.thread-plan-blocker')).toHaveCount(0);

	// The live phase is marked, and everything before it reads as done.
	const current = inspector.locator('.thread-plan-timeline li[data-state="current"]');
	await expect(current).toHaveCount(1);
	await expect(current).toContainText('executing');
	await expect(current).toHaveAttribute('aria-current', 'step');
	await expect(inspector.locator('.thread-plan-timeline li[data-state="done"]')).toHaveCount(6);

	// Vertical, not the old wrapping pill row: every node shares one x centre and descends in y.
	const nodes = await inspector.locator('.thread-plan-timeline li').evaluateAll((items) =>
		items.map((item) => {
			const node = item.querySelector('.thread-plan-timeline-node').getBoundingClientRect();
			return { x: Math.round(node.x + node.width / 2), y: Math.round(node.y) };
		}),
	);
	expect(nodes).toHaveLength(10);
	expect(new Set(nodes.map((node) => node.x)).size).toBe(1);
	for (let index = 1; index < nodes.length; index += 1) {
		expect(nodes[index].y).toBeGreaterThan(nodes[index - 1].y);
	}

	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

test('Threads: the Team tab reads as a manager console — grouped by state, active on top, blocked shows a fix, idle collapsed', async ({
	page,
}) => {
	// A deterministic roster injected over the live control plane: one agent per state so the grouping,
	// ordering and collapse behaviour are asserted regardless of what the seeded backend detects.
	const ROSTER = [
		{
			id: 'agent-active-demo',
			name: 'Ada · Backend',
			role: 'developer',
			runtimeType: 'api',
			runtimeMode: 'api',
			runtimeAvailability: {
				status: 'available',
				available: true,
				selectedProviderId: 'anthropic_api',
				requiredCapabilities: ['chat'],
			},
			routingProfileId: 'balanced_best_value',
			roleModelPolicyId: 'developer',
			reviewerPolicy: { reviewerRole: 'technical_lead', mode: 'peer' },
			allowedProviders: ['anthropic_api'],
			maxCostPerRun: 2,
			maxTokensPerRun: 200000,
			status: 'active',
		},
		{
			id: 'agent-waiting-demo',
			name: 'Boro · QA',
			role: 'qa',
			runtimeType: 'api',
			runtimeMode: 'api',
			runtimeAvailability: {
				status: 'available',
				available: true,
				selectedProviderId: 'anthropic_api',
				requiredCapabilities: ['chat'],
			},
			reviewerPolicy: {},
			allowedProviders: ['anthropic_api'],
			maxCostPerRun: 1,
			maxTokensPerRun: 120000,
			status: 'active',
		},
		{
			id: 'agent-blocked-demo',
			name: 'Cid · Security',
			role: 'security_engineer',
			runtimeType: 'ollama',
			runtimeMode: 'ollama',
			runtimeAvailability: {
				status: 'blocked',
				available: false,
				blockedReason: 'Ollama runtime offline',
				requiredCapabilities: ['chat'],
				candidateProviderIds: ['ollama'],
			},
			reviewerPolicy: {},
			allowedProviders: ['ollama'],
			maxCostPerRun: 1,
			maxTokensPerRun: 90000,
			status: 'active',
		},
		{
			id: 'agent-available-a',
			name: 'Dot · Docs',
			role: 'researcher',
			runtimeType: 'api',
			runtimeMode: 'api',
			runtimeAvailability: {
				status: 'available',
				available: true,
				selectedProviderId: 'anthropic_api',
				requiredCapabilities: ['chat'],
			},
			reviewerPolicy: {},
			allowedProviders: ['anthropic_api'],
			maxCostPerRun: 1,
			maxTokensPerRun: 80000,
			status: 'active',
		},
		{
			id: 'agent-available-b',
			name: 'Eli · Frontend',
			role: 'frontend_engineer',
			runtimeType: 'api',
			runtimeMode: 'api',
			runtimeAvailability: {
				status: 'available',
				available: true,
				selectedProviderId: 'anthropic_api',
				requiredCapabilities: ['chat'],
			},
			reviewerPolicy: {},
			allowedProviders: ['anthropic_api'],
			maxCostPerRun: 1,
			maxTokensPerRun: 80000,
			status: 'active',
		},
		{
			id: 'agent-unconfigured-demo',
			name: 'Fen · DevOps',
			role: 'devops_engineer',
			runtimeType: 'cli',
			runtimeMode: 'cli',
			runtimeAvailability: {
				status: 'configuration_required',
				available: false,
				requiredCapabilities: ['shell'],
				candidateProviderIds: ['codex_cli'],
			},
			reviewerPolicy: {},
			allowedProviders: ['codex_cli'],
			maxCostPerRun: 1,
			maxTokensPerRun: 80000,
			status: 'active',
		},
	];

	// Snapshot the live plane once, then serve static snapshots so background polls stay fast and stable
	// (the flat-list roster is what we are replacing). Routes are set BEFORE navigation so the inspector's
	// project-keyed product-loop resource is intercepted on its first (and only) fetch — otherwise it
	// caches the real loop and never sees the injected assignments. Overview keeps its real
	// threads/projects so the created thread stays selected; only `agentProfiles` is swapped.
	const overview = await (await page.request.get('/api/v1/overview')).json();
	const project = overview.projects.find((item) => item.status === 'active') ?? overview.projects[0];
	expect(project).toBeTruthy();
	const baseLoop = await (
		await page.request.get(`/api/v1/projects/${project.id}/product-loop`)
	).json();
	const loopFixture = {
		...baseLoop,
		tasks: [
			...baseLoop.tasks,
			{ id: 'task-active-demo', title: 'Implement the login form', role: 'developer', status: 'in_progress' },
			{ id: 'task-waiting-demo', title: 'Draft the QA plan', role: 'qa', status: 'queued' },
		],
		assignments: [
			...baseLoop.assignments,
			{
				id: 'asg-active-demo',
				agentId: 'agent-active-demo',
				taskId: 'task-active-demo',
				status: 'in_progress',
				releasedAt: null,
				canonicalArtifactId: 'artifact-login-0001',
			},
			{
				id: 'asg-waiting-demo',
				agentId: 'agent-waiting-demo',
				taskId: 'task-waiting-demo',
				status: 'queued',
				releasedAt: null,
				canonicalArtifactId: '',
			},
		],
	};

	await page.route('**/api/v1/overview', (route) =>
		route.fulfill({ json: { ...overview, agentProfiles: ROSTER } }),
	);
	await page.route('**/api/v1/projects/*/product-loop', (route) =>
		route.fulfill({ json: loopFixture }),
	);

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);

	const firstMessage = `Show the AI team grouped by state ${Date.now()}`;
	await createLiveThread(page, firstMessage);

	const inspector = page.locator('.inspector-panel');
	await expect(inspector.locator('.thread-inspector')).toBeVisible({ timeout: 20_000 });

	await inspector.getByRole('tablist').getByRole('tab', { name: /Team|Equipo/ }).click();
	await expect(inspector.locator('.thread-inspector-loading')).toBeHidden({ timeout: 20_000 });

	// The roster is grouped, not a flat wall: the state-group sections exist and the summary counts the
	// whole bench (6) even though only the working rows are shown.
	await expect(inspector.locator('.thread-team-group').first()).toHaveAttribute(
		'data-group',
		'active',
		{ timeout: 20_000 },
	);
	await expect(inspector.locator('.thread-team .thread-inspector-summary')).toContainText('6');

	// Requirement 1 — the active agent is at the top of the roster.
	await expect(inspector.locator('[data-group="active"] [data-state="active"]')).toContainText('Ada');

	// Requirement 2 — a blocked agent shows its reason and an inline remediation action.
	const blockedCard = inspector.locator('.thread-inspector-row[data-state="blocked"]').first();
	await expect(blockedCard).toContainText(/Ollama runtime offline/);
	await expect(blockedCard.getByRole('button', { name: /Resolve|Resolver/ })).toBeVisible();

	// Requirement 3 — "unknown" never dominates: only the attention rows (active+waiting+blocked = 3)
	// render, while the idle available/unconfigured groups collapse behind their Disclosure.
	await expect(
		inspector.locator(
			'[data-group="active"] .thread-inspector-row, [data-group="waiting"] .thread-inspector-row, [data-group="blocked"] .thread-inspector-row',
		),
	).toHaveCount(3);
	await expect(
		inspector.locator('[data-group="available"] .disclosure-trigger').first(),
	).toHaveAttribute('aria-expanded', 'false');
	await expect(inspector.getByText('Dot · Docs')).toBeHidden();

	await page.unrouteAll({ behavior: 'ignoreErrors' });
});
