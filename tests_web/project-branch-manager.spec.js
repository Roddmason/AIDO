import { expect, test } from '@playwright/test';

/**
 * Sidebar project "⋯" menu + gitflow branch manager.
 *
 * The real control plane serves the shell and the project; the open-folder and branch-manager
 * endpoints are route-mocked so the spec asserts the UI contract deterministically: the menu is
 * keyboard operable, open-folder calls the backend (never a browser path), protected branches are
 * not selectable, and destructive requests carry exactly the reviewed selection — force delete
 * only after the extra acknowledgement, remote delete only when opted in.
 * @author Rodrigo Mason
 */

const SCREENSHOT_DIR = process.env.AIDO_BRANCHES_SCREENSHOT_DIR || '';

async function expectControlPlaneLoaded(page) {
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
}

async function activeProject(page) {
	const response = await page.request.get('/api/v1/projects');
	const { projects } = await response.json();
	const project = projects.find((item) => item.status === 'active');
	expect(project).toBeTruthy();
	return project;
}

function branch(overrides) {
	return {
		ref: `refs/heads/${overrides.name}`,
		kind: 'local',
		remote: null,
		shortName: overrides.name,
		current: false,
		protected: false,
		protectedReason: null,
		lastCommit: {
			hash: 'a'.repeat(40),
			date: '2026-09-20T10:00:00+00:00',
			author: 'Rodrigo Mason',
			subject: `Work on ${overrides.name}`,
		},
		ageDays: 7,
		ahead: 0,
		behind: 0,
		mergeState: 'not_merged',
		mergeEvidence: null,
		upstream: { name: '', status: 'none', ahead: 0, behind: 0 },
		worktreePath: null,
		aidoWorkspaces: [],
		stale: false,
		farBehind: false,
		basedOnMain: false,
		suggestedName: null,
		deletable: false,
		requiresForce: true,
		...overrides,
	};
}

function inventoryFixture(projectId) {
	return {
		status: 'completed',
		reason: 'Branch inventory collected through ToolBroker.',
		projectId,
		workspaceId: 'workspace-1',
		root: '/fake/repo',
		snapshotAt: '2026-09-27T10:00:00+00:00',
		refreshRequired: false,
		currentBranch: 'dev',
		integrationBranch: 'dev',
		integrationExists: true,
		mainlineBranch: 'main',
		remotes: ['origin'],
		staleDays: 30,
		farBehindCommits: 50,
		squashDetection: 'cherry+merge-tree',
		branches: [
			branch({
				name: 'dev',
				type: 'integration',
				current: true,
				protected: true,
				protectedReason: 'integration_branch',
				mergeState: 'merged',
				requiresForce: false,
			}),
			branch({
				name: 'main',
				type: 'integration',
				protected: true,
				protectedReason: 'gitflow_mainline',
				mergeState: 'merged',
				requiresForce: false,
			}),
			branch({
				name: 'feature/merged',
				type: 'feature',
				mergeState: 'merged',
				mergeEvidence: 'merge-base --is-ancestor',
				deletable: true,
				requiresForce: false,
				behind: 3,
				upstream: { name: 'origin/feature/merged', status: 'tracking', ahead: 0, behind: 0 },
			}),
			branch({
				name: 'feature/squashed',
				type: 'feature',
				mergeState: 'squash_probable',
				mergeEvidence: 'merge-tree',
				deletable: true,
				ahead: 2,
				behind: 1,
			}),
			branch({ name: 'feature/wip', type: 'feature', ahead: 1, stale: true, ageDays: 45 }),
			branch({
				name: 'quick-fix-login',
				type: 'other',
				ahead: 1,
				suggestedName: 'bugfix/quick-fix-login',
			}),
		],
		health: {
			total: 6,
			local: 6,
			remote: 0,
			protected: 2,
			merged: 1,
			squashProbable: 1,
			stale: 1,
			nonGitflow: 1,
			farBehind: 0,
			basedOnMain: 0,
			upstreamGone: 0,
			issues: [
				{
					kind: 'merged_not_deleted',
					severity: 'warning',
					branches: ['feature/merged'],
					suggestedAction: 'delete_merged',
				},
				{
					kind: 'squash_probable',
					severity: 'info',
					branches: ['feature/squashed'],
					suggestedAction: 'review_force_delete',
				},
				{
					kind: 'non_gitflow',
					severity: 'info',
					branches: ['quick-fix-login'],
					suggestedAction: 'rename',
				},
			],
		},
		toolCalls: [],
		policyDecisionIds: [],
	};
}

