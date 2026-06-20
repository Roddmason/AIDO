import { expect, test } from '@playwright/test';

// Absolute output dir so screenshots land in the repo regardless of the runner's
// per-chunk working directory; Playwright creates parent dirs for screenshot paths.
const SHOT_DIR = 'H:/Proyectos/Personales/AIDO/.tmp/ide-shell';

async function expectShellLoaded(page) {
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
}

function isDesktopViewport(page) {
	const viewport = page.viewportSize();
	return (viewport?.width ?? 1280) >= 981;
}

test('desktop shell exposes resizable panes with accessible separators', async ({ page }, info) => {
	test.skip(!isDesktopViewport(page), 'resizable panes are a desktop-only layout');
	await page.goto('/');
	await expectShellLoaded(page);

	// One accessible (role=separator) resize handle per pane edge: explorer | center
	// | inspector in the row, plus workbench | bottom in the center column.
	const separators = page.locator('[data-separator][role="separator"]');
	await expect(separators.first()).toBeVisible();
	expect(await separators.count()).toBeGreaterThanOrEqual(3);

	await expect(page.locator('.explorer-panel')).toBeVisible();
	await expect(page.locator('.main-area')).toBeVisible();

	await page.screenshot({ path: `${SHOT_DIR}/desktop-default-${info.project.name}.png` });
});

test('shortcuts toggle the explorer (Ctrl+B), inspector (Ctrl+Shift+B) and bottom dock (Ctrl+J)', async ({
	page,
}, info) => {
	test.skip(!isDesktopViewport(page), 'desktop-only shortcuts for the resizable layout');
	await page.goto('/');
	await expectShellLoaded(page);

	const explorer = page.locator('.explorer-panel');
	await expect(explorer).toBeVisible();

	await page.keyboard.press('Control+b');
	await expect(explorer).toBeHidden();
	await page.keyboard.press('Control+b');
	await expect(explorer).toBeVisible();

	const inspector = page.getByRole('complementary', { name: 'Inspector' });
	await expect(inspector).toBeHidden();
	await page.keyboard.press('Control+Shift+B');
	await expect(inspector).toBeVisible();

	const bottomDock = page.getByRole('region', { name: 'Bottom panel' });
	await expect(bottomDock).toBeHidden();
	await page.keyboard.press('Control+j');
	await expect(bottomDock).toBeVisible();
	await page.screenshot({ path: `${SHOT_DIR}/desktop-bottom-${info.project.name}.png` });
});

test('pane collapse persists in versioned localStorage across reloads', async ({ page }) => {
	test.skip(!isDesktopViewport(page), 'persistence applies to the resizable layout');
	await page.goto('/');
	await expectShellLoaded(page);

	const explorer = page.locator('.explorer-panel');
	await expect(explorer).toBeVisible();
	await page.keyboard.press('Control+b');
	await expect(explorer).toBeHidden();

	const storedKeys = await page.evaluate(() =>
		Object.keys(window.localStorage).filter((key) => key.includes('aido:ide-shell:v1')),
	);
	expect(storedKeys.length).toBeGreaterThan(0);

	await page.reload();
	await expectShellLoaded(page);
	await expect(page.locator('.explorer-panel')).toBeHidden();
});

test('mobile shell stacks the explorer and keeps it reachable', async ({ page }, info) => {
	test.skip(isDesktopViewport(page), 'stacked layout is the narrow-viewport fallback');
	await page.goto('/');
	await expectShellLoaded(page);

	await expect(page.locator('.explorer-panel')).toBeVisible();
	await page.screenshot({ path: `${SHOT_DIR}/mobile-${info.project.name}.png`, fullPage: true });
});
