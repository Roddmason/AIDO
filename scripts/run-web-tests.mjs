import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import process from 'node:process';

const cleanupScript = fileURLToPath(new URL('./cleanup-playwright-webserver.mjs', import.meta.url));
const viteCli = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url));
const playwrightCli = fileURLToPath(new URL('../node_modules/@playwright/test/cli.js', import.meta.url));
const windowsPython = fileURLToPath(new URL('../.venv/Scripts/python.exe', import.meta.url));
const posixPython = fileURLToPath(new URL('../.venv/bin/python', import.meta.url));
const defaultDashboardPort = String(30000 + (process.pid % 20000));
const dashboardPort = process.env.PLAYWRIGHT_DASHBOARD_PORT || defaultDashboardPort;
const dbPath = process.env.PLAYWRIGHT_DB_PATH || `.tmp/playwright-control-center-${process.pid}.sqlite`;
const playwrightProjects = ['desktop', 'mobile'];
const testsPerChunk = Number.parseInt(process.env.PLAYWRIGHT_TESTS_PER_CHUNK || '4', 10);
const dashboardPortNumber = Number.parseInt(dashboardPort, 10);
const artifactRoot = process.env.PLAYWRIGHT_ARTIFACT_ROOT || `.tmp/playwright-artifacts-${process.pid}`;

function runNode(args, options = {}) {
	return spawnSync(process.execPath, args, {
		stdio: 'inherit',
		...options,
	});
}

function runNodeCaptured(args, options = {}) {
	return spawnSync(process.execPath, args, {
		encoding: 'utf8',
		...options,
	});
}

function dashboardServerCommand(port, sqlitePath) {
	const pythonPath = process.platform === 'win32' ? windowsPython : posixPython;
	const dashboardArgs = [
		'-m',
		'local_control_center',
		'--dashboard-only',
		'--dashboard-host',
		'127.0.0.1',
		'--dashboard-port',
		port,
		'--db-path',
		sqlitePath,
		'--workspace',
		'.',
	];
	if (existsSync(pythonPath)) {
		return { command: pythonPath, args: dashboardArgs };
	}
	return { command: 'uv', args: ['run', 'python', ...dashboardArgs] };
}

function cleanup(env = process.env) {
	return runNode([cleanupScript], { env }).status ?? 1;
}

