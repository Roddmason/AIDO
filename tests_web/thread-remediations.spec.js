/**
 * Actionable blocker remediations in the Thread Inspector.
 *
 * When a thread is blocked, `/threads/{id}/remediations` returns persisted repair actions that the
 * inspector renders as blocker cards pinned above the manager tabs. This spec drives the three
 * headline blocker types against a mocked remediations endpoint (so each blocker is deterministic)
 * and asserts the card exposes the right primary repair: a runtime block opens Providers & CLI, an
 * uninitialized Git repo offers "Initialize Git", and a stopped worker offers "Run now".
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

// The AI-manager inspector auto-opens only in the desktop IDE layout (min-width 981px); pin a
// desktop-width viewport so both Playwright projects exercise the inspector and its blocker cards.
test.use({ viewport: { width: 1280, height: 800 } });

// `injectBlockedEvent` awaits `route.fetch()`, so a background thread poll can still be in flight when
// Playwright closes the page at teardown. Drop the handlers first — from `afterEach`, so this also runs
// when an assertion fails early — and ignore whatever the interrupted handlers throw on the way out.
test.afterEach(async ({ page }) => {
	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

async function expectControlPlaneLoaded(page) {
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
}

/** Creates a real thread through the composer and waits for the live layout + inspector. */
async function createLiveThread(page, firstMessage) {
	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();
	await page.getByLabel('Message AIDO').fill(firstMessage);
	await page.getByRole('button', { name: 'Create thread' }).click();
	await expect(page.getByText(firstMessage).first()).toBeVisible({ timeout: 20_000 });
}

/** One persisted remediation record shaped like the backend contract. */
function remediation(overrides) {
	return {
		id: 'remediation-fixture',
		projectId: 'project-fixture',
		threadId: 'thread-fixture',
		loopId: 'loop-fixture',
		stage: 'runtime',
		blockerType: 'runtime_not_executable',
		title: 'Repair action',
		description: 'A persisted repair action.',
		actionType: 'validate_runtime',
		payload: {},
		status: 'pending',
		createdAt: '2026-07-03T00:00:00.000Z',
		resolvedAt: null,
		...overrides,
	};
}

/** Serves a fixed remediations list for whichever live thread the inspector queries. */
async function mockRemediations(page, remediations) {
	await page.route('**/api/v1/threads/*/remediations', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ remediations }),
		});
	});
}

/** The blocker card that carries the given title text, scoped to the inspector pane. */
function blockerCard(page, titlePattern) {
	return page
		.locator('.inspector-panel')
		.locator('.thread-remediation-card', { hasText: titlePattern });
}

/**
 * Appends a `blocked` event to whatever the thread endpoint really returns, so the execution panel
 * derives a blocked pipeline without a backend run. Its sequence outranks any milestone event.
 */
async function injectBlockedEvent(page, { stage, reason }) {
	await page.route('**/api/v1/threads/*', async (route) => {
		const response = await route.fetch();
		const detail = await response.json();
		if (!Array.isArray(detail.events)) {
			await route.fulfill({ response });
			return;
		}
		detail.events.push({
			id: 'event-blocked-fixture',
			threadId: detail.thread.id,
			sequence: 9999,
			type: 'blocked',
			agentRole: 'aido_lead',
			payload: { stage, reason },
			createdAt: '2026-07-09T00:00:00.000Z',
		});
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(detail),
		});
	});
}

