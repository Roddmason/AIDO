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
