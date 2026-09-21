import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 1280, height: 800 } });
test.afterEach(async ({ page }) => page.unrouteAll({ behavior: 'ignoreErrors' }));

const APPLY_RESEARCH = /Apply research|Incorporar investigación/;
const RESEARCH_LABEL = /Research report|Reporte de investigación/;
const eligibleReport = {
	researchRunId: 'research-reviewed', label: 'Verified technical decision', eligible: true,
};

/** All thread state and writes are fixtures; no job or provider is executed by these UI tests. */
async function openResearchBlocker(page, { candidates = [eligibleReport], artifacts = [], stage = 'research', eventTail = [] } = {}) {
	const { projects } = await (await page.request.get('/api/v1/projects')).json();
	const project = projects.find((entry) => entry.status === 'active');
	expect(project).toBeTruthy();
	const thread = {
		id: 'thread-research-resolution', projectId: project.id, ownerId: project.id,
		ownerType: 'workspace', title: 'Research evidence selection fixture', summary: '',
		status: 'blocked', metadata: {}, createdAt: '2026-09-20T12:00:00Z', updatedAt: '2026-09-20T12:00:00Z',
	};
	const event = (sequence, type, payload = {}) => ({
		id: `research-stage-${sequence}`, threadId: thread.id, projectId: project.id,
		sequence, type, payload, metadata: {}, createdAt: thread.createdAt,
	});
	const events = [
		event(1, 'runtime_check'), event(2, 'git_check'),
		event(3, 'blocked', { stage, loopId: 'loop-research', reason: 'Research evidence required.' }),
		...eventTail.map((entry) => event(entry.sequence, entry.type, entry.payload)),
	];
	const remediation = {
		id: 'remediation-research', threadId: thread.id, projectId: project.id, loopId: 'loop-research',
		stage: 'research', blockerType: 'research_required', actionType: 'retry_loop',
		title: 'Apply research', description: 'Choose research evidence.', primary: true,
		payload: { reason: 'Research evidence required.', researchCandidates: candidates },
		status: 'pending', createdAt: thread.createdAt, resolvedAt: null,
	};
	const state = { remediations: [remediation], executions: [], events, threadStatus: 'blocked', running: false };
	await page.route('**/api/v1/overview', async (route) => {
		const response = await route.fetch();
		const overview = await response.json();
		await route.fulfill({ response, json: { ...overview, threads: [...overview.threads, thread] } });
	});
	await page.route(`**/api/v1/threads/${thread.id}`, (route) => route.fulfill({ json: {
		thread: { ...thread, status: state.threadStatus }, messages: [], decisions: [],
		artifacts: artifacts.map((artifact) => ({ projectId: project.id, artifactId: artifact.id, ...artifact })),
		events: state.events,
	} }));
	await page.route(`**/api/v1/threads/${thread.id}/events?*`, (route) => {
		const afterSeq = Number(new URL(route.request().url()).searchParams.get('afterSeq') ?? 0);
		return route.fulfill({ json: {
			events: state.events.filter((entry) => entry.sequence > afterSeq),
			lastSeq: state.events.at(-1).sequence, running: state.running, threadStatus: state.threadStatus,
		} });
	});
	await page.route(`**/api/v1/threads/${thread.id}/remediations`, (route) =>
		route.fulfill({ json: { remediations: state.remediations } }),
	);
	await page.goto('/#threads');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const threadButton = page.getByRole('button', { name: thread.title, exact: true });
	if (!(await threadButton.isVisible())) {
		await page.locator('.thread-workspace-head').filter({ hasText: project.name }).click();
	}
	await threadButton.click();
	const execution = page.locator('.thread-execution-pane');
	const card = execution.getByRole('group', { name: /Research evidence required|Evidencia de investigación requerida/ });
	await expect(card).toBeVisible();
	return { state, card, execution, remediation, event };
}

test('Research resolution requires an explicit report, sends only its id and stays busy until completion', async ({ page }) => {
	let release;
	const responseGate = new Promise((resolve) => { release = resolve; });
	const { state, card, remediation } = await openResearchBlocker(page);
	await page.route('**/api/v1/remediations/remediation-research/execute', async (route) => {
		state.executions.push(route.request().postDataJSON());
		await responseGate;
		state.remediations = [];
		await route.fulfill({ json: {
			remediation: { ...remediation, status: 'resolved' },
			execution: { status: 'completed', action: 'retry_loop', reason: 'Research evidence incorporated.' },
		} });
	});
	try {
		const select = card.getByRole('combobox', { name: RESEARCH_LABEL });
		const apply = card.getByRole('button', { name: APPLY_RESEARCH });
		await expect(select).toHaveValue('');
		await expect(apply).toBeDisabled();
		await expect(card.getByText(/Adds evidence and preserves|Agrega evidencia y conserva/)).toBeVisible();
		expect(state.executions).toEqual([]);
		await select.selectOption(eligibleReport.researchRunId);
		await apply.click();
		await expect.poll(() => state.executions).toEqual([{ payload: { researchRunId: eligibleReport.researchRunId } }]);
		await expect(apply).toHaveAttribute('aria-busy', 'true');
		await expect(apply).toBeDisabled();
		await expect(select).toBeDisabled();
		release();
		await expect(card).toBeHidden();
		await expect(page.getByLabel('Notifications').getByText('Research evidence incorporated.')).toBeVisible();
		expect(state.executions).toHaveLength(1);
	} finally {
		release();
	}
});