test('Remediations: a blocked runtime card opens Providers & CLI', async ({ page }) => {
	await mockRemediations(page, [
		remediation({
			stage: 'runtime',
			blockerType: 'runtime_not_executable',
			actionType: 'validate_runtime',
			title: 'Validate runtime',
			description: 'Re-check local runtime availability.',
			payload: {
				reason: 'No executable runtime is currently available.',
				details: { selectedRuntimeId: 'codex-cli' },
			},
		}),
	]);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Runtime blocker remediation ${Date.now()}`);

	// The runtime blocker card renders with its plain-language title and the "Configure runtime"
	// primary that routes into the Providers & CLI settings section.
	const card = blockerCard(page, /No executable runtime|Sin runtime ejecutable/);
	await expect(card).toBeVisible({ timeout: 20_000 });
	const configure = card.getByRole('button', { name: /Configure runtime|Configurar runtime/ });
	await expect(configure).toBeVisible();

	await configure.click();

	// Clicking it opens the Settings dialog at Providers & CLI — the runtime's real recovery path.
	const settings = page.getByRole('dialog', { name: 'Settings' });
	await expect(settings).toBeVisible({ timeout: 10_000 });
	await expect(settings.getByText(/Providers & CLI/).first()).toBeVisible();
});

test('Remediations: git_not_initialized offers Initialize Git', async ({ page }) => {
	let executeCalled = false;
	let remediations = [
		remediation({
			id: 'remediation-git-init',
			stage: 'git',
			blockerType: 'git_not_initialized',
			actionType: 'git_init',
			title: 'Initialize Git repository',
			description: 'Create Git metadata in the project folder.',
			payload: { reason: 'Not a git repository.' },
		}),
	];
	await page.route('**/api/v1/threads/*/remediations', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ remediations }),
		});
	});
	await page.route('**/api/v1/remediations/remediation-git-init/execute', async (route) => {
		executeCalled = true;
		remediations = [];
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				remediation: { ...remediation({ id: 'remediation-git-init' }), status: 'resolved' },
				execution: {
					status: 'completed',
					action: 'git_init',
					reason: 'Git repository initialized.',
				},
			}),
		});
	});
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Git init blocker remediation ${Date.now()}`);

	const card = blockerCard(page, /Git is not initialized|Git no está inicializado/);
	await expect(card).toBeVisible({ timeout: 20_000 });
	await card.getByRole('button', { name: /Initialize Git|Inicializar Git/ }).click();
	await expect.poll(() => executeCalled).toBe(true);
	await expect(page.getByText(/Git repository initialized/)).toBeVisible();
	await expect(card).toBeHidden({ timeout: 20_000 });
});

test('Remediations: research network blocker offers Check network access', async ({ page }) => {
	let executeCalled = false;
	let remediations = [
		remediation({
			id: 'remediation-network-check',
			stage: 'research',
			blockerType: 'research_required',
			actionType: 'check_network_access',
			title: 'Check network access',
			description: 'Verify ResearchAgent can reach the web-search endpoint before retrying.',
			payload: {
				reason: 'ResearchAgent web search failed: urlopen timed out.',
				section: 'internet',
			},
			primary: true,
			destructive: false,
			confirmationRequired: false,
		}),
	];
	await page.route('**/api/v1/threads/*/remediations', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ remediations }),
		});
	});
	await page.route('**/api/v1/remediations/remediation-network-check/execute', async (route) => {
		executeCalled = true;
		remediations = [];
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				remediation: {
					...remediation({ id: 'remediation-network-check' }),
					actionType: 'check_network_access',
					status: 'resolved',
				},
				execution: {
					status: 'completed',
					action: 'check_network_access',
					reason: 'Research web-search endpoint is reachable.',
				},
			}),
		});
	});
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Network blocker remediation ${Date.now()}`);

	const card = blockerCard(page, /Research evidence required|Evidencia de investigación requerida/);
	await expect(card).toBeVisible({ timeout: 20_000 });
	await card.getByRole('button', { name: /Check network access|Verificar acceso/ }).click();
	await expect.poll(() => executeCalled).toBe(true);
	await expect(page.getByText(/Research web-search endpoint is reachable/)).toBeVisible();
	await expect(card).toBeHidden({ timeout: 20_000 });
});

test('Remediations: answer_question requires selecting one Product Loop option', async ({ page }) => {
	let executePayload = null;
	let remediations = [
		remediation({
			id: 'remediation-answer-question',
			stage: 'functionality_memory',
			blockerType: 'functionality_memory_decision_required',
			actionType: 'answer_question',
			title: 'Answer Product Loop question',
			description: 'Resolve the similar functionality decision.',
			payload: {
				reason: 'Existing functionality detected.',
				decisionId: 'decision-similar-thread',
				options: [
					'continue_existing',
					'improve_existing',
					'performance_pass',
					'create_new_anyway',
				],
			},
		}),
	];
	await page.route('**/api/v1/threads/*/remediations', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ remediations }),
		});
	});
	await page.route(
		'**/api/v1/remediations/remediation-answer-question/execute',
		async (route) => {
			executePayload = route.request().postDataJSON();
			remediations = [];
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					remediation: {
						...remediation({ id: 'remediation-answer-question' }),
						status: 'resolved',
					},
					execution: {
						status: 'completed',
						action: 'answer_question',
						reason: 'Decision resolved.',
					},
				}),
			});
		},
	);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Decision option remediation ${Date.now()}`);

	const card = blockerCard(
		page,
		/Similar functionality already exists|Funcionalidad similar ya existe/,
	);
	await expect(card).toBeVisible({ timeout: 20_000 });
	const answerButton = card.getByRole('button', { name: /Answer question|Responder pregunta/ });
	await expect(answerButton).toBeDisabled();

	await card.getByLabel(/Answer|Respuesta/).selectOption('improve_existing');
	await expect(answerButton).toBeEnabled();
	await answerButton.click();

	await expect.poll(() => executePayload).toEqual({ payload: { answer: 'improve_existing' } });
	await expect(page.getByText(/Decision resolved/)).toBeVisible();
	await expect(card).toBeHidden({ timeout: 20_000 });
});

