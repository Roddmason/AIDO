import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import process from 'node:process';

const viteCli = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url));
const playwrightCli = fileURLToPath(new URL('../node_modules/@playwright/test/cli.js', import.meta.url));
const windowsPython = fileURLToPath(new URL('../.venv/Scripts/python.exe', import.meta.url));
const posixPython = fileURLToPath(new URL('../.venv/bin/python', import.meta.url));
// Low, non-ephemeral base: the 30000-49999 range can land on Windows reserved/ephemeral ports
// (winerror 10013). 8600-9499 is safe, and per-chunk increments below stay well under 10000.
const defaultDashboardPort = String(8600 + (process.pid % 900));
const dashboardPort = process.env.PLAYWRIGHT_DASHBOARD_PORT || defaultDashboardPort;
const dbPath = process.env.PLAYWRIGHT_DB_PATH || `.tmp/playwright-control-center-${process.pid}.sqlite`;
const playwrightProjects = ['desktop', 'mobile'];
const testsPerChunk = Number.parseInt(process.env.PLAYWRIGHT_TESTS_PER_CHUNK || '4', 10);
const dashboardPortNumber = Number.parseInt(dashboardPort, 10);
const artifactRoot = process.env.PLAYWRIGHT_ARTIFACT_ROOT || `.tmp/playwright-artifacts-${process.pid}`;
const argumentsList = process.argv.slice(2);
const grepIndex = argumentsList.indexOf('--grep');
const selectedGrep = grepIndex < 0 ? null : argumentsList.splice(grepIndex, 2)[1];
if (grepIndex >= 0 && !selectedGrep) throw new Error('--grep requires a test title pattern');
const selectedTestFiles = argumentsList;
const privateServerTests = new Set();
for (const file of selectedTestFiles) {
	const relative = path.relative(path.resolve('tests_web'), path.resolve(file));
	if (relative.startsWith('..') || path.isAbsolute(relative) || !existsSync(file) || !file.endsWith('.spec.js')) {
		throw new Error(`Focused web tests must be existing files under tests_web: ${file}`);
	}
}

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
	// A concurrent `vite build` empties dist/web mid-run and every chunk starts 500ing on GET /;
	// pointing the dashboard at a frozen snapshot of the build isolates the run from that race.
	if (process.env.PLAYWRIGHT_STATIC_DIR) {
		dashboardArgs.push('--static-dir', process.env.PLAYWRIGHT_STATIC_DIR);
	}
	if (existsSync(pythonPath)) {
		return { command: pythonPath, args: dashboardArgs };
	}
	return { command: 'uv', args: ['run', 'python', ...dashboardArgs] };
}

function escapeRegExp(value) {
	return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function chunkTests(tests) {
	const chunkSize = Number.isFinite(testsPerChunk) && testsPerChunk > 0 ? testsPerChunk : 1;
	const chunks = [];
	let pending = [];
	for (const title of tests) {
		if (privateServerTests.has(title)) {
			if (pending.length) chunks.push(pending);
			pending = [];
			chunks.push([title]);
		} else {
			pending.push(title);
			if (pending.length === chunkSize) { chunks.push(pending); pending = []; }
		}
	}
	if (pending.length) chunks.push(pending);
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
		throw new Error('Dashboard root exited before owned-tree cleanup; outer supervisor cleanup is required.');
	}
	if (!Number.isFinite(childProcess.aidoCreatedAt)) {
		throw new Error('Dashboard identity is unverified; outer supervisor cleanup is required.');
	}
	// The Windows venv PID is a redirector: snapshot and stop its descendants before losing it.
	const result = processTreeCommand('stop', childProcess.pid, childProcess.aidoCreatedAt);
	if (result.remainingCount !== 0 || !(await waitForExit(childProcess, 5000))) {
		throw new Error('Owned dashboard tree did not exit; supervisor cleanup is required.');
	}
}

function processTreeCommand(action, pid, createdAt) {
	const python = process.platform === 'win32' ? windowsPython : posixPython;
	const args = ['-m', 'local_control_center.quality.owned_tree', action, '--pid', String(pid), '--parent-pid', String(process.pid)];
	if (createdAt !== undefined) args.push('--created-at', String(createdAt));
	const result = spawnSync(python, args, { encoding: 'utf8', timeout: 20000 });
	if (result.status !== 0) throw new Error(`Owned-tree ${action} failed: ${result.stderr || result.error || result.status}`);
	return JSON.parse(result.stdout);
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
	const result = runNodeCaptured([playwrightCli, 'test', ...selectedTestFiles, `--project=${project}`, '--list', ...(selectedGrep ? ['--grep', selectedGrep] : [])], {
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
		if (title) {
			tests.push(title);
			if (line.includes('thread-lifecycle-e2e.spec.js:')) privateServerTests.add(title);
		}
	}
	return Array.from(new Set(tests));
}

async function runPlaywrightChunk(project, chunk, chunkEnv, chunkOutput) {
	if (chunk.length === 1 && privateServerTests.has(chunk[0])) {
		// This journey owns a separate API and worker. Do not allocate an unused second API.
		return executePlaywrightChunk(project, chunk, chunkEnv, chunkOutput);
	}
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
		dashboardProcess.aidoCreatedAt = processTreeCommand('identify', dashboardProcess.pid).createdAt;
		await waitForDashboardHealth(chunkEnv.PLAYWRIGHT_DASHBOARD_PORT, dashboardProcess);
		const result = executePlaywrightChunk(project, chunk, chunkEnv, chunkOutput);
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

function executePlaywrightChunk(project, chunk, chunkEnv, chunkOutput) {
	const grep = `(?:${chunk.map(escapeRegExp).join('|')})`;
	return runNode([playwrightCli, 'test', ...selectedTestFiles, `--project=${project}`, '--reporter=line', '--output', chunkOutput, '--grep', grep], {
		env: { ...chunkEnv, PLAYWRIGHT_EXTERNAL_SERVER: '1' },
	}).status ?? 1;
}

let status = 0;

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
			// Always increment per chunk from the (env or default) base. Reusing one fixed port
			// across sequential chunks can hit a TIME_WAIT bind collision (abnormal exit); a unique
			// port per chunk avoids both that and the reserved-range failure.
			const chunkPort = Number.isFinite(dashboardPortNumber)
				? String(dashboardPortNumber + projectIndex * 200 + chunkIndex + 1)
				: dashboardPort;
			const chunkEnv = {
				...projectEnv,
				PLAYWRIGHT_DASHBOARD_PORT: chunkPort,
				PLAYWRIGHT_DB_PATH: process.env.PLAYWRIGHT_DB_PATH || projectDbPath.replace(/\.sqlite$/, `-chunk${chunkIndex + 1}.sqlite`),
			};
			const chunkOutput = path.join(artifactRoot, safePathSegment(project), `chunk-${chunkIndex + 1}`);
			mkdirSync(chunkOutput, { recursive: true });
			const chunk = chunks[chunkIndex];
			status = await runPlaywrightChunk(project, chunk, chunkEnv, chunkOutput);
			if (status !== 0) break;
		}
		if (status !== 0) break;
	}
}

process.exit(status);
