import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 1280, height: 800 } });
test.afterEach(async ({ page }) => page.unrouteAll({ behavior: 'ignoreErrors' }));

const APPROVE = /Approve runtime risk|Aprobar riesgo del runtime/;
const REASON = /Reason for approval|Motivo de aprobación/;
const CONSENT = /I accept the runtime risk|Acepto el riesgo de runtime/;
const proposals = Array.from({ length: 12 }, (_, index) => ({
	role: `role-${index + 1}`, providerId: `provider-${index % 2 + 1}`, model: `model-${index + 1}`,
	runtime: 'api', risk: 'high', decisionId: `decision-${index + 1}`,
	taskId: 'shared-task', agentProfileId: `profile-${index + 1}`,
}));

/** Isolated HTTP fixtures: no approval, job, provider or inference reaches the backend. */
async function openRiskReview(page, payload = {}) {
	const { projects } = await (await page.request.get('/api/v1/projects')).json();
	const project = projects.find((entry) => entry.status === 'active');
	expect(project).toBeTruthy();
	const thread = {
		id: 'thread-risk-review', projectId: project.id, ownerId: project.id, ownerType: 'workspace',
		title: 'Runtime risk review fixture', summary: '', status: 'blocked', metadata: {},
		createdAt: '2026-09-21T16:00:00Z', updatedAt: '2026-09-21T16:00:00Z',
	};
	const remediation = {
		id: 'remediation-runtime-risk', threadId: thread.id, projectId: project.id, loopId: 'loop-runtime-risk',
		stage: 'resource_manager', blockerType: 'runtime_risk_review_required', actionType: 'approve_runtime_risk',
		title: 'Approve runtime risk', description: 'Explicit review of the selected runtimes is required.', primary: true,
		payload: { proposals, actionRequestId: 'request-runtime-risk', jobId: 'job-runtime-risk',
			expiresAt: new Date(Date.now() + 3_600_000).toISOString(), ...payload },
		status: 'pending', createdAt: thread.createdAt, resolvedAt: null,
	};
	const event = (sequence, type, payload) => ({
		id: `risk-event-${sequence}`, threadId: thread.id, projectId: project.id,
		sequence, type, payload, metadata: {}, createdAt: thread.createdAt,
	});
	const state = { remediations: [remediation], executions: [], threadStatus: 'blocked', events: [
		event(1, 'worker_claimed', { jobId: 'job-runtime-risk' }),
		event(2, 'blocked', { stage: 'resource_manager', loopId: remediation.loopId, reason: 'Runtime risk requires explicit review.' }),
	] };
	await page.route('**/api/v1/overview', async (route) => {
		const response = await route.fetch();
		const overview = await response.json();
		await route.fulfill({ response, json: { ...overview, threads: [...overview.threads, { ...thread, status: state.threadStatus }] } });
	});
	await page.route(`**/api/v1/threads/${thread.id}`, (route) => route.fulfill({ json: {
		thread: { ...thread, status: state.threadStatus }, messages: [], decisions: [], artifacts: [], events: state.events,
	} }));
	await page.route(`**/api/v1/threads/${thread.id}/events?*`, (route) => {
		const afterSeq = Number(new URL(route.request().url()).searchParams.get('afterSeq') ?? 0);
		return route.fulfill({ json: {
			events: state.events.filter((entry) => entry.sequence > afterSeq),
			lastSeq: state.events.at(-1).sequence, running: false, threadStatus: state.threadStatus,
		} });
	});
	await page.route(`**/api/v1/threads/${thread.id}/remediations`, (route) =>
		route.fulfill({ json: { remediations: state.remediations } }));
	await page.route('**/api/v1/remediations/remediation-runtime-risk/execute', (route) => {
		state.executions.push(route.request().postDataJSON());
		return route.fulfill({ status: 409, json: { detail: 'Unexpected fixture approval.' } });
	});
	await page.goto('/#threads');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const threadButton = page.getByRole('button', { name: thread.title, exact: true });
	if (!(await threadButton.isVisible())) {
		await page.locator('.thread-workspace-head').filter({ hasText: project.name }).click();
	}
	await threadButton.click();
	const card = page.locator('.thread-execution-pane').getByRole('group', {
		name: /Runtime risk review required|Se requiere revisar el riesgo del runtime/,
	});
	await expect(card).toBeVisible();
	return { card, state, remediation, event };
}

