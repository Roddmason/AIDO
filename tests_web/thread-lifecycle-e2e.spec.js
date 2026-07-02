import { spawn, spawnSync } from 'node:child_process';
import {
	existsSync,
	mkdirSync,
	mkdtempSync,
	readdirSync,
	rmSync,
	statSync,
	writeFileSync,
} from 'node:fs';
import { createServer } from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, request as playwrightRequest, test } from '@playwright/test';

/**
 * Full-lifecycle journey against a REAL dashboard server, a REAL temporary git project and a
 * controlled (but real) Ollama-protocol runtime. It exists to keep the GUI honest: every pipeline
 * surface asserted here (queued banner, worker claim, diff, QA/gitleaks, approval card, archive,
 * rename, similarity recall) must be backed by real control-plane state, never by decoration.
 *
 * The spec owns its own dashboard process (fresh SQLite, isolated workspace root) because the
 * controlled runtime needs `AIDO_OLLAMA_BASE_URL` injected into the server environment before it
 * boots; the shared per-chunk Playwright server stays untouched.
 */

const repoRoot = fileURLToPath(new URL('..', import.meta.url));
const PROJECT_NAME = 'Lifecycle E2E Project';
const GOAL = 'Add a generated lifecycle note file under src for the AIDO E2E flow';
const RENAMED_TITLE = 'Lifecycle E2E renamed thread';
const GENERATED_FILE = 'src/aido-lifecycle-note.txt';
const GENERATED_CONTENT = 'AIDO lifecycle E2E generated this file through the controlled runtime.\n';

// 10300-10799 stays clear of run-web-tests.mjs chunk servers (8600 + pid%900 + offsets < 10000),
// so this spec's private dashboard never races the shared per-chunk one.
const dashboardPort = 10300 + (process.pid % 500);
const baseUrl = `http://127.0.0.1:${dashboardPort}`;

let scratchDir = '';
let projectRepoDir = '';
let mockServer = null;
let mockBaseUrl = '';
let dashboardProcess = null;
const dashboardLog = [];
const mockCalls = { productOwner: 0, developer: 0, unknown: 0 };

/** Windows can hold worktree handles past afterAll; sweep leftovers from earlier runs instead. */
function sweepStaleScratchDirs() {
	const tempRoot = os.tmpdir();
	const staleBefore = Date.now() - 6 * 60 * 60 * 1000;
	let entries = [];
	try {
		entries = readdirSync(tempRoot);
	} catch {
		return;
	}
	for (const entry of entries) {
		if (!entry.startsWith('aido-lifecycle-')) continue;
		const stalePath = path.join(tempRoot, entry);
		try {
			if (statSync(stalePath).mtimeMs < staleBefore) {
				rmSync(stalePath, { recursive: true, force: true });
			}
		} catch {
			// A concurrent run or a lingering handle owns it; the next sweep retries.
		}
	}
}

function runGit(args, cwd) {
	const result = spawnSync('git', args, { cwd, encoding: 'utf8' });
	if (result.status !== 0) {
		throw new Error(`git ${args.join(' ')} failed: ${result.stderr || result.stdout}`);
	}
	return result;
}

/** Real ProductOwnerAgent contract output: backlog_ready with no blocking questions/decisions. */
function productOwnerOutput() {
	return {
		status: 'backlog_ready',
		summary: 'Backlog for the lifecycle E2E goal is ready for implementation.',
		confidence: 'high',
		completeness: { score: 95, missing: [], rationale: 'The goal is small and fully specified.' },
		questions: [],
		assumptions: [
			{
				statement: 'The repository accepts generated documentation files under src.',
				confidence: 'high',
				validation: 'Confirmed by the project layout.',
			},
		],
		decisions: [],
		productBriefPatch: {
			title: 'Lifecycle E2E generated note',
			summary: 'Produce a generated note file through the real Product Loop.',
			problemStatement: 'The pipeline needs a real artifact to prove it runs end to end.',
			goals: ['Generate the note file through the DeveloperAgent'],
			targetUsers: ['AIDO operators'],
			successMetrics: ['note_file_created'],
			scope: 'One generated file under src.',
			outOfScope: 'Any other repository change.',
		},
		epics: [{ title: 'Lifecycle note generation', description: 'Generate the E2E note file.' }],
		userStories: [
			{
				epicTitle: 'Lifecycle note generation',
				title: 'Generate the lifecycle note file',
				asA: 'AIDO operator',
				iWant: 'the Product Loop to generate the note file',
				soThat: 'I can verify the pipeline produces real changes',
				businessValue: 'high',
				acceptanceCriteria: ['The generated note file appears in the workspace diff'],
			},
		],
		risks: [
			{
				severity: 'low',
				description: 'The generated file could collide with existing content.',
				mitigation: 'Use a dedicated file name.',
			},
		],
		recommendedNextAction: 'Implement the backlog through the DeveloperAgent runtime.',
	};
}

