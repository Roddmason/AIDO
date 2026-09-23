/**
 * Endurecimiento del panel de hilos: etiquetas legibles de decisión, tarjeta de research del intake,
 * espera por capacidad del equipo y pipeline detenido sin job activo. Cada caso mockea el overview,
 * el detalle, los eventos y las remediaciones del hilo para que el estado sea determinista.
 * @author Rodrigo Mason
 */
import { expect, test } from './fixtures/operations.js';

test.use({ viewport: { width: 1280, height: 800 } });

test.afterEach(async ({ page }) => {
	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

async function openMockThread(
	page,
	{ id, title, status, events = [], decisions = [], remediations = [], running = false },
) {
	const { projects } = await (await page.request.get('/api/v1/projects')).json();
	const project = projects.find((entry) => entry.status === 'active');
	expect(project).toBeTruthy();
	const createdAt = '2026-09-22T00:00:00Z';
	const thread = {
		id, projectId: project.id, ownerId: project.id, ownerType: 'workspace', title, summary: '',
		status, metadata: {}, createdAt, updatedAt: createdAt,
	};
	const eventRecords = events.map((item) => ({
		id: `${id}-event-${item.sequence}`, threadId: id, projectId: project.id, payload: {},
		metadata: {}, createdAt, ...item,
	}));
	const decisionRecords = decisions.map((item) => ({
		threadId: id, projectId: project.id, messageId: null, status: 'pending', resolution: null,
		decidedBy: null, decidedAt: null, metadata: {}, createdAt, updatedAt: createdAt, ...item,
	}));
	const remediationRecords = remediations.map((item) => ({
		projectId: project.id, threadId: id, loopId: '', title: 'Repair action',
		description: 'A persisted repair action.', payload: {}, status: 'pending',
		createdAt: '2026-09-22T00:00:00.000Z', resolvedAt: null, ...item,
	}));
	await page.route('**/api/v1/overview', async (route) => {
		const response = await route.fetch();
		const overview = await response.json();
		await route.fulfill({ response, json: { ...overview, threads: [...overview.threads, thread] } });
	});
	await page.route(`**/api/v1/threads/${id}`, (route) => route.fulfill({ json: {
		thread, messages: [], decisions: decisionRecords, artifacts: [], events: [],
	} }));
	await page.route(`**/api/v1/threads/${id}/remediations`, (route) =>
		route.fulfill({ json: { remediations: remediationRecords } }),
	);
	await page.route(`**/api/v1/threads/${id}/events?*`, (route) => {
		const afterSeq = Number(new URL(route.request().url()).searchParams.get('afterSeq'));
		return route.fulfill({ json: {
			events: eventRecords.filter((item) => item.sequence > afterSeq),
			lastSeq: eventRecords.at(-1)?.sequence ?? 0, running, threadStatus: status,
		} });
	});
	await page.goto('/#threads');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const threadButton = page.getByRole('button', { name: title, exact: true });
	if (!(await threadButton.isVisible())) {
		await page.locator('.thread-workspace-head').filter({ hasText: project.name }).click();
	}
	await threadButton.click();
	return page.getByRole('complementary', { name: 'Execution console' });
}

test('Threads: decision options show readable labels while sending the raw code', async ({ page }) => {
	await openMockThread(page, {
		id: 'thread-hardening-decision', title: 'Hardening decision labels', status: 'waiting_decision',
		decisions: [{
			id: 'decision-functionality', title: 'Existing functionality detected',
			prompt: 'Existing functionality detected: Workspace filters.',
			options: ['continue_existing', 'improve_existing', 'performance_pass', 'create_new_anyway'],
		}],
	});
	const decision = page.locator('.thread-decision-console').first();
	await expect(decision.getByRole('button', { name: 'Continue the existing work', exact: true })).toBeVisible();
	await expect(decision.getByRole('button', { name: 'Create a new one anyway', exact: true })).toBeVisible();
	await expect(decision.getByRole('button', { name: 'continue_existing', exact: true })).toHaveCount(0);
	await expect(decision.getByText('Ignore the match and start separate work.')).toBeVisible();
});

test('Threads: intake questions render from their i18n key with readable options', async ({ page }) => {
	const question = 'What outcome should AIDO optimize for: diagnosis, implementation, or research?';
	const questionEs = '¿Qué resultado debe priorizar AIDO: diagnóstico, implementación o investigación?';
	await openMockThread(page, {
		id: 'thread-hardening-intake', title: 'Hardening intake question', status: 'waiting_decision',
		decisions: [{
			id: 'decision-intake', title: 'Decision needed (ask)', prompt: question,
			options: ['Diagnosis', 'Implementation', 'Research'],
			metadata: { questions: [question], questionKeys: ['app.threads.intake.question.outcome'] },
		}],
	});
	const decision = page.locator('.thread-decision-console').first();
	await expect(decision.getByText(question)).toBeVisible();
	await expect(decision.getByRole('button', { name: 'Diagnose', exact: true })).toBeVisible();
	await expect(decision.getByRole('button', { name: 'Implement', exact: true })).toBeVisible();

	// Switch to Spanish: the prompt itself keeps the English string sent by the backend, so
	// only a real key-driven translation makes the Spanish text appear here.
	await page.getByRole('button', { name: 'ES', exact: true }).click();
	await expect(page.locator('html')).toHaveAttribute('lang', 'es');
	await expect(decision.getByText(questionEs)).toBeVisible();
	await expect(decision.getByText(question)).toHaveCount(0);
	await expect(decision.getByRole('button', { name: 'Diagnosticar', exact: true })).toBeVisible();
	await expect(decision.getByRole('button', { name: 'Implementar', exact: true })).toBeVisible();
});

test('Threads: an intake research blocked by the search provider explains the real cause', async ({ page }) => {
	const execution = await openMockThread(page, {
		id: 'thread-hardening-provider', title: 'Hardening research provider', status: 'blocked',
		events: [
			{ sequence: 1, type: 'research_running' },
			{ sequence: 2, type: 'blocked', payload: { stage: 'research', reason: 'The web search provider blocked the query (searxng: HTTP 403).' } },
		],
		remediations: [{
			id: 'remediation-research-settings', stage: 'research', blockerType: 'research_required',
			actionType: 'open_settings_section',
			payload: { section: 'research', status: 'research_blocked', researchRemediation: 'research_provider_blocked' },
		}],
	});
	await expect(execution.getByText('The search provider blocked the query', { exact: true })).toBeVisible();
	await expect(execution.getByText(/validated technical decision/)).toHaveCount(0);
});

test('Threads: a failed intake research does not talk about technical decisions', async ({ page }) => {
	const execution = await openMockThread(page, {
		id: 'thread-hardening-research', title: 'Hardening research failure', status: 'blocked',
		events: [
			{ sequence: 1, type: 'research_running' },
			{ sequence: 2, type: 'blocked', payload: { stage: 'research', reason: 'ResearchAgent web search returned no sources.' } },
		],
		remediations: [{
			id: 'remediation-research-worker', stage: 'research', blockerType: 'research_required',
			actionType: 'run_worker_once', payload: { status: 'research_blocked' },
		}],
	});
	await expect(execution.getByText('Research could not answer', { exact: true })).toBeVisible();
	await expect(execution.getByText(/validated technical decision/)).toHaveCount(0);
});

test('Threads: a queued run waiting for machine capacity shows the readable reason', async ({ page }) => {
	await page.route('**/api/v1/workers/status', (route) => route.fulfill({ json: {
		status: 'running', running: true, paused: false, autostart: true, reason: '',
		maxConcurrentJobs: 1, pollIntervalSeconds: 1, inFlightJobs: 0, claimedJobs: 0,
		completedRuns: 0, failedRuns: 0,
	} }));
	const execution = await openMockThread(page, {
		id: 'thread-hardening-capacity', title: 'Hardening capacity wait', status: 'queued', running: true,
		events: [
			{ sequence: 1, type: 'run_queued' },
			{ sequence: 2, type: 'resource_wait', payload: {
				jobId: 'job-capacity', reasonCode: 'minimum_free_memory',
				reason: 'Available memory is below the configured workload admission threshold.',
			} },
		],
	});
	const banner = execution.getByRole('region', { name: 'Waiting for machine capacity' });
	await expect(banner.getByText('Waiting for machine capacity', { exact: true })).toBeVisible();
	await expect(execution.getByRole('region', { name: 'Waiting for worker' })).toHaveCount(0);
	await expect(banner).toContainText('This machine does not have enough free RAM right now');
});

test('Threads: without an active job the pipeline stops the last active step', async ({ page }) => {
	const execution = await openMockThread(page, {
		id: 'thread-hardening-stopped', title: 'Hardening stopped pipeline', status: 'open',
		events: [
			{ sequence: 1, type: 'runtime_check' },
			{ sequence: 2, type: 'git_check' },
			{ sequence: 3, type: 'branch_ready' },
		],
	});
	await expect(execution.locator('.thread-pipeline-step', { hasText: 'Branch ready' })).toHaveAttribute('data-state', 'stopped');
	await expect(execution.locator('.thread-pipeline-step[data-state="active"]')).toHaveCount(0);
});
