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