function escapeRegExp(value) {
	return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function chunkTests(tests) {
	const chunkSize = Number.isFinite(testsPerChunk) && testsPerChunk > 0 ? testsPerChunk : 1;
	const chunks = [];
	for (let index = 0; index < tests.length; index += chunkSize) {
		chunks.push(tests.slice(index, index + chunkSize));
	}
	return chunks;
}

function safePathSegment(value) {
	return value.replace(/[^a-zA-Z0-9._-]+/g, '-');
}

function delay(milliseconds) {
	return new Promise((resolve) => {
		setTimeout(resolve, milliseconds);
	});
}

async function waitForExit(childProcess, timeoutMs) {
	if (childProcess.exitCode !== null || childProcess.signalCode !== null) {
		return true;
	}
	return await new Promise((resolve) => {
		const timeout = setTimeout(() => {
			childProcess.off('exit', onExit);
			resolve(false);
		}, timeoutMs);
		function onExit() {
			clearTimeout(timeout);
			resolve(true);
		}
		childProcess.once('exit', onExit);
	});
}

async function stopDashboardServer(childProcess) {
	if (childProcess.exitCode !== null || childProcess.signalCode !== null) {
		return;
	}
	childProcess.kill();
	if (await waitForExit(childProcess, 5000)) {
		return;
	}
	if (process.platform === 'win32' && childProcess.pid) {
		spawnSync(
			'powershell',
			[
				'-NoProfile',
				'-ExecutionPolicy',
				'Bypass',
				'-Command',
				`Stop-Process -Id ${childProcess.pid} -Force -ErrorAction SilentlyContinue`,
			],
			{ stdio: 'inherit' },
		);
		return;
	}
	if (childProcess.pid) {
		try {
			process.kill(childProcess.pid, 'SIGKILL');
		} catch {
			// The process may have exited between the graceful and forced stop.
		}
	}
}

async function waitForDashboardHealth(port, childProcess) {
	const deadline = Date.now() + 120_000;
	let lastReason = 'not_checked';
	const healthUrl = `http://127.0.0.1:${port}/healthz`;
	while (Date.now() < deadline) {
		if (childProcess.exitCode !== null || childProcess.signalCode !== null) {
			throw new Error(`dashboard server exited before healthcheck: code=${childProcess.exitCode} signal=${childProcess.signalCode}`);
		}
		try {
			const response = await fetch(healthUrl);
			if (response.ok) {
				return;
			}
			lastReason = `healthcheck_status_${response.status}`;
		} catch (error) {
			lastReason = error instanceof Error ? error.message : String(error);
		}
		await delay(500);
	}
	throw new Error(`dashboard healthcheck timed out for ${healthUrl}: ${lastReason}`);
}

function listProjectTests(project, env) {
	const result = runNodeCaptured([playwrightCli, 'test', `--project=${project}`, '--list'], {
		env: { ...env, PLAYWRIGHT_EXTERNAL_SERVER: '1' },
	});
	if ((result.status ?? 1) !== 0) {
		process.stdout.write(result.stdout || '');
		process.stderr.write(result.stderr || '');
		return null;
	}
	const output = `${result.stdout || ''}\n${result.stderr || ''}`;
	const marker = `[${project}] › `;
	const tests = [];
	for (const line of output.split(/\r?\n/)) {
		if (!line.includes(marker)) continue;
		const title = line.split(' › ').pop()?.trim();
		if (title) tests.push(title);
	}
	return Array.from(new Set(tests));
}

async function runPlaywrightChunk(project, chunk, chunkEnv, chunkOutput) {
	const dashboard = dashboardServerCommand(chunkEnv.PLAYWRIGHT_DASHBOARD_PORT, chunkEnv.PLAYWRIGHT_DB_PATH);
	const dashboardProcess = spawn(dashboard.command, dashboard.args, {
		env: chunkEnv,
		stdio: 'inherit',
	});
	let dashboardExitedEarly = false;
	dashboardProcess.once('exit', () => {
		dashboardExitedEarly = true;
	});
	try {
		await waitForDashboardHealth(chunkEnv.PLAYWRIGHT_DASHBOARD_PORT, dashboardProcess);
		const grep = `(?:${chunk.map(escapeRegExp).join('|')})`;
		const result = runNode([playwrightCli, 'test', `--project=${project}`, '--reporter=line', '--output', chunkOutput, '--grep', grep], {
			env: { ...chunkEnv, PLAYWRIGHT_EXTERNAL_SERVER: '1' },
		}).status ?? 1;
		if (result === 0 && dashboardExitedEarly) {
			console.error('Dashboard server exited before Playwright chunk cleanup.');
			return 1;
		}
		return result;
	} catch (error) {
		console.error(error instanceof Error ? error.message : String(error));
		return 1;
	} finally {
		await stopDashboardServer(dashboardProcess);
	}
}

let status = cleanup();

if (status === 0) {
	status = runNode([
		viteCli,
		'build',
		'--config',
		'local-control-center/web/vite.config.ts',
	]).status ?? 1;
}

if (status === 0) {
	for (let projectIndex = 0; projectIndex < playwrightProjects.length; projectIndex += 1) {
		const project = playwrightProjects[projectIndex];
		const projectDbPath = process.env.PLAYWRIGHT_DB_PATH || dbPath.replace(/\.sqlite$/, `-${project}.sqlite`);
		const projectEnv = {
			...process.env,
			PLAYWRIGHT_DASHBOARD_PORT: dashboardPort,
			PLAYWRIGHT_DB_PATH: projectDbPath,
		};
		const tests = listProjectTests(project, projectEnv);
		if (!tests || tests.length === 0) {
			status = 1;
			break;
		}
		const chunks = chunkTests(tests);
		for (let chunkIndex = 0; chunkIndex < chunks.length; chunkIndex += 1) {
			const chunkPort = process.env.PLAYWRIGHT_DASHBOARD_PORT
				|| (Number.isFinite(dashboardPortNumber) ? String(dashboardPortNumber + projectIndex * 200 + chunkIndex + 1) : dashboardPort);
			const chunkEnv = {
				...projectEnv,
				PLAYWRIGHT_DASHBOARD_PORT: chunkPort,
				PLAYWRIGHT_DB_PATH: process.env.PLAYWRIGHT_DB_PATH || projectDbPath.replace(/\.sqlite$/, `-chunk${chunkIndex + 1}.sqlite`),
			};
			const chunkOutput = path.join(artifactRoot, safePathSegment(project), `chunk-${chunkIndex + 1}`);
			mkdirSync(chunkOutput, { recursive: true });
			status = cleanup(chunkEnv);
			if (status !== 0) break;
			const chunk = chunks[chunkIndex];
			status = await runPlaywrightChunk(project, chunk, chunkEnv, chunkOutput);
			const projectCleanupStatus = cleanup(chunkEnv);
			if (status === 0 && projectCleanupStatus !== 0) {
				status = projectCleanupStatus;
			}
			if (status !== 0) break;
		}
		if (status !== 0) break;
	}
}

const cleanupStatus = cleanup();
if (status === 0 && cleanupStatus !== 0) {
	status = cleanupStatus;
}

process.exit(status);