function actionFixture(projectId, results) {
	return {
		status: 'completed',
		reason: 'done',
		projectId,
		workspaceId: 'workspace-1',
		integrationBranch: 'dev',
		results,
		summary: {
			done: results.filter((item) => item.status === 'deleted').length,
			skipped: results.filter((item) => item.status === 'skipped').length,
			failed: 0,
		},
		toolCalls: [],
		policyDecisionIds: [],
	};
}

async function openProjectMenu(page, project) {
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	const trigger = page.getByRole('button', { name: `Project actions: ${project.name}` });
	await expect(trigger).toBeVisible({ timeout: 20_000 });
	return trigger;
}

test('project menu is keyboard operable and opens the folder through the backend', async ({
	page,
}, testInfo) => {
	const project = await activeProject(page);
	const openFolderCalls = [];
	await page.route('**/api/v1/projects/*/open-folder', (route) => {
		openFolderCalls.push({ url: route.request().url(), body: route.request().postData() });
		return route.fulfill({
			json: { status: 'opened', projectId: project.id, path: project.path, launcher: 'explorer.exe' },
		});
	});
	const trigger = await openProjectMenu(page, project);

	await trigger.focus();
	await page.keyboard.press('Enter');
	const menu = page.getByRole('menu', { name: 'Project actions' });
	await expect(menu).toBeVisible();
	for (const name of ['New thread', 'Open folder', 'Copy path', 'Manage branches…', 'Project settings']) {
		await expect(menu.getByRole('menuitem', { name })).toBeVisible();
	}
	await expect(menu.getByRole('menuitem', { name: 'New thread' })).toBeFocused();
	if (SCREENSHOT_DIR) {
		await page.screenshot({ path: `${SCREENSHOT_DIR}/project-menu-${testInfo.project.name}.png` });
	}
	await page.keyboard.press('ArrowDown');
	await expect(menu.getByRole('menuitem', { name: 'Open folder' })).toBeFocused();
	await page.keyboard.press('Escape');
	await expect(menu).toBeHidden();
	await expect(trigger).toBeFocused();

	await trigger.click();
	await page.getByRole('menuitem', { name: 'Open folder' }).click();
	await expect(page.getByText('Folder opened in the file manager')).toBeVisible();
	expect(openFolderCalls).toHaveLength(1);
	expect(openFolderCalls[0].url).toContain(`/api/v1/projects/${project.id}/open-folder`);
	// The browser never sends a path: the server resolves the registered project folder itself.
	expect(openFolderCalls[0].body ?? '').not.toContain('/');
});

test('open folder surfaces a clear error when the folder is gone', async ({ page }) => {
	const project = await activeProject(page);
	await page.route('**/api/v1/projects/*/open-folder', (route) =>
		route.fulfill({
			status: 409,
			json: { detail: `The project folder no longer exists: ${project.path}` },
		}),
	);
	const trigger = await openProjectMenu(page, project);
	await trigger.click();
	await page.getByRole('menuitem', { name: 'Open folder' }).click();
	const notifications = page.getByLabel('Notifications');
	await expect(notifications.getByText('Could not open the project folder.')).toBeVisible();
	await expect(
		notifications.getByText('The project folder no longer exists', { exact: false }),
	).toBeVisible();
});

