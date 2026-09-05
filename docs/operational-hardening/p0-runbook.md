# P0 operator runbook

Use the repository root. Keep real providers disabled unless you explicitly intend and approve a
real operation. Read [recovery](p0-recovery.md) before changing a database or cleaning up processes.

## Installation and preflight

Python is pinned in `.python-version` to 3.13.15. Verify the actual SQLite runtime before startup:

```powershell
uv sync --locked --all-extras
uv run python -c "import sys,sqlite3; from local_control_center.shared.db import require_safe_sqlite_runtime; require_safe_sqlite_runtime(); print(sys.version, sqlite3.sqlite_version)"
corepack pnpm@10.24.0 install --frozen-lockfile
```

Use the repository's Node 24.16.x and pnpm 10.24.0. An old `uv` may not contain download metadata for
the pinned interpreter. Install an official patched Python and pass its absolute executable with
`uv sync --locked --all-extras --python <absolute-python-path>`; verify SQLite again. Do not bypass
the SQLite guard or silently fall back to an affected interpreter. Official installation guidance:
[uv Python versions](https://docs.astral.sh/uv/concepts/python-versions/).

For this validation checkout only, the verified interpreter was installed under `.tmp/p0-python/`
from Astral's 20260901 standalone build. `.venv` references it: **do not delete that directory as
temporary evidence** while it is in use. The old environment is recoverably retained at
`.tmp/operational-hardening-p0/venv-before-wal-fix`; it links affected SQLite and is not a safe P0
runtime. Recreate the environment against a permanent patched interpreter before routine cleanup.

Before a heavy gate inspect free RAM, disk, CPU and Unreal/browser/build processes. Admission repeats
the check; a static preflight does not reserve resources. Do not run gates concurrently.

## Start API, worker or both

```powershell
# API and separate worker; build only when the dashboard artifact is missing.
uv run python local-control-center/scripts/start_control_center.py --mode supervisor
# API only, requiring an existing dashboard build.
uv run python local-control-center/scripts/start_control_center.py --mode api --no-build
# Worker only; no dashboard build or API listener.
uv run python local-control-center/scripts/start_control_center.py --mode worker
```

These are alternatives, not commands to launch three simultaneous instances. `--db-path` and
`--workspace` must identify the same intended instance for separate API/worker launches. Default
database resolution remains `~/.claude/local-control-center/platform.sqlite`. The PowerShell wrapper
forwards to the same Python launcher; it does not create another worker implementation. P0 accepts
only `--worker-count 1`. The API remains available if its supervised worker exits.

## Read state and issue controls

The Operations panel under Settings shows worker leadership, resource waits, executions, managed
processes and runtime blockers. Mutating HTTP requests require the loopback token and a valid Origin.
Keep that token in memory; do not print or commit it.

```powershell
$AidoBase = 'http://127.0.0.1:4310'
$AidoHandshake = Invoke-RestMethod "$AidoBase/api/v1/security/handshake"
$AidoHeaders = @{ 'X-Local-Control-Token' = $AidoHandshake.token; Origin = $AidoBase }
Invoke-RestMethod "$AidoBase/api/v1/workers/status"
Invoke-RestMethod "$AidoBase/api/v1/operations/resources"
Invoke-RestMethod "$AidoBase/api/v1/operations/processes?limit=100"
Invoke-RestMethod "$AidoBase/api/v1/executions?limit=100"
```

| Control | Request | Meaning |
| --- | --- | --- |
| Pause | `POST /api/v1/workers/pause` | Stop new admission; do not claim active work was cancelled |
| Resume | `POST /api/v1/workers/resume` | Allow the leader to claim work again |
| Run once | `POST /api/v1/workers/run-once` | Queue one bounded batch request; 202 is acceptance |
| Drain | `POST /api/v1/workers/drain` | Stop new claims and exit after current work |
| Cancel execution | `POST /api/v1/executions/{id}/cancel` | Durable cancellation with JSON `reason` |
| Cancel tree | `POST /api/v1/operations/processes/{id}/cancel` | Target one recorded AIDO tree, with `reason` |
| Emergency stop | `POST /api/v1/workers/emergency-stop` | Stop admission and request cancellation of active AIDO trees |

Example explicit pause (no request body required):

```powershell
Invoke-RestMethod -Method Post -Headers $AidoHeaders "$AidoBase/api/v1/workers/pause"
```

For cancellation/emergency stop supply a nonblank human reason in JSON and `Content-Type:
application/json`. A 202 response means the request was persisted. Observe execution state/events
and terminal process evidence before declaring stopped. Repeating cancellation is safe. Never replace
these controls with a broad `Stop-Process` by executable name.

`GET /api/v1/executions/{id}/events?afterSeq=0&limit=100` supplies ordered incremental events. Preserve
the returned execution ID after acceptance and poll it; do not retry a productive POST just because
the browser observation timed out. Automatic command idempotency across arbitrary repeated POSTs is
not claimed. Resource waits expose a reason; see [profiles](p0-resource-profiles.md).

## Quality tiers and recoverable evidence

```powershell
uv run python -m local_control_center.quality --tier fast --python-test tests_py/test_operational_recovery.py
uv run python -m local_control_center.quality --tier story --python-test tests_py/test_operational_recovery.py --web-test tests_web/operational-hardening.spec.js
corepack pnpm@10.24.0 run quality:pr
powershell -NoProfile -File scripts/verify-operational-hardening.ps1
corepack pnpm@10.24.0 run quality:release
```

Run only the appropriate command, sequentially. Fast/story are bounded iteration checks; neither is
delivery approval. PR retains full Python, desktop/mobile browser suites, build, TypeScript, Ruff,
format, Biome, architecture, Gitleaks, Semgrep and diff checks. Release runs the operational verifier
(which includes PR) plus an isolated locked clean installation, API smoke, web build and real baseline
upgrade/backup/restore. No tier starts real provider inference. Native test helpers use isolated data.

The verifier writes `report.json`, `report.md`, `process-tree-report.json`, `resource-report.json`,
`concurrency-report.json` and `migration-report.json` under `.tmp/operational-hardening-p0/`.
Unique run directories retain per-step JUnit and reports; supervised trees retain redacted stdout,
stderr, hashes and native metrics. Exit 0 is required; exit 75 means resource wait. Missing terminal
evidence is unknown/interrupted, never success. Check the owned process before restarting an
interrupted gate; do not launch an overlapping copy.

Before each command, resource admission may wait up to 120 seconds, rechecking every five seconds
with unchanged resource limits. This handles temporary host contention without restarting completed
steps. If admission still fails, the gate exits 75. A command that started and then failed, timed out
or was cancelled is never automatically retried or treated as successful.

## Explicit real Codex smoke (not executed by P0 verification)

Prerequisites: operator approval for a potentially billable call, selected isolated workspace,
configured and enabled native Codex installation/account, native authentication, enabled provider
and project policy, catalog mapping for `fast_analysis`, and sufficient host capacity. Enabling flags
is a separate operator action; this runbook does not authorize it.

1. Inspect `GET /api/v1/model-gateway/cli-runtimes/codex_cli/compatibility`.
2. Request the queued capability probe at
   `POST /api/v1/model-gateway/cli-runtimes/codex_cli/capabilities/probe` and observe its execution.
3. Only after explicit approval, POST to
   `/api/v1/model-gateway/cli-runtimes/codex_cli/compatibility/smoke` with the actual `workspaceId`,
   a meaningful `reason` and `approved: true`.
4. Observe terminal result and read compatibility again. A valid receipt requires the exact binary
   and contract fingerprints, a supervised exit 0, no timeout/cancellation/remaining descendants,
   recognized JSON completion, and the expected read-only marker with no tool events.

An installed binary, a version string, native login or a mocked test cannot replace this receipt.
Authentication/configuration missing means `configuration_required`; an incompatible CLI remains
blocked. Never promote unverified compatibility to production readiness.
