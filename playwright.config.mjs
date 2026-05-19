import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
	testDir: './tests_web',
	timeout: 60_000,
	expect: { timeout: 10_000 },
	fullyParallel: false,
	reporter: [['list']],
	use: {
		baseURL: 'http://127.0.0.1:4321',
		trace: 'on-first-retry',
	},
	webServer: {
		command: 'uv run python -m local_control_center --dashboard-only --dashboard-host 127.0.0.1 --dashboard-port 4321 --db-path .tmp/playwright-control-center.sqlite --workspace .',
		url: 'http://127.0.0.1:4321/healthz',
		timeout: 120_000,
		reuseExistingServer: !process.env.CI,
	},
	projects: [
		{ name: 'desktop', use: { ...devices['Desktop Chrome'] } },
		{ name: 'mobile', use: { ...devices['Pixel 5'], viewport: { width: 375, height: 812 } } },
	],
});
