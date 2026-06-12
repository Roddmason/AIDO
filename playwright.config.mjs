import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'node:fs';

const dashboardPort = process.env.PLAYWRIGHT_DASHBOARD_PORT || '4321';
const playwrightDbPath = process.env.PLAYWRIGHT_DB_PATH || `.tmp/playwright-control-center-${process.pid}.sqlite`;
const venvPython = process.platform === 'win32' ? '.\\.venv\\Scripts\\python.exe' : './.venv/bin/python';
const pythonCommand = existsSync(venvPython) ? `"${venvPython}"` : 'uv run python';
const externalWebServer = process.env.PLAYWRIGHT_EXTERNAL_SERVER === '1';
const webServerConfig = externalWebServer
	? {}
	: {
			webServer: {
				command: `${pythonCommand} -m local_control_center --dashboard-only --dashboard-host 127.0.0.1 --dashboard-port ${dashboardPort} --db-path ${playwrightDbPath} --workspace .`,
				url: `http://127.0.0.1:${dashboardPort}/healthz`,
				timeout: 120_000,
				reuseExistingServer: false,
			},
		};

export default defineConfig({
	testDir: './tests_web',
	timeout: 60_000,
	expect: { timeout: 10_000 },
	fullyParallel: false,
	workers: 1,
	reporter: [['list']],
	use: {
		baseURL: `http://127.0.0.1:${dashboardPort}`,
		trace: 'on-first-retry',
	},
	...webServerConfig,
	projects: [
		{ name: 'desktop', use: { ...devices['Desktop Chrome'] } },
		{ name: 'mobile', use: { ...devices['Pixel 5'], viewport: { width: 375, height: 812 } } },
	],
});
