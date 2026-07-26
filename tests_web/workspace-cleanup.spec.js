import { expect, test } from '@playwright/test';

/**
 * Workspaces → Workspace cleanup: the confirmed drain of orphan runtime worktrees.
 *
 * The real control plane serves the page and the project; the two cleanup endpoints are
 * route-mocked so the spec can assert the CONTRACT of the flow deterministically: the plan
 * is read-only, every candidate arrives preselected but reviewable, nothing is posted until
 * the confirmation dialog, and the POST carries exactly the ids the operator reviewed.
 */

async function getWriteToken(page) {
	const handshake = await page.request.get('/api/v1/security/handshake');
	return (await handshake.json()).token;
}

async function createWebProject(page) {
	const token = await getWriteToken(page);
	const suffix = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
	const response = await page.request.post('/api/v1/projects', {
		headers: { 'X-Local-Control-Token': token },
		data: {
			name: `Cleanup Project ${suffix}`,
			path: `./.tmp/cleanup-project-${suffix}`,
			templateId: 'other',
			createDirectory: true,
		},
	});
	expect(response.status()).toBe(201);
	return (await response.json()).project;
}

function planFixture(projectId) {
	return {
		projectId,
		projectName: 'Cleanup Project',
		generatedAt: '2026-07-26T12:00:00.000Z',
		candidates: [
			{
				workspaceId: 'workspace-archived-thread',
				taskId: 'product-loop-abc123def456',
				ownerAgentId: 'developer_agent',
				path: 'H:/fake/.tmp/workspaces/workspace-archived-thread',
				pathExists: true,
				isolationType: 'git_worktree',
				branch: 'codex/product-loop-abc123def456',
				reason: 'thread_archived',
				threadId: 'thread-1',
				threadTitle: 'Archived thread',
				updatedAt: '2026-07-20T10:00:00.000Z',
			},
			{
				workspaceId: 'workspace-missing-path',
				taskId: 'story-missing',
				ownerAgentId: 'developer_agent',
				path: 'H:/fake/.tmp/workspaces/workspace-missing-path',
				pathExists: false,
				isolationType: 'git_worktree',
				branch: 'codex/story-missing',
				reason: 'path_missing',
				threadId: null,
				threadTitle: null,
				updatedAt: '2026-07-19T10:00:00.000Z',
			},
		],
		repoOrphans: [
			{
				path: 'H:/fake/.tmp/workspaces/orphan-manual',
				branch: 'orphan/manual',
				directoryExists: true,
				prunable: false,
			},
		],
		summary: { activeWorkspaceCount: 5, candidateCount: 2, repoOrphanCount: 1 },
	};
}

function applyFixture(projectId) {
	return {
		projectId,
		results: [
			{
				workspaceId: 'workspace-archived-thread',
				status: 'archived',
				reason: 'thread_archived',
				worktreeCleanup: 'removed',
				branchCleanup: null,
			},
		],
		orphanResults: [
			{ path: 'H:/fake/.tmp/workspaces/orphan-manual', status: 'removed', reason: null },
		],
		prune: { status: 'completed', stderr: null },
		summary: {
			archivedCount: 1,
			skippedCount: 0,
			orphanRemovedCount: 1,
			orphanRefusedCount: 0,
			branchesDeletedCount: 0,
		},
	};
}

test('the real cleanup plan endpoint answers read-only for a fresh project', async ({ page }) => {
	const project = await createWebProject(page);
	const response = await page.request.get(
		`/api/v1/projects/${project.id}/workspaces/cleanup/plan`,
	);
	expect(response.status()).toBe(200);
	const plan = await response.json();
	expect(plan.projectId).toBe(project.id);
	expect(plan.candidates).toEqual([]);
});

test('cleanup drains only the reviewed selection and only after confirmation', async ({ page }) => {
	const project = await createWebProject(page);
	let applyBody = null;
	await page.route('**/api/v1/projects/*/workspaces/cleanup/plan', (route) =>
		route.fulfill({ json: planFixture(project.id) }),
	);
	await page.route('**/api/v1/projects/*/workspaces/cleanup', (route) => {
		applyBody = route.request().postDataJSON();
		return route.fulfill({ json: applyFixture(project.id) });
	});

	await page.goto('/#workspaces');
	const panel = page.locator('section.surface', { hasText: 'Workspace cleanup' });
	await panel.getByLabel('Project').selectOption(project.id);
	await panel.getByRole('button', { name: 'Analyze candidates' }).click();

	await expect(panel.getByText('2 cleanup candidates')).toBeVisible();
	await expect(panel.getByText('1 orphan worktrees')).toBeVisible();
	await expect(panel.getByText('Thread archived')).toBeVisible();
	await expect(panel.getByText('Path missing')).toBeVisible();

	// Every candidate arrives preselected; the operator can drop one before confirming.
	const missingPathRow = panel.locator('label.checkbox-row', { hasText: 'codex/story-missing' });
	await expect(missingPathRow.locator('input[type="checkbox"]')).toBeChecked();
	await missingPathRow.locator('input[type="checkbox"]').uncheck();

	await panel.getByRole('button', { name: 'Clean up selected' }).click();
	const dialog = page.getByRole('dialog', { name: 'Confirm workspace cleanup' });
	await expect(dialog).toBeVisible();
	await expect(dialog.getByText('discarded permanently', { exact: false })).toBeVisible();
	// Nothing was posted while the dialog is still open.
	expect(applyBody).toBeNull();

	await dialog.getByRole('button', { name: 'Confirm cleanup' }).click();
	await expect(page.getByText('Workspace cleanup applied')).toBeVisible();
	expect(applyBody).not.toBeNull();
	expect(applyBody.workspaceIds).toEqual(['workspace-archived-thread']);
	expect(applyBody.orphanWorktreePaths).toEqual(['H:/fake/.tmp/workspaces/orphan-manual']);
	expect(applyBody.deleteBranches).toBe(false);
});