/** Real DeveloperAgent contract output: strict JSON files patch applied via workspace_patch. */
function developerOutput() {
	return {
		summary: 'Generated the lifecycle note file.',
		files: [{ path: GENERATED_FILE, content: GENERATED_CONTENT }],
		tests: [],
		risks: [],
	};
}

/**
 * Controlled Ollama-protocol server: /api/tags advertises a model, /api/chat answers per agent.
 * Agent routing keys off the literal agent names inside the system prompts built by
 * `local_control_center/agents/product_owner_agent.py` (`_system_instruction`) and
 * `local_control_center/agents/developer_agent.py` (`_developer_model_messages`); renaming an
 * agent there must update this router, otherwise the mock answers 500 and the run fails loudly.
 */
function startMockOllamaServer() {
	return new Promise((resolve) => {
		const server = createServer((incoming, response) => {
			if (incoming.method === 'GET' && incoming.url === '/api/tags') {
				const body = JSON.stringify({ models: [{ name: 'controlled-model' }] });
				response.writeHead(200, { 'Content-Type': 'application/json' });
				response.end(body);
				return;
			}
			if (incoming.method === 'POST' && incoming.url === '/api/chat') {
				let raw = '';
				incoming.on('data', (chunk) => {
					raw += chunk;
				});
				incoming.on('end', () => {
					let payload;
					const parsed = JSON.parse(raw || '{}');
					const system = (parsed.messages ?? [])
						.filter((message) => message.role === 'system')
						.map((message) => String(message.content ?? ''))
						.join('\n');
					if (system.includes('ProductOwnerAgent')) {
						mockCalls.productOwner += 1;
						payload = productOwnerOutput();
					} else if (system.includes('DeveloperAgent')) {
						mockCalls.developer += 1;
						payload = developerOutput();
					} else {
						mockCalls.unknown += 1;
						response.writeHead(500, { 'Content-Type': 'application/json' });
						response.end(JSON.stringify({ error: `unknown agent prompt: ${system.slice(0, 120)}` }));
						return;
					}
					const body = JSON.stringify({
						message: { content: JSON.stringify(payload) },
						prompt_eval_count: 1,
						eval_count: 1,
					});
					response.writeHead(200, { 'Content-Type': 'application/json' });
					response.end(body);
				});
				return;
			}
			response.writeHead(404);
			response.end();
		});
		server.listen(0, '127.0.0.1', () => {
			const address = server.address();
			resolve({ server, url: `http://127.0.0.1:${address.port}` });
		});
	});
}

function dashboardCommand() {
	const windowsPython = path.join(repoRoot, '.venv', 'Scripts', 'python.exe');
	const posixPython = path.join(repoRoot, '.venv', 'bin', 'python');
	const pythonPath = process.platform === 'win32' ? windowsPython : posixPython;
	const args = [
		'-m',
		'local_control_center',
		'--dashboard-only',
		'--dashboard-host',
		'127.0.0.1',
		'--dashboard-port',
		String(dashboardPort),
		'--db-path',
		path.join(scratchDir, 'platform.sqlite'),
		'--workspace',
		scratchDir,
	];
	// Same isolation as run-web-tests.mjs: a concurrent `vite build` empties dist/web mid-run,
	// so serve the frozen snapshot when the runner provides one.
	if (process.env.PLAYWRIGHT_STATIC_DIR) {
		args.push('--static-dir', process.env.PLAYWRIGHT_STATIC_DIR);
	}
	if (existsSync(pythonPath)) {
		return { command: pythonPath, args };
	}
	return { command: 'uv', args: ['run', 'python', ...args] };
}

async function waitForDashboardHealth() {
	const deadline = Date.now() + 120_000;
	let lastReason = 'not_checked';
	while (Date.now() < deadline) {
		if (dashboardProcess.exitCode !== null) {
			throw new Error(
				`lifecycle dashboard exited before healthcheck (code=${dashboardProcess.exitCode}):\n` +
					dashboardLog.slice(-40).join(''),
			);
		}
		try {
			const response = await fetch(`${baseUrl}/healthz`);
			if (response.ok) return;
			lastReason = `status_${response.status}`;
		} catch (error) {
			lastReason = error instanceof Error ? error.message : String(error);
		}
		await new Promise((resolveDelay) => {
			setTimeout(resolveDelay, 500);
		});
	}
	throw new Error(`lifecycle dashboard healthcheck timed out: ${lastReason}`);
}