test('Remediations: continue_plan_only executes the continue action from the blocker card', async ({
	page,
}) => {
	let executeCalled = false;
	let remediations = [
		remediation({
			id: 'remediation-continue-plan-only',
			stage: 'runtime',
			blockerType: 'runtime_output_invalid',
			actionType: 'continue_plan_only',
			title: 'Continue plan-only',
			description: 'Continue without executing code while runtime output is invalid.',
			payload: {
				reason: 'Runtime output did not validate.',
				loopId: 'loop-plan-only',
			},
		}),
	];
	await page.route('**/api/v1/threads/*/remediations', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ remediations }),
		});
	});
	await page.route(
		'**/api/v1/remediations/remediation-continue-plan-only/execute',
		async (route) => {
			executeCalled = true;
			remediations = [];
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					remediation: {
						...remediation({ id: 'remediation-continue-plan-only' }),
						status: 'resolved',
					},
					execution: {
						status: 'queued',
						action: 'continue_plan_only',
						reason: 'Plan-only continuation queued.',
					},
				}),
			});
		},
	);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Continue plan-only remediation ${Date.now()}`);

	const card = blockerCard(
		page,
		/Runtime returned invalid output|El runtime devolvió una salida inválida/,
	);
	await expect(card).toBeVisible({ timeout: 20_000 });
	await card.getByRole('button', { name: /Continue plan-only|Continuar solo planificación/ }).click();

	await expect.poll(() => executeCalled).toBe(true);
	await expect(page.getByText(/Plan-only continuation queued/)).toBeVisible();
	await expect(card).toBeHidden({ timeout: 20_000 });
});

test('Remediations: a ProductOwnerAgent block offers validate and switch runtime', async ({
	page,
}) => {
	const technicalReason = 'ProductOwnerAgent returned a brief that failed schema validation.';
	await mockRemediations(page, [
		remediation({
			id: 'remediation-po-validate',
			stage: 'product_owner',
			blockerType: 'product_owner_output_invalid',
			actionType: 'validate_runtime',
			title: 'Validate runtime',
			description: 'Re-check the runtime that ProductOwnerAgent used before retrying the loop.',
			technicalReason,
			primary: true,
			payload: { reason: technicalReason, runtimeId: 'ollama' },
		}),
		remediation({
			id: 'remediation-po-switch',
			stage: 'product_owner',
			blockerType: 'product_owner_output_invalid',
			actionType: 'switch_runtime',
			title: 'Switch runtime',
			description: 'Select a different executable runtime for ProductOwnerAgent.',
			technicalReason,
			payload: { reason: technicalReason, runtimeId: 'ollama' },
		}),
	]);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Product owner blocker remediation ${Date.now()}`);

	const card = blockerCard(
		page,
		/ProductOwnerAgent output is incomplete|La salida de ProductOwnerAgent está incompleta/,
	);
	await expect(card).toBeVisible({ timeout: 20_000 });

	// Both runtime repairs are offered: the backend's primary (revalidate) plus switching runtime.
	await expect(card.getByRole('button', { name: /Revalidate runtime|Revalidar runtime/ })).toBeVisible();
	await expect(card.getByRole('button', { name: /Switch to Ollama|Cambiar a Ollama/ })).toBeVisible();

	// The card explains itself: stage, machine cause, and what staying blocked costs.
	await expect(card.getByText(/product owner/i).first()).toBeVisible();
	await expect(card.getByText(technicalReason)).toBeVisible();
	await expect(
		card.getByText(
			/Without a validated brief or backlog|Sin un brief o backlog validado/,
		),
	).toBeVisible();
});