test('Research incorporation succeeds when its continuation is queued', async ({ page }) => {
	const { state, card, remediation } = await openResearchBlocker(page);
	await page.route('**/api/v1/remediations/remediation-research/execute', async (route) => {
		state.executions.push(route.request().postDataJSON());
		state.remediations = [];
		await route.fulfill({ json: {
			remediation: { ...remediation, status: 'resolved' },
			execution: { status: 'queued', action: 'retry_loop', reason: 'Research evidence incorporated; continuation is waiting for a worker.' },
		} });
	});
	await card.getByRole('combobox', { name: RESEARCH_LABEL }).selectOption(eligibleReport.researchRunId);
	await card.getByRole('button', { name: APPLY_RESEARCH }).click();
	await expect(card).toBeHidden();
	const notification = page.getByLabel('Notifications').getByRole('status').filter({
		hasText: /Research applied; continuation queued|Investigación incorporada; continuación en cola/,
	});
	await expect(notification).toBeVisible();
	await expect(notification).toHaveAttribute('data-tone', 'ok');
	await expect(notification).toContainText('continuation is waiting for a worker');
	expect(state.executions).toEqual([{ payload: { researchRunId: eligibleReport.researchRunId } }]);
});

test('Research ready without an eligible technical decision cannot be selected or applied', async ({ page }) => {
	const { card } = await openResearchBlocker(page, { candidates: [{
		researchRunId: 'ready-without-decision', label: 'Ready recommendation', eligible: false,
		reason: 'A validated technical decision is missing.',
	}, { researchRunId: 'ready-without-eligibility', label: 'Ready alone', status: 'research_ready' }] });
	const select = card.getByRole('combobox', { name: RESEARCH_LABEL });
	await expect(select).toHaveValue('');
	await expect(select.locator('option[value="ready-without-decision"]')).toBeDisabled();
	await expect(select.locator('option[value="ready-without-decision"]')).toContainText('technical decision is missing');
	await expect(select.locator('option[value="ready-without-eligibility"]')).toBeDisabled();
	await expect(card.getByText(/No eligible report is available|No hay un reporte elegible/)).toBeVisible();
	await expect(card.getByRole('button', { name: APPLY_RESEARCH })).toBeDisabled();
});

for (const outcome of ['HTTP 409', 'blocked execution']) {
	test(`Research resolution preserves the thread, selection and inline error after ${outcome}`, async ({ page }) => {
		const { state, card, remediation } = await openResearchBlocker(page);
		const reason = 'Research evidence changed. Choose a current report.';
		await page.route('**/api/v1/remediations/remediation-research/execute', async (route) => {
			state.executions.push(route.request().postDataJSON());
			await route.fulfill(outcome === 'HTTP 409'
				? { status: 409, json: { detail: reason } }
				: { json: { remediation, execution: { status: 'blocked', action: 'retry_loop', reason } } });
		});
		const select = card.getByRole('combobox', { name: RESEARCH_LABEL });
		const apply = card.getByRole('button', { name: APPLY_RESEARCH });
		await select.selectOption(eligibleReport.researchRunId);
		await apply.click();
		await expect(card.getByRole('alert')).toContainText('Research evidence changed');
		await expect(page.locator('.thread-conversation-title').getByRole('heading', { name: 'Research evidence selection fixture', exact: true })).toBeVisible();
		await expect(page.locator('.thread-row[aria-current="true"]')).toContainText('Research evidence selection fixture');
		await expect(select).toHaveAttribute('aria-invalid', 'true');
		await expect(select).toHaveValue(eligibleReport.researchRunId);
		await expect(apply).toBeEnabled();
		expect(state.executions).toHaveLength(1);
	});
}