test('branch manager deletes only the reviewed merged branches, force needs acknowledgement', async ({
	page,
}, testInfo) => {
	const project = await activeProject(page);
	const deleteBodies = [];
	await page.route('**/api/v1/projects/*/git/branch-manager', (route) =>
		route.fulfill({ json: inventoryFixture(project.id) }),
	);
	await page.route('**/api/v1/projects/*/git/branch-manager/scan', (route) =>
		route.fulfill({ json: inventoryFixture(project.id) }),
	);
	await page.route('**/api/v1/projects/*/git/branch-manager/delete', (route) => {
		const body = route.request().postDataJSON();
		deleteBodies.push(body);
		return route.fulfill({
			json: actionFixture(
				project.id,
				body.branches.map((name) => ({
					branch: name,
					kind: 'local',
					action: 'delete',
					status: 'deleted',
					reason: 'merged into dev',
					forced: body.forceBranches.includes(name),
					detail: [],
				})),
			),
		});
	});
	const trigger = await openProjectMenu(page, project);
	await trigger.click();
	await page.getByRole('menuitem', { name: 'Manage branches…' }).click();

	const dialog = page.getByRole('dialog', { name: `Branches — ${project.name}` });
	await expect(dialog).toBeVisible();
	await expect(dialog.getByRole('heading', { name: 'Gitflow health' })).toBeVisible();
	await expect(dialog.getByText('1 branch(es) already merged into dev but not deleted.')).toBeVisible();
	await expect(dialog.getByText('Probably merged (squash)').first()).toBeVisible();
	await expect(dialog.getByRole('checkbox', { name: 'Select dev' })).toBeDisabled();
	await expect(dialog.getByRole('checkbox', { name: 'Select main' })).toBeDisabled();
	if (SCREENSHOT_DIR) {
		await page.screenshot({
			path: `${SCREENSHOT_DIR}/branch-manager-${testInfo.project.name}.png`,
			fullPage: false,
		});
	}

	await dialog.getByRole('button', { name: 'Delete merged branches (1)…' }).click();
	await expect(dialog.getByRole('heading', { name: 'Confirm branch deletion' })).toBeFocused();
	await expect(dialog.locator('.branch-step-list li')).toHaveText(['feature/merged']);
	expect(deleteBodies).toHaveLength(0);
	if (SCREENSHOT_DIR) {
		await page.screenshot({ path: `${SCREENSHOT_DIR}/branch-confirm-${testInfo.project.name}.png` });
	}
	await dialog.getByRole('button', { name: 'Delete 1 branch(es)' }).click();
	await expect(dialog.getByRole('heading', { name: 'Result' })).toBeVisible();
	expect(deleteBodies[0]).toEqual({
		branches: ['feature/merged'],
		forceBranches: [],
		deleteRemote: false,
		remoteBranches: [],
	});
	await expect(dialog.getByText('Deleted', { exact: true })).toBeVisible();

	await dialog.getByRole('button', { name: 'Back to branches' }).click();
	await dialog.getByRole('checkbox', { name: 'Select feature/wip' }).check();
	await dialog.getByRole('button', { name: 'Delete selected…' }).click();
	const confirm = dialog.getByRole('button', { name: 'Delete 1 branch(es)' });
	await expect(confirm).toBeDisabled();
	await dialog.getByText('I understand these commits may be lost', { exact: false }).click();
	await dialog.getByText('Also delete the branch on the remote', { exact: false }).click();
	await expect(confirm).toBeEnabled();
	await confirm.click();
	await expect(dialog.getByRole('heading', { name: 'Result' })).toBeVisible();
	expect(deleteBodies[1]).toEqual({
		branches: ['feature/wip'],
		forceBranches: ['feature/wip'],
		deleteRemote: true,
		remoteBranches: [],
	});

	await page.keyboard.press('Escape');
	await expect(dialog).toBeHidden();
});

test('branch manager renames a branch without prefix to a gitflow name', async ({ page }) => {
	const project = await activeProject(page);
	let renameBody = null;
	await page.route('**/api/v1/projects/*/git/branch-manager', (route) =>
		route.fulfill({ json: inventoryFixture(project.id) }),
	);
	await page.route('**/api/v1/projects/*/git/branch-manager/scan', (route) =>
		route.fulfill({ json: inventoryFixture(project.id) }),
	);
	await page.route('**/api/v1/projects/*/git/branch-manager/rename', (route) => {
		renameBody = route.request().postDataJSON();
		return route.fulfill({
			json: actionFixture(project.id, [
				{
					branch: renameBody.branch,
					kind: 'local',
					action: 'rename',
					status: 'renamed',
					reason: `Renamed to ${renameBody.newName}.`,
					forced: false,
					detail: [],
				},
			]),
		});
	});
	const trigger = await openProjectMenu(page, project);
	await trigger.click();
	await page.getByRole('menuitem', { name: 'Manage branches…' }).click();
	const dialog = page.getByRole('dialog', { name: `Branches — ${project.name}` });
	await dialog.getByRole('button', { name: 'Show and rename' }).click();
	await dialog.getByRole('button', { name: 'Rename quick-fix-login' }).click();
	const field = dialog.getByLabel('New gitflow name');
	await expect(field).toHaveValue('bugfix/quick-fix-login');
	await field.fill('random/name');
	await expect(dialog.getByRole('button', { name: 'Rename branch' })).toBeDisabled();
	await field.fill('bugfix/quick-fix-login');
	await dialog.getByRole('button', { name: 'Rename branch' }).click();
	await expect(dialog.getByText('Renamed', { exact: true })).toBeVisible();
	expect(renameBody).toEqual({ branch: 'quick-fix-login', newName: 'bugfix/quick-fix-login' });
});
