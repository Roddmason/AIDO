import { spawnSync } from 'node:child_process';
import process from 'node:process';

const dashboardPort = process.env.PLAYWRIGHT_DASHBOARD_PORT || '4321';
const dbMarker = 'playwright-control-center-';

function cleanupWindows() {
	const script = `
$ErrorActionPreference = "Stop"
$dashboardPort = "${dashboardPort}"
$dbMarker = "${dbMarker}"
$processes = Get-CimInstance Win32_Process | Where-Object {
	$_.CommandLine -and
	$_.CommandLine -like "*--dashboard-port $dashboardPort*" -and
	$_.CommandLine -like "*$dbMarker*"
}
foreach ($process in $processes) {
	Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
`;
	return spawnSync(
		'powershell',
		['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
		{ stdio: 'inherit' },
	);
}

function cleanupPosix() {
	const ps = spawnSync('ps', ['-eo', 'pid=,command='], { encoding: 'utf8' });
	if (ps.status !== 0) {
		return ps;
	}
	for (const line of ps.stdout.split('\n')) {
		if (!line.includes(`--dashboard-port ${dashboardPort}`) || !line.includes(dbMarker)) {
			continue;
		}
		const pid = Number(line.trim().split(/\s+/, 1)[0]);
		if (Number.isFinite(pid) && pid > 0 && pid !== process.pid) {
			try {
				process.kill(pid, 'SIGTERM');
			} catch {
				// The process may have exited between ps and kill.
			}
		}
	}
	return { status: 0 };
}

const result = process.platform === 'win32' ? cleanupWindows() : cleanupPosix();
process.exit(result.status ?? 1);