test('Research adoption supersedes an auxiliary job blocker and a later failure still wins', async ({ page }) => {
	const historicalReason = 'ResearchAgent web search is disabled by policy.';
	const { state, card, execution, remediation, event } = await openResearchBlocker(page, {
		eventTail: [
			{ sequence: 4, type: 'worker_claimed', payload: { jobId: 'old-research-job' } },
			{ sequence: 5, type: 'blocked', payload: { jobId: 'old-research-job', reason: historicalReason, status: 'blocked' } },
		],
	});
	// The auxiliary job omitted stage/loopId; the pending remediation retains the current stage.
	await expect(execution.locator('.thread-pipeline-step', { hasText: /Discovery and research|Definición e investigación/ })).toHaveAttribute('data-state', 'blocked');
	await expect(execution.locator('.thread-pipeline-step', { hasText: /Git check|Verificación de Git/ })).toHaveAttribute('data-state', 'done');
	await page.route('**/api/v1/remediations/remediation-research/execute', async (route) => {
		state.remediations = [];
		state.threadStatus = 'queued';
		state.events.push(event(6, 'research_adopted', {
			loopId: 'loop-research', receiptId: 'research-receipt', researchRunId: eligibleReport.researchRunId, status: 'queued',
		}));
		await route.fulfill({ json: {
			remediation: { ...remediation, status: 'resolved' }, execution: { status: 'queued', action: 'retry_loop' },
		} });
	});
	await card.getByRole('combobox', { name: RESEARCH_LABEL }).selectOption(eligibleReport.researchRunId);
	await card.getByRole('button', { name: APPLY_RESEARCH }).click();
	await expect(execution.getByRole('region', { name: /Waiting for worker|Esperando worker/ })).toBeVisible();
	await expect(execution.locator('.thread-remediation-card')).toHaveCount(0);
	await expect(execution.locator('.thread-pipeline-step[data-state="blocked"]')).toHaveCount(0);
	await expect(execution.locator('.thread-console-row[data-type="blocked"]').last().locator('.thread-console-body > p')).toHaveText(historicalReason);
	// Lifecycle events have progressed while the thread status snapshot remains stale.
	state.threadStatus = 'blocked';
	state.running = true;
	state.events.push(event(7, 'worker_claimed', { jobId: 'continuation-job' }), event(8, 'discovery', { loopId: 'loop-research' }));
	await expect(execution.locator('.thread-console-row[data-type="discovery"]')).toBeVisible();
	await expect(execution.locator('.thread-pipeline-step[data-state="blocked"]')).toHaveCount(0);
	await expect(execution.locator('.thread-remediation-card')).toHaveCount(0);
	// A new failure after adoption must still block and offer its own repair fallback.
	state.running = false;
	state.events.push(event(9, 'blocked', { loopId: 'loop-research', stage: 'resource_manager', reason: 'A new resource review is required.' }));
	await expect(execution.locator('.thread-pipeline-step', { hasText: /Runtime check|Verificación de runtime/ })).toHaveAttribute('data-state', 'blocked');
	await expect(execution.locator('.thread-remediation-card').getByText('A new resource review is required.', { exact: true })).toBeVisible();
});

test('Historical blocked reports retain evidence without replay actions and research blocks the correct pipeline stage', async ({ page }) => {
	const { execution } = await openResearchBlocker(page, { artifacts: [{
		id: 'historical-research-artifact', threadId: 'thread-research-resolution', kind: 'research_report',
		title: 'Older research result', createdAt: '2026-09-18T12:00:00Z', metadata: {
			status: 'research_blocked', reason: 'ResearchAgent allowWebSearch=false',
			remediation: { summary: 'Earlier research could not access the web.' },
			sources: [{ publisher: 'Recorded source', url: 'https://example.invalid/research', trustLevel: 'trusted',
				fetchedAt: '2026-09-18T12:00:00Z', hash: 'historical-evidence-hash' }],
		},
	}] });
	await expect(execution.locator('.thread-pipeline-step', { hasText: /Runtime check|Verificación de runtime/ })).toHaveAttribute('data-state', 'done');
	await expect(execution.locator('.thread-pipeline-step', { hasText: /Git check|Verificación de Git/ })).toHaveAttribute('data-state', 'done');
	await expect(execution.locator('.thread-pipeline-step', { hasText: /Discovery and research|Definición e investigación/ })).toHaveAttribute('data-state', 'blocked');
	await expect(execution.locator('.thread-pipeline-step', { hasText: /Branch ready|Rama lista/ })).toHaveCount(0);
	const historical = page.locator('.thread-research-card');
	await expect(historical.getByText(/Research history|Historial de investigación/)).toBeVisible();
	await expect(historical.getByText(/This report is a historical result|Este reporte es un resultado histórico/)).toBeVisible();
	await expect(historical.getByText('ResearchAgent allowWebSearch=false').first()).toBeVisible();
	await expect(historical.getByRole('link', { name: 'Recorded source' })).toHaveAttribute('href', 'https://example.invalid/research');
	await expect(historical.getByRole('button', { name: /Run now|Ejecutar ahora|Retry|Reintentar|Apply research|Incorporar investigación/ })).toHaveCount(0);
	const inspector = page.locator('.inspector-panel');
	await inspector.getByRole('tab', { name: /Research|Investigación/ }).click();
	const report = inspector.locator('.thread-inspector-research');
	await expect(report.getByText(/Research history|Historial de investigación/)).toBeVisible();
	await expect(report.getByText(/Recorded result|Resultado registrado/)).toBeVisible();
	await expect(report.getByText(/This report is a historical result|Este reporte es un resultado histórico/)).toBeVisible();
	await expect(report.getByRole('button', { name: /Run now|Ejecutar ahora|Retry|Reintentar/ })).toHaveCount(0);
	await historical.getByRole('button', { name: /Open research|Abrir investigación/ }).click();
	await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible();
});