test('Runtime risk requires a reason and unchecked consent for all exact proposals, then reports queued', async ({ page }) => {
	const { card, state, remediation, event } = await openRiskReview(page);
	let release;
	const responseGate = new Promise((resolve) => { release = resolve; });
	await page.route('**/api/v1/remediations/remediation-runtime-risk/execute', async (route) => {
		state.executions.push(route.request().postDataJSON());
		await responseGate;
		state.remediations = [];
		state.threadStatus = 'queued';
		state.events.push(event(3, 'runtime_risk_approved', {
			status: 'queued', loopId: remediation.loopId, jobId: 'continuation-job',
		}));
		await route.fulfill({ json: {
			remediation: { ...remediation, status: 'resolved' },
			execution: { status: 'queued', jobId: 'continuation-job', reason: 'Continuation is waiting for a worker.' },
		} });
	});
	try {
		const visibleProposals = card.getByRole('list', { name: /Proposed runtimes|Runtimes propuestos/ }).getByRole('listitem');
		await expect(visibleProposals).toHaveCount(12);
		for (let index = 0; index < proposals.length; index += 1) {
			for (const field of ['role', 'providerId', 'model', 'risk']) {
				await expect(visibleProposals.nth(index).getByText(proposals[index][field], { exact: true })).toBeVisible();
			}
		}
		await expect(card.getByText(remediation.payload.actionRequestId, { exact: true })).toBeHidden();
		await expect(card.locator('time')).toHaveAttribute('datetime', remediation.payload.expiresAt);
		await expect(card.locator('time')).not.toHaveText(remediation.payload.expiresAt);
		await expect(card.getByText(/in this thread and this run|en este hilo y esta ejecución/)).toBeVisible();
		await card.getByRole('button', { name: /Approval details|Detalles de la aprobación/ }).click();
		const rows = card.getByRole('table', { name: /Proposed runtimes|Runtimes propuestos/ }).locator('tbody tr');
		await expect(rows).toHaveCount(12);
		for (let index = 0; index < proposals.length; index += 1) {
			for (const value of Object.values(proposals[index])) {
				await expect(rows.nth(index).getByRole('cell', { name: value, exact: true })).toBeVisible();
			}
		}
		for (const value of [remediation.projectId, remediation.threadId, remediation.loopId,
			remediation.payload.jobId, remediation.payload.actionRequestId, remediation.payload.expiresAt]) {
			await expect(card.getByText(value, { exact: true })).toBeVisible();
		}
		await expect(card.getByText(/does not approve budgets, costs or permissions|no aprueba presupuestos, costos ni permisos/i)).toBeVisible();
		const reason = card.getByRole('textbox', { name: REASON });
		const consent = card.getByRole('checkbox', { name: CONSENT });
		const approve = card.getByRole('button', { name: APPROVE });
		await expect(consent).not.toBeChecked();
		await expect(approve).toBeDisabled();
		await consent.check();
		await reason.fill('   ');
		await expect(approve).toBeDisabled();
		await consent.uncheck();
		await reason.fill('Reviewed all role and model risks for this continuation.');
		await expect(approve).toBeDisabled();
		expect(state.executions).toEqual([]);
		await consent.check();
		await approve.click();
		await expect.poll(() => state.executions).toEqual([{ payload: {
			reason: 'Reviewed all role and model risks for this continuation.',
		} }]);
		await expect(approve).toHaveAttribute('aria-busy', 'true');
		await expect(approve).toBeDisabled();
		await expect(reason).toBeDisabled();
		await expect(consent).toBeDisabled();
		release();
		await expect(card).toBeHidden();
		const toast = page.getByLabel('Notifications').getByRole('status').filter({
			hasText: /Runtime risk approved; continuation queued|Riesgo de runtime aprobado; continuación en cola/,
		});
		await expect(toast).toBeVisible();
		await expect(toast).toHaveAttribute('data-tone', 'ok');
		const execution = page.locator('.thread-execution-pane');
		await expect(execution.getByRole('region', { name: /Waiting for worker|Esperando worker/ })).toBeVisible({ timeout: 20_000 });
		await expect(execution.locator('.thread-remediation-card')).toHaveCount(0);
		await expect(execution.locator('.thread-pipeline-step[data-state="blocked"]')).toHaveCount(0);
		await expect(execution.locator('.thread-console-row[data-type="blocked"] .thread-console-body > p')).toHaveText('Runtime risk requires explicit review.');
		expect(state.executions).toHaveLength(1);
	} finally {
		release();
	}
});