test('Remediations: a destructive action is confirmed before it executes', async ({ page }) => {
	let executePayload = null;
	let remediations = [
		remediation({
			id: 'remediation-checkout-branch',
			stage: 'git',
			blockerType: 'git_branch_missing',
			actionType: 'checkout_branch',
			title: 'Checkout branch',
			description: 'Switch to an existing branch before continuing.',
			technicalReason: 'Working branch required before execution.',
			primary: true,
			destructive: true,
			confirmationRequired: true,
			payload: { reason: 'Working branch required before execution.', branchName: 'dev' },
		}),
	];
	await page.route('**/api/v1/threads/*/remediations', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ remediations }),
		});
	});
	await page.route('**/api/v1/remediations/remediation-checkout-branch/execute', async (route) => {
		executePayload = route.request().postDataJSON();
		remediations = [];
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				remediation: { ...remediation({ id: 'remediation-checkout-branch' }), status: 'resolved' },
				execution: { status: 'completed', action: 'checkout_branch', reason: 'Checked out dev.' },
			}),
		});
	});
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Destructive blocker remediation ${Date.now()}`);

	const card = blockerCard(page, /Working branch required|Falta la rama de trabajo/);
	await expect(card).toBeVisible({ timeout: 20_000 });
	await card.getByRole('button', { name: /Switch branch|Cambiar de rama/ }).click();

	// The backend refuses a destructive action without an explicit confirmation, so the card asks.
	const confirm = page.getByRole('dialog', { name: /Confirm this action|Confirma esta acción/ });
	await expect(confirm).toBeVisible();
	expect(executePayload).toBeNull();

	await confirm.getByRole('button', { name: /Run anyway|Ejecutar de todos modos/ }).click();
	await expect.poll(() => executePayload).toEqual({ payload: { confirmed: true } });
	await expect(page.getByText(/Checked out dev/)).toBeVisible();
});

test('Remediations: a blocked run with no remediation falls back to settings and diagnostic', async ({
	page,
}) => {
	const reason = 'ProductLoop stopped: unmapped blocker from the runtime gateway.';
	await mockRemediations(page, []);
	await injectBlockedEvent(page, { stage: 'runtime', reason });
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Fallback blocker remediation ${Date.now()}`);

	// The execution panel owns the fallback: a blocked run never degrades to raw console text.
	const card = page
		.locator('.thread-execution-pane')
		.locator('.thread-remediation-card', {
			hasText: /Blocked without a repair plan|Bloqueado sin plan de reparación/,
		});
	await expect(card).toBeVisible({ timeout: 20_000 });
	await expect(card.getByText(reason)).toBeVisible();

	await expect(
		card.getByRole('button', { name: /Open configuration|Abrir configuración/ }),
	).toBeVisible();
	await expect(card.getByRole('button', { name: /Copy diagnostic|Copiar diagnóstico/ })).toBeVisible();
	// Nothing was persisted, so there is nothing to dismiss.
	await expect(card.getByRole('button', { name: /Dismiss|Descartar/ })).toHaveCount(0);

	await card.getByRole('button', { name: /Open configuration|Abrir configuración/ }).click();
	const settings = page.getByRole('dialog', { name: 'Settings' });
	await expect(settings).toBeVisible({ timeout: 10_000 });
});

test('Remediations: a stopped worker offers Run now', async ({ page }) => {
	await mockRemediations(page, [
		remediation({
			stage: 'worker',
			blockerType: 'worker_not_running',
			actionType: 'run_worker_once',
			title: 'Run worker once',
			description: 'This thread is queued, but the local worker is not running.',
			payload: { reason: 'Local worker runtime is not running.' },
		}),
	]);
	await page.goto('/#threads');
	await expectControlPlaneLoaded(page);
	await createLiveThread(page, `Worker stopped remediation ${Date.now()}`);

	const card = blockerCard(page, /Worker is not running|El worker no está corriendo/);
	await expect(card).toBeVisible({ timeout: 20_000 });
	await expect(card.getByRole('button', { name: /Run now|Ejecutar ahora/ })).toBeVisible();
});
