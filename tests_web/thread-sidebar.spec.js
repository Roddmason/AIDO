import { expect, test } from '@playwright/test';

async function expectControlPlaneLoaded(page) {
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
}

async function getActiveProject(page) {
	const projectsResponse = await page.request.get('/api/v1/projects');
	const { projects } = await projectsResponse.json();
	const project = projects.find((item) => item.status === 'active');
	expect(project).toBeTruthy();
	return project;
}

async function getWriteToken(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	const { token } = await handshake.json();
	return token;
}

/** Creates a bare thread (status `open`, no queued run) directly through the API. */
async function createThreadViaApi(page, title) {
	const project = await getActiveProject(page);
	const token = await getWriteToken(page);
	const response = await page.request.post('/api/v1/threads', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			projectId: project.id,
			ownerType: 'workspace',
			ownerId: project.id,
			title,
		},
	});
	expect(response.ok()).toBe(true);
	const { thread } = await response.json();
	return { project, token, thread };
}

/** The workspace head starts open only when the project is pre-selected; expand it when needed. */
async function ensureWorkspaceOpen(page) {
	const head = page.locator('.thread-workspace-head').first();
	await expect(head).toBeVisible();
	if ((await head.getAttribute('aria-expanded')) !== 'true') {
		await head.click();
	}
}

function threadRow(page, title) {
	return page.locator('.thread-row', { hasText: title });
}

async function openThreadMenu(page, title) {
	const row = threadRow(page, title);
	await expect(row).toBeVisible({ timeout: 20_000 });
	await row.locator('.thread-menu-trigger').click();
	await expect(page.getByRole('menu')).toBeVisible();
}

test('Sidebar: renaming a thread from the contextual menu changes its title', async ({ page }) => {
	const title = `Sidebar rename source ${Date.now()}`;
	const { thread } = await createThreadViaApi(page, title);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await ensureWorkspaceOpen(page);

	await openThreadMenu(page, title);
	await page.getByRole('menuitem', { name: /Rename|Renombrar/ }).click();

	const renamed = `Sidebar renamed thread ${Date.now()}`;
	const editor = page.getByLabel(/New thread title|Nuevo título del hilo/);
	await expect(editor).toBeVisible();
	const patchRequests = [];
	page.on('request', (request) => {
		if (request.method() === 'PATCH' && request.url().includes(`/api/v1/threads/${thread.id}`)) {
			patchRequests.push(request.url());
		}
	});
	await editor.fill(renamed);
	await editor.press('Enter');

	// The PATCH lands and the refreshed overview re-renders the row with the new title.
	await expect(threadRow(page, renamed)).toBeVisible({ timeout: 20_000 });
	await expect(threadRow(page, title)).toBeHidden();
	// Enter commits exactly once; the blur of the unmounting editor must not duplicate the PATCH.
	expect(patchRequests.length).toBe(1);
});

test('Sidebar: archiving hides the thread immediately and offers an undo toast', async ({
	page,
}) => {
	const title = `Sidebar archive target ${Date.now()}`;
	await createThreadViaApi(page, title);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await ensureWorkspaceOpen(page);

	await openThreadMenu(page, title);
	await page.getByRole('menuitem', { name: /Archive|Archivar/ }).click();

	await expect(page.getByText(/Thread archived|Hilo archivado/)).toBeVisible({ timeout: 20_000 });
	await expect(page.getByRole('button', { name: /Undo|Deshacer/ })).toBeVisible();
	await expect(threadRow(page, title)).toBeHidden({ timeout: 20_000 });
});

