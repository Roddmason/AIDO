import { existsSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import process from 'node:process';

const cleanupScript = fileURLToPath(new URL('./cleanup-playwright-webserver.mjs', import.meta.url));
const viteCli = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url));
const playwrightCli = fileURLToPath(new URL('../node_modules/@playwright/test/cli.js', import.meta.url));
const dashboardPort = process.env.PLAYWRIGHT_DASHBOARD_PORT || '4321';
const dbPath = process.env.PLAYWRIGHT_DB_PATH || `.tmp/playwright-control-center-${process.pid}.sqlite`;
const baseUrl = `http://127.0.0.1:${dashboardPort}`;

function run(command, args, options = {}) {
	if (process.platform === 'win32') {
		const commandLine = [command, ...args.map((part) => `"${String(part).replaceAll('"', '\\"')}"`)].join(' ');
		return spawnSync(commandLine, {
			stdio: 'inherit',
			shell: true,
			...options,
		});
	}
	return spawnSync(command, args, {
		stdio: 'inherit',
		...options,
	});
}

function runNode(args, options = {}) {
	return spawnSync(process.execPath, args, {
		stdio: 'inherit',
		...options,
	});
}

function cleanup() {
	return runNode([cleanupScript]).status ?? 1;
}

function pythonCommand() {
	const venvPython = process.platform === 'win32' ? '.\\.venv\\Scripts\\python.exe' : './.venv/bin/python';
	if (existsSync(venvPython)) {
		return { command: venvPython, argsPrefix: [], shell: false };
	}
	return { command: 'uv', argsPrefix: ['run', 'python'], shell: process.platform === 'win32' };
}

function startServer() {
	const python = pythonCommand();
	return spawn(
		python.command,
		[
			...python.argsPrefix,
			'-m',
			'local_control_center',
			'--dashboard-only',
			'--dashboard-host',
			'127.0.0.1',
			'--dashboard-port',
			dashboardPort,
			'--db-path',
			dbPath,
			'--workspace',
			'.',
		],
		{
			stdio: 'inherit',
			shell: python.shell,
			windowsHide: true,
		},
	);
}

async function waitForReady(server) {
	const deadline = Date.now() + 120_000;
	let lastError = '';
	while (Date.now() < deadline) {
		if (server.exitCode !== null) {
			throw new Error(`Playwright backend exited before readiness with code ${server.exitCode}.`);
		}
		try {
			const health = await fetch(`${baseUrl}/healthz`);
			const handshake = await fetch(`${baseUrl}/api/v1/security/handshake`);
			if (health.ok && handshake.ok) {
				return;
			}
			lastError = `health=${health.status} handshake=${handshake.status}`;
		} catch (error) {
			lastError = error instanceof Error ? error.message : String(error);
		}
		await new Promise((resolve) => setTimeout(resolve, 500));
	}
	throw new Error(`Timed out waiting for Playwright backend readiness: ${lastError}`);
}

let status = cleanup();
let server = null;

if (status === 0) {
	status = runNode([
		viteCli,
		'build',
		'--config',
		'local-control-center/web/vite.config.ts',
	]).status ?? 1;
}

if (status === 0) {
	try {
		server = startServer();
		await waitForReady(server);
		status = runNode([playwrightCli, 'test'], {
			env: {
				...process.env,
				PLAYWRIGHT_DASHBOARD_PORT: dashboardPort,
				PLAYWRIGHT_DB_PATH: dbPath,
				PLAYWRIGHT_EXTERNAL_SERVER: '1',
			},
		}).status ?? 1;
	} catch (error) {
		console.error(error instanceof Error ? error.message : error);
		status = 1;
	}
}

server?.kill();
const cleanupStatus = cleanup();
if (status === 0 && cleanupStatus !== 0) {
	status = cleanupStatus;
}

process.exit(status);
