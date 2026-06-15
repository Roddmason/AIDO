import { expect, test } from '@playwright/test';

async function expectControlPlaneLoaded(page) {
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
}

/** Force a deterministic state: no evidence packages and no pending approvals,
 *  so "Open evidence" and "Review pending approvals" are reliably disabled. */
async function routeEmptyEvidenceAndApprovals(page) {
	const overviewResponse = await page.request.get('/api/v1/overview');
	const overview = await overviewResponse.json();
	await page.route('/api/v1/overview', async (route) => {
		await route.fulfill({ json: { ...overview, evidencePackages: [], actionRequests: [] } });
	});
}

test('command palette opens as a top-centered modal with grouped actions and shortcut chips', async ({ page }) => {
	await page.goto('/');
	await expectControlPlaneLoaded(page);

	await page.keyboard.press('Control+k');

	const dialog = page.getByRole('dialog', { name: 'Command palette' });
	await expect(dialog).toBeVisible();

	// IDE quick-open: rendered in the dedicated top-anchored, horizontally centered layer (not a side drawer).
	await expect(page.locator('.command-palette-layer')).toBeVisible();
	await expect(page.locator('.drawer-panel')).toHaveCount(0);

	// Search input is the combobox and receives focus on open.
	const combobox = dialog.getByRole('combobox', { name: 'Filter commands' });
	await expect(combobox).toBeFocused();

	// Grouped, IDE-style sections.
	for (const group of ['Navigate', 'Actions', 'Runtime']) {
		await expect(dialog.getByText(group, { exact: true })).toBeVisible();
	}

	// The eight requested actions are present.
	for (const label of [
		'Open folder',
		'New task',
		'Go to Workbench',
		'Review pending approvals',
		'Configure runtime',
		'Refresh runtime health',
		'Open evidence',
		'Open settings group',
	]) {
		await expect(dialog.getByRole('option', { name: new RegExp(label) })).toBeVisible();
	}

	// Keyboard shortcut chips are real (wired) — the approvals action advertises Ctrl+Alt+A.
	await expect(dialog.getByRole('option', { name: /Review pending approvals/ })).toHaveAttribute('aria-keyshortcuts', /A/);
	await expect(dialog.locator('kbd.command-kbd').first()).toBeVisible();

	// Top-centered geometry sanity check.
	const box = await dialog.boundingBox();
	const viewport = page.viewportSize();
	const horizontalCenter = box.x + box.width / 2;
	expect(Math.abs(horizontalCenter - viewport.width / 2)).toBeLessThan(40);
	expect(box.y).toBeLessThan(viewport.height / 2);

	await page.screenshot({ path: '.tmp/command-palette-open.png' });
});

test('command palette filters by text', async ({ page }) => {
	await page.goto('/');
	await expectControlPlaneLoaded(page);
	await page.keyboard.press('Control+k');

	const dialog = page.getByRole('dialog', { name: 'Command palette' });
	await dialog.getByRole('combobox', { name: 'Filter commands' }).fill('evidence');

	await expect(dialog.getByRole('option', { name: /Open evidence/ })).toBeVisible();
	await expect(dialog.getByRole('option', { name: /Go to Workbench/ })).toHaveCount(0);
	await expect(dialog.getByRole('option', { name: /Configure runtime/ })).toHaveCount(0);
});

test('disabled commands show the reason and cannot be activated', async ({ page }) => {
	await routeEmptyEvidenceAndApprovals(page);
	await page.goto('/');
	await expectControlPlaneLoaded(page);
	await page.keyboard.press('Control+k');

	const dialog = page.getByRole('dialog', { name: 'Command palette' });

	const evidence = dialog.getByRole('option', { name: /Open evidence/ });
	await expect(evidence).toHaveAttribute('aria-disabled', 'true');
	await expect(evidence).toContainText('No evidence yet');

	const approvals = dialog.getByRole('option', { name: /Review pending approvals/ });
	await expect(approvals).toHaveAttribute('aria-disabled', 'true');
	await expect(approvals).toContainText('No pending approvals');

	// Clicking a disabled command is a no-op: the palette stays open and the URL does not change.
	// (force:true bypasses Playwright's own aria-disabled actionability guard to exercise the handler guard.)
	await evidence.click({ force: true });
	await expect(dialog).toBeVisible();
	expect(page.url()).not.toContain('#evidence');
});

test('keyboard navigation skips disabled items and Escape restores focus', async ({ page }) => {
	await routeEmptyEvidenceAndApprovals(page);
	await page.goto('/');
	await expectControlPlaneLoaded(page);

	// Open via the header trigger so focus restoration has a known target.
	const trigger = page.getByRole('button', { name: 'Open command palette' });
	await trigger.click();
	const dialog = page.getByRole('dialog', { name: 'Command palette' });
	const combobox = dialog.getByRole('combobox', { name: 'Filter commands' });
	await expect(combobox).toBeFocused();

	// First enabled action is highlighted on open.
	await expect(combobox).toHaveAttribute('aria-activedescendant', 'command-palette-option-go-workbench');

	// ArrowDown skips the disabled "Open evidence" and lands on "Open settings group".
	await page.keyboard.press('ArrowDown');
	await expect(combobox).toHaveAttribute('aria-activedescendant', 'command-palette-option-open-settings');

	// Escape closes the palette and restores focus to the trigger button.
	await page.keyboard.press('Escape');
	await expect(dialog).toBeHidden();
	await expect(trigger).toBeFocused();

	// Reopening resets the highlight back to the first action.
	await trigger.click();
	await expect(combobox).toHaveAttribute('aria-activedescendant', 'command-palette-option-go-workbench');
});

test('Enter runs the highlighted command', async ({ page }) => {
	await routeEmptyEvidenceAndApprovals(page);
	await page.goto('/');
	await expectControlPlaneLoaded(page);

	await page.keyboard.press('Control+k');
	const dialog = page.getByRole('dialog', { name: 'Command palette' });
	const combobox = dialog.getByRole('combobox', { name: 'Filter commands' });
	await expect(combobox).toBeFocused();
	// A fresh open highlights the first enabled action.
	await expect(combobox).toHaveAttribute('aria-activedescendant', 'command-palette-option-go-workbench');

	await page.keyboard.press('Enter');
	await expect(dialog).toBeHidden();
	expect(page.url()).toContain('#workbench');
});

test('Ctrl+Alt+A opens approvals only when approvals are pending', async ({ page }) => {
	await routeEmptyEvidenceAndApprovals(page);
	await page.goto('/');
	await expectControlPlaneLoaded(page);

	// Disabled action -> wired shortcut is inert.
	await page.keyboard.press('Control+Alt+a');
	await expect(page.getByRole('dialog', { name: 'Approval drawer' })).toHaveCount(0);
});