test('Sidebar: undo on the archive toast restores the thread in place', async ({ page }) => {
	const title = `Sidebar archive undo ${Date.now()}`;
	await createThreadViaApi(page, title);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await ensureWorkspaceOpen(page);

	await openThreadMenu(page, title);
	await page.getByRole('menuitem', { name: /Archive|Archivar/ }).click();

	// Undo must be pressed while the 8s action toast is still on screen (the real user budget);
	// waiting for the row to disappear first can eat that window on slow (mobile) renders.
	const undoButton = page.getByRole('button', { name: /Undo|Deshacer/ });
	await expect(undoButton).toBeVisible({ timeout: 5_000 });
	await undoButton.click();
	await expect(page.getByText(/Thread restored|Hilo restaurado/)).toBeVisible({ timeout: 20_000 });
	await expect(threadRow(page, title)).toBeVisible({ timeout: 20_000 });
});

test('Sidebar: a queued thread cannot be deleted and explains the blocker', async ({ page }) => {
	const title = `Sidebar delete blocked ${Date.now()}`;
	const { token, thread } = await createThreadViaApi(page, title);
	// A clear actionable goal classifies as execute and leaves the thread `queued` (dashboard-only
	// test servers never run the worker), which is exactly the delete-blocking state.
	const message = await page.request.post(`/api/v1/threads/${thread.id}/messages`, {
		headers: { 'X-Local-Control-Token': token },
		data: { content: `Add an audit endpoint covering sidebar delete blocking ${Date.now()}` },
	});
	expect(message.ok()).toBe(true);

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await ensureWorkspaceOpen(page);
	await openThreadMenu(page, title);

	const deleteItem = page.getByRole('menuitem', { name: /Delete|Eliminar/ });
	await expect(deleteItem).toHaveAttribute('aria-disabled', 'true');
	await expect(page.getByText(/stop the run first|detén el run primero/)).toBeVisible();
	// Selecting the disabled item is a no-op: no confirm dialog and the row stays listed. The
	// forced click bypasses Playwright's enabled-actionability wait on the aria-disabled item.
	await deleteItem.click({ force: true });
	await expect(page.getByRole('dialog', { name: /Delete thread|Eliminar hilo/ })).toHaveCount(0);
	await expect(threadRow(page, title)).toBeVisible();
});

test('Sidebar: the show-archived toggle reveals archived threads', async ({ page }) => {
	const title = `Sidebar archived visible ${Date.now()}`;
	const { token, thread } = await createThreadViaApi(page, title);
	const archiveResponse = await page.request.post(`/api/v1/threads/${thread.id}/archive`, {
		headers: { 'X-Local-Control-Token': token },
		data: { reason: 'Archived by the sidebar Playwright test.' },
	});
	expect(archiveResponse.ok()).toBe(true);

	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await ensureWorkspaceOpen(page);

	// Hidden while the toggle is off (the default view lists active threads only).
	await expect(threadRow(page, title)).toBeHidden();

	await page.getByRole('button', { name: /Show archived|Mostrar archivados/ }).click();
	await expect(page.locator('.thread-archived-head').first()).toBeVisible({ timeout: 20_000 });
	const archivedRow = threadRow(page, title);
	await expect(archivedRow).toBeVisible({ timeout: 20_000 });
	await expect(archivedRow).toHaveClass(/is-archived/);

	await page.getByRole('button', { name: /Hide archived|Ocultar archivados/ }).click();
	await expect(threadRow(page, title)).toBeHidden();
});

test('Sidebar: deleting a thread asks for confirmation before it disappears', async ({ page }) => {
	const title = `Sidebar delete target ${Date.now()}`;
	await createThreadViaApi(page, title);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await ensureWorkspaceOpen(page);

	await openThreadMenu(page, title);
	await page.getByRole('menuitem', { name: /Delete|Eliminar/ }).click();

	// Nothing is deleted yet: the dialog explains the soft delete and asks for confirmation.
	const dialog = page.getByRole('dialog', { name: /Delete thread|Eliminar hilo/ });
	await expect(dialog).toBeVisible();
	await expect(threadRow(page, title)).toBeVisible();

	await dialog.getByRole('button', { name: /^(Delete thread|Eliminar hilo)$/ }).click();
	await expect(dialog).toBeHidden({ timeout: 20_000 });
	await expect(threadRow(page, title)).toBeHidden({ timeout: 20_000 });
});