async function stopDashboard() {
	if (!dashboardProcess || dashboardProcess.exitCode !== null) return;
	dashboardProcess.kill();
	const exited = await new Promise((resolveExit) => {
		const timeout = setTimeout(() => resolveExit(false), 5000);
		dashboardProcess.once('exit', () => {
			clearTimeout(timeout);
			resolveExit(true);
		});
	});
	if (!exited && process.platform === 'win32' && dashboardProcess.pid) {
		spawnSync('powershell', [
			'-NoProfile',
			'-Command',
			`Stop-Process -Id ${dashboardProcess.pid} -Force -ErrorAction SilentlyContinue`,
		]);
	}
}

/** Enables the seeded ollama provider account so the worker preflight sees an executable runtime. */
async function arrangeControlledRuntime() {
	const context = await playwrightRequest.newContext();
	try {
		const handshake = await context.get(`${baseUrl}/api/v1/security/handshake`);
		const { token } = await handshake.json();
		const headers = { 'X-Local-Control-Token': token, Origin: baseUrl };
		const patch = await context.patch(`${baseUrl}/api/v1/model-gateway/providers/ollama`, {
			headers,
			data: { enabled: true },
		});
		if (!patch.ok()) {
			throw new Error(`enabling the ollama provider failed: ${patch.status()} ${await patch.text()}`);
		}
		const status = await context.get(`${baseUrl}/api/v1/agents/developer/status`);
		const body = await status.json();
		const readiness = body.developerAgent ?? body;
		if (readiness.executable !== true || readiness.selectedRuntimeId !== 'ollama') {
			throw new Error(`controlled runtime is not executable: ${JSON.stringify(readiness)}`);
		}
	} finally {
		await context.dispose();
	}
}