for (const outcome of ['HTTP 409', 'blocked execution']) {
	test(`Runtime risk preserves the reason and exposes an inline error after ${outcome}`, async ({ page }) => {
		const { card, state, remediation } = await openRiskReview(page);
		await page.route('**/api/v1/remediations/remediation-runtime-risk/execute', async (route) => {
			state.executions.push(route.request().postDataJSON());
			const detail = 'The runtime approval scope changed. Review the current request.';
			await route.fulfill(outcome === 'HTTP 409'
				? { status: 409, json: { detail } }
				: { json: { remediation, execution: { status: 'blocked', reason: detail } } });
		});
		const reason = card.getByRole('textbox', { name: REASON });
		await reason.fill('Reviewed the proposed runtimes for these roles.');
		await card.getByRole('checkbox', { name: CONSENT }).check();
		await card.getByRole('button', { name: APPROVE }).click();
		await expect(card.getByRole('alert')).toContainText('runtime approval scope changed');
		await expect(reason).toHaveAttribute('aria-invalid', 'true');
		await expect(reason).toHaveValue('Reviewed the proposed runtimes for these roles.');
		const consent = card.getByRole('checkbox', { name: CONSENT });
		const approve = card.getByRole('button', { name: APPROVE });
		await expect(consent).not.toBeChecked();
		await expect(consent).toBeDisabled();
		await expect(approve).toBeDisabled();
		await expect(card.getByText(/Refresh the actions and review|Actualiza las acciones y revisa/)).toBeVisible();
		// A failed refresh cannot unlock consent to the obsolete server snapshot.
		await page.route('**/api/v1/threads/thread-risk-review/remediations', (route) =>
			route.fulfill({ status: 503, json: { detail: 'Temporary fixture read failure.' } }));
		const refresh = card.getByRole('button', { name: /Refresh actions|Actualizar acciones/ });
		await refresh.click();
		await expect(page.locator('.thread-execution-pane .thread-remediation-load-error')).toBeVisible();
		await expect(consent).toBeDisabled();
		await expect(approve).toBeDisabled();
		await page.route('**/api/v1/threads/thread-risk-review/remediations', (route) =>
			route.fulfill({ json: { remediations: state.remediations } }));
		await refresh.click();
		await expect(consent).toBeEnabled();
		await expect(consent).not.toBeChecked();
		await expect(approve).toBeDisabled();
		await expect(reason).toHaveValue('Reviewed the proposed runtimes for these roles.');
		await expect(card.getByRole('alert')).toContainText('runtime approval scope changed');
		await consent.check();
		await expect(approve).toBeEnabled();
		expect(state.executions).toHaveLength(1);
	});
}

test('Runtime risk requires fresh consent when the displayed proposals change without erasing the reason', async ({ page }) => {
	const { card, state, remediation } = await openRiskReview(page);
	const reason = card.getByRole('textbox', { name: REASON });
	const consent = card.getByRole('checkbox', { name: CONSENT });
	await reason.fill('Reviewed the proposals in this scope.');
	await consent.check();
	await expect(card.getByRole('button', { name: APPROVE })).toBeEnabled();
	state.remediations = [{ ...remediation, payload: { ...remediation.payload,
		proposals: [{ ...proposals[0], model: 'replacement-model' }, ...proposals.slice(1)],
	} }];
	await card.getByRole('button', { name: /Refresh actions|Actualizar acciones/ }).click();
	await expect(card.getByRole('list', { name: /Proposed runtimes|Runtimes propuestos/ }).getByText('replacement-model', { exact: true })).toBeVisible();
	await expect(consent).not.toBeChecked();
	await expect(reason).toHaveValue('Reviewed the proposals in this scope.');
	await expect(card.getByRole('button', { name: APPROVE })).toBeDisabled();
	expect(state.executions).toEqual([]);
});

test('Runtime risk approval expires while the review remains open', async ({ page }) => {
	const start = new Date('2026-09-21T16:00:00Z');
	await page.clock.install({ time: start });
	const { card, state } = await openRiskReview(page, { expiresAt: new Date(start.getTime() + 60_000).toISOString() });
	await card.getByRole('textbox', { name: REASON }).fill('Reviewed before expiry.');
	await card.getByRole('checkbox', { name: CONSENT }).check();
	await expect(card.getByRole('button', { name: APPROVE })).toBeEnabled();
	await page.clock.fastForward(61_000);
	await expect(card.getByRole('alert')).toContainText(/expired|caducó/);
	await expect(card.getByRole('button', { name: APPROVE })).toBeDisabled();
	expect(state.executions).toEqual([]);
});

for (const [label, payload] of [
	['expired', { expiresAt: '2020-01-01T00:00:00Z' }],
	['invalid expiry', { expiresAt: 'invalid' }],
	['empty', { proposals: [] }],
	['incomplete', { proposals: [{ ...proposals[0], model: '' }] }],
]) {
	test(`Runtime risk cannot approve an ${label} request`, async ({ page }) => {
		const { card, state } = await openRiskReview(page, payload);
		await expect(card.getByRole('alert')).toContainText(/expired|incomplete|caducó|incompleta/i);
		await expect(card.getByRole('checkbox', { name: CONSENT })).toBeDisabled();
		await expect(card.getByRole('button', { name: APPROVE })).toBeDisabled();
		expect(state.executions).toEqual([]);
	});
}