async function expectControlPlaneLoaded(page) {
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({
		timeout: 30_000,
	});
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
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

test.describe('Threads lifecycle (real pipeline)', () => {
	test.skip(
		({ isMobile }) => isMobile,
		'The lifecycle journey owns a dedicated dashboard server and runs once, on desktop.',
	);

	test.beforeAll(async () => {
		sweepStaleScratchDirs();
		scratchDir = mkdtempSync(path.join(os.tmpdir(), 'aido-lifecycle-'));
		projectRepoDir = path.join(scratchDir, 'lifecycle-sample-project');
		mkdirSync(path.join(projectRepoDir, 'src'), { recursive: true });
		writeFileSync(path.join(projectRepoDir, 'src', 'app.txt'), 'initial\n', 'utf8');
		const init = spawnSync('git', ['init', '--initial-branch', 'main'], { cwd: projectRepoDir });
		if (init.status !== 0) {
			runGit(['init'], projectRepoDir);
			runGit(['checkout', '-b', 'main'], projectRepoDir);
		}
		runGit(['config', 'user.email', 'aido-lifecycle@example.invalid'], projectRepoDir);
		runGit(['config', 'user.name', 'AIDO Lifecycle E2E'], projectRepoDir);
		runGit(['add', '.'], projectRepoDir);
		runGit(['commit', '-m', 'Initial commit'], projectRepoDir);

		const mock = await startMockOllamaServer();
		mockServer = mock.server;
		mockBaseUrl = mock.url;

		const env = { ...process.env, AIDO_OLLAMA_BASE_URL: mockBaseUrl };
		delete env.OLLAMA_BASE_URL;
		delete env.OLLAMA_HOST;
		delete env.AIDO_ENABLE_CLI_RUNTIMES;
		const dashboard = dashboardCommand();
		dashboardProcess = spawn(dashboard.command, dashboard.args, { cwd: repoRoot, env });
		dashboardProcess.stdout.on('data', (chunk) => dashboardLog.push(String(chunk)));
		dashboardProcess.stderr.on('data', (chunk) => dashboardLog.push(String(chunk)));
		try {
			await waitForDashboardHealth();
			await arrangeControlledRuntime();
		} catch (error) {
			// afterAll never runs when beforeAll throws; stop the spawned server here so a failed
			// boot cannot leak an orphan python process holding the port.
			await stopDashboard();
			throw error;
		}
	});

	test.afterAll(async () => {
		await stopDashboard();
		if (mockServer) mockServer.close();
		if (scratchDir) {
			try {
				rmSync(scratchDir, { recursive: true, force: true, maxRetries: 5 });
			} catch {
				// Windows can keep worktree handles briefly; the OS temp dir cleans up leftovers.
			}
		}
	});

	test.afterEach(({}, testInfo) => {
		if (testInfo.status !== testInfo.expectedStatus) {
			console.log(`[lifecycle] mock calls: ${JSON.stringify(mockCalls)}`);
			console.log(`[lifecycle] dashboard log tail:\n${dashboardLog.slice(-40).join('')}`);
		}
	});

	test('a real Git project runs goal -> queued -> worker -> diff -> QA/gitleaks -> approval -> archive -> rename -> similarity', async ({
		page,
	}, testInfo) => {
		test.setTimeout(600_000);
		await page.setViewportSize({ width: 1280, height: 800 });
		const handshake = await page.request.get(`${baseUrl}/api/v1/security/handshake`);
		const { token } = await handshake.json();
		const apiHeaders = { 'X-Local-Control-Token': token, Origin: baseUrl };
		let project;
		let threadId = '';

		await test.step('1. abre un proyecto Git temporal desde el wizard de workspace', async () => {
			await page.goto(`${baseUrl}/#threads`);
			await expectControlPlaneLoaded(page);

			await page
				.locator('.shell-footer-item', { hasText: /Open folder|Abrir carpeta/ })
				.first()
				.click();
			await expect(
				page.getByRole('heading', { name: /New workspace|Nuevo espacio de trabajo/ }),
			).toBeVisible();
			await page.locator('#workspace-folder').fill(projectRepoDir);
			await page.getByRole('button', { name: /Detect project|Detectar proyecto/ }).click();
			await page.getByRole('button', { name: /^(Next|Siguiente)$/ }).click();

			const nameInput = page.locator('#workspace-project-name');
			await expect(nameInput).toBeVisible();
			await nameInput.fill(PROJECT_NAME);
			await page.getByRole('button', { name: /^(Next|Siguiente)$/ }).click();
			await page
				.getByRole('button', { name: /Open in workbench|Abrir en el workbench/ })
				.click();
			await expect(page.getByText(/Workspace ready|Workspace listo/).first()).toBeVisible({
				timeout: 20_000,
			});

			const projectsResponse = await page.request.get(`${baseUrl}/api/v1/projects`);
			const { projects } = await projectsResponse.json();
			project = projects.find((item) => item.name === PROJECT_NAME);
			expect(project, 'the wizard must create a real project for the temp git repo').toBeTruthy();
		});

		await test.step('2. crea un thread nuevo desde el workspace del proyecto', async () => {
			await page.goto(`${baseUrl}/#threads`);
			await expectControlPlaneLoaded(page);
			const head = page.locator('.thread-workspace-head', { hasText: PROJECT_NAME });
			await expect(head).toBeVisible({ timeout: 20_000 });
			if ((await head.getAttribute('aria-expanded')) !== 'true') {
				await head.click();
			}
			await page.locator('.shell-new-thread').click();
			await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();
		});

		await test.step('3. escribe la meta en el intake', async () => {
			await page.getByLabel('Message AIDO').fill(GOAL);
		});

		await test.step('4. la deteccion de similares no bloquea cuando no hay threads previos', async () => {
			// 500ms debounce + one similarity read; with an empty project the card must never render.
			await page.waitForTimeout(1500);
			await expect(page.locator('.thread-similarity-card')).toHaveCount(0);
		});

		await test.step('5. el thread pasa a queued con su evento en consola', async () => {
			await page.getByRole('button', { name: 'Create thread' }).click();
			await expect(page.getByText(GOAL).first()).toBeVisible({ timeout: 20_000 });
			await expect(page.getByText(/Run queued|Run encolado/).first()).toBeVisible({
				timeout: 20_000,
			});
			await expect(
				page.getByText(/waiting for a worker|esperando que un worker/).first(),
			).toBeVisible({ timeout: 20_000 });

			const listed = await page.request.get(
				`${baseUrl}/api/v1/threads?projectId=${project.id}`,
			);
			const { threads } = await listed.json();
			const created = threads.find((thread) => thread.title === GOAL.slice(0, 80));
			expect(created, 'the queued thread must exist in the control plane').toBeTruthy();
			expect(created.status).toBe('queued');
			threadId = created.id;
		});

		await test.step('6. el worker corre una pasada real desde el boton Run now', async () => {
			const runNow = page.getByRole('button', { name: /Run now|Ejecutar ahora/ });
			await expect(runNow).toBeVisible({ timeout: 20_000 });
			await runNow.click();

			// The run-once pass executes the whole Product Loop synchronously; wait on real state.
			const deadline = Date.now() + 240_000;
			let detail = null;
			let status = 'queued';
			while (Date.now() < deadline) {
				const response = await page.request.get(`${baseUrl}/api/v1/threads/${threadId}`);
				detail = await response.json();
				status = detail.thread.status;
				if (status !== 'queued' && status !== 'running') break;
				await page.waitForTimeout(2000);
			}
			const blocked = (detail?.events ?? []).filter((event) => event.type === 'blocked').pop();
			expect(
				status,
				`product loop must reach approval; blocked payload: ${JSON.stringify(blocked?.payload ?? {})}`,
			).toBe('awaiting_approval');
		});

		await test.step('7. el panel de ejecucion muestra worker_claimed', async () => {
			const log = page.locator('.thread-execution-log');
			await expect(
				log.locator('.thread-console-row[data-type="worker_claimed"]').first(),
			).toBeVisible({ timeout: 120_000 });
			await expect(page.getByText(/Worker assigned|Worker asignado/).first()).toBeVisible();
		});

		await test.step('8. el runtime controlado produjo un diff real en el worktree', async () => {
			expect(mockCalls.productOwner, 'ProductOwnerAgent must hit the controlled runtime').toBeGreaterThan(0);
			expect(mockCalls.developer, 'DeveloperAgent must hit the controlled runtime').toBeGreaterThan(0);
			expect(mockCalls.unknown).toBe(0);

			const loopResponse = await page.request.get(
				`${baseUrl}/api/v1/projects/${project.id}/product-loop`,
			);
			const loopBody = await loopResponse.json();
			const loops = loopBody.loops ?? loopBody.productLoops ?? [];
			const loop =
				loops.find((item) => item.state === 'awaiting_approval') ?? loops[loops.length - 1];
			expect(loop, 'an awaiting_approval product loop must exist').toBeTruthy();
			const durable = loop.context?.durableRun ?? {};
			expect(durable.review?.state).toBe('captured');
			expect(durable.review?.changedFiles).toEqual([GENERATED_FILE]);
			expect(String(durable.review?.patch ?? '')).toContain('controlled runtime');
			const generatedOnDisk = path.join(durable.workspacePath ?? '', GENERATED_FILE);
			expect(
				existsSync(generatedOnDisk),
				`the generated file must exist on disk at ${generatedOnDisk}`,
			).toBe(true);
			const executingStep = page.locator('.thread-pipeline-step', { hasText: /Executing/ });
			await expect(executingStep).toHaveAttribute('data-state', 'done');
		});

		await test.step('9. QA y gitleaks aparecen en el pipeline y la consola', async () => {
			const qaStep = page.locator('.thread-pipeline-step', { hasText: /QA checks/ });
			const securityStep = page.locator('.thread-pipeline-step', { hasText: /Security scan/ });
			await expect(qaStep).toHaveAttribute('data-state', 'done', { timeout: 30_000 });
			await expect(securityStep).toHaveAttribute('data-state', 'done');
			const log = page.locator('.thread-execution-log');
			await expect(log.locator('.thread-console-row[data-type="qa_running"]').first()).toBeVisible();
			await expect(
				log.locator('.thread-console-row[data-type="security_running"]').first(),
			).toBeVisible();

			const loopResponse = await page.request.get(
				`${baseUrl}/api/v1/projects/${project.id}/product-loop`,
			);
			const loopBody = await loopResponse.json();
			const loops = loopBody.loops ?? loopBody.productLoops ?? [];
			const loop = loops.find((item) => item.state === 'awaiting_approval');
			const gitleaks = loop?.context?.durableRun?.gitleaks ?? {};
			expect(gitleaks.status, 'gitleaks must really run against the workspace').toBe('completed');
			// A defaulted/stubbed result would not carry the scanner report: demand the real shape.
			expect(Array.isArray(gitleaks.gitleaks?.report), 'gitleaks report must be present').toBe(true);
			expect(gitleaks.gitleaks?.findingCount ?? 0).toBe(0);
			expect(Boolean(gitleaks.deliveryBlocked)).toBe(false);
		});

		await test.step('10. aparece la tarjeta de revision/aprobacion', async () => {
			await expect(
				page.locator('.thread-conversation-head .badge', { hasText: /awaiting approval/ }),
			).toBeVisible({ timeout: 30_000 });
			const approvalCard = page.locator('.thread-blocker-card', {
				hasText: /Approval required|Aprobación requerida/,
			});
			await expect(approvalCard).toBeVisible({ timeout: 30_000 });

			// Visual evidence artifact: the execution panel with pipeline, console and approval card.
			await testInfo.attach('execution-panel-awaiting-approval', {
				body: await page.screenshot({ fullPage: false }),
				contentType: 'image/png',
			});
			await approvalCard
				.getByRole('button', { name: /Open approvals|Abrir aprobaciones/ })
				.click();
			// "Open approvals" routes to the review board, where the pending delivery shows up
			// as a real review card fed by the action request the pipeline created.
			const board = page.locator('.review-board');
			await expect(board).toBeVisible({ timeout: 20_000 });
			await expect(page.getByText('product_loop.approve_delivery').first()).toBeVisible({
				timeout: 20_000,
			});

			await page.goto(`${baseUrl}/#threads`);
			await expectControlPlaneLoaded(page);
			const head = page.locator('.thread-workspace-head', { hasText: PROJECT_NAME });
			await expect(head).toBeVisible({ timeout: 20_000 });
			if ((await head.getAttribute('aria-expanded')) !== 'true') {
				await head.click();
			}
		});

		await test.step('11. archiva el thread desde el menu contextual', async () => {
			await openThreadMenu(page, GOAL.slice(0, 80));
			await page.getByRole('menuitem', { name: /Archive|Archivar/ }).click();
			await expect(page.getByText(/Thread archived|Hilo archivado/).first()).toBeVisible({
				timeout: 20_000,
			});
			await expect(threadRow(page, GOAL.slice(0, 80))).toBeHidden({ timeout: 20_000 });
		});

		await test.step('12. el toggle de archivados vuelve a mostrar el thread', async () => {
			await page.getByRole('button', { name: /Show archived|Mostrar archivados/ }).click();
			await expect(page.locator('.thread-archived-head').first()).toBeVisible({
				timeout: 20_000,
			});
			const archivedRow = threadRow(page, GOAL.slice(0, 80));
			await expect(archivedRow).toBeVisible({ timeout: 20_000 });
			await expect(archivedRow).toHaveClass(/is-archived/);
		});

		await test.step('13. desarchiva y renombra el thread', async () => {
			await openThreadMenu(page, GOAL.slice(0, 80));
			await page.getByRole('menuitem', { name: /Unarchive|Desarchivar/ }).click();
			await expect(page.getByText(/Thread restored|Hilo restaurado/).first()).toBeVisible({
				timeout: 20_000,
			});

			// Wait for the refreshed sidebar to move the row back to the active section before
			// touching its menu: the archived flavor of the menu has no Rename item.
			const activeRow = page.locator('.thread-row:not(.is-archived)', {
				hasText: GOAL.slice(0, 80),
			});
			await expect(activeRow).toBeVisible({ timeout: 20_000 });
			await page.getByRole('button', { name: /Hide archived|Ocultar archivados/ }).click();

			await activeRow.locator('.thread-menu-trigger').click();
			await expect(page.getByRole('menu')).toBeVisible();
			await page.getByRole('menuitem', { name: /Rename|Renombrar/ }).click();
			const editor = page.getByLabel(/New thread title|Nuevo título del hilo/);
			await expect(editor).toBeVisible();
			await editor.fill(RENAMED_TITLE);
			await editor.press('Enter');
			await expect(threadRow(page, RENAMED_TITLE)).toBeVisible({ timeout: 20_000 });

			const detailResponse = await page.request.get(`${baseUrl}/api/v1/threads/${threadId}`, {
				headers: apiHeaders,
			});
			const detail = await detailResponse.json();
			expect(detail.thread.title).toBe(RENAMED_TITLE);
		});

		await test.step('14. crear un thread similar muestra la sugerencia con el trabajo previo', async () => {
			await page.locator('.shell-new-thread').click();
			await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();
			await page.getByLabel('Message AIDO').fill(GOAL);

			const card = page.locator('.thread-similarity-card');
			await expect(card).toBeVisible({ timeout: 15_000 });
			await expect(card).toContainText('This was already worked on in');
			await expect(card).toContainText(RENAMED_TITLE);
			await expect(card.getByRole('button', { name: 'Continue existing thread' })).toBeVisible();
			await expect(card.getByRole('button', { name: 'Create new thread anyway' })).toBeVisible();
		});
	});
});
