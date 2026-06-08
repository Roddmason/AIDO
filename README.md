# AIDO / Local Control Center

AIDO is a local-first native control plane for software-development agents on
Windows, Linux and macOS.
It is not a chat clone and not an autonomous free-for-all. The product goal is
to control workflows, approvals, isolated workspaces, policy decisions, evidence,
cost records, and audit events around agent-assisted engineering.

## Current Status

- Backend: Python/FastAPI with SQLite as the canonical local store.
- Frontend: Vite + React + TypeScript console built with PNPM.
- Package management: PNPM for JavaScript, `uv` for Python.
- License: MIT. AIDO is open source; core dependencies remain OSI-compatible
  or isolated as optional adapters.
- Runtime target: native OS process on Windows, Linux and macOS. Docker is
  optional, not required.

## What It Is

- Local AI SDLC control plane.
- Durable job and approval surface.
- Policy-gated command and tool execution foundation.
- Workflow, workspace, agent, model-policy, skill, memory, and evidence backend
  foundation.
- Governance ledger for architecture decisions, risk register entries, and
  prioritized next steps.
- Hybrid agent runtime catalog per agent: API, CLI, Ollama, or manual, always
  behind policy and evidence controls.
- Runtime provider truth from `/api/v1/runtime/providers`, including whether a
  provider is detected, configured, available, executable, and why it is
  blocked.
- Real `issue_to_patch` runs are completion-gated by executable runtime,
  isolated Git worktree, diff/evidence capture, passing QA, and required
  approval resolution. Test simulators are not product runtime providers.
- Automatic risk creation from policy-gated actions, failed/blocked QA, and
  cancelled workflows.
- Workspace-aware path policy and task-scoped Git worktree allocation when the
  project is a Git repository.

## Real Capability Matrix

| Capability | Real state | Endpoint/UI | Tests | Limitations |
| --- | --- | --- | --- | --- |
| Local control plane | Implemented as Python/FastAPI with SQLite and loopback-token protected writes. | `GET /api/v1/overview`, `GET /api/v1/security/handshake`, dashboard. | `tests_py`, Playwright dashboard tests. | Local-first runtime; not a hosted multi-user deployment surface. |
| Runtime provider truth | Implemented and tested. Provider rows report `detected`, `configured`, `available`, `executable`, `reason`, capabilities, and required config from real checks. | `GET /api/v1/runtime/providers`, Runtime & Model Gateway UI, Command Center. | `tests_py/test_aido_real_runtime_slice.py`, `tests_web/control-center.spec.js`, `tests_py/test_internal_mock_product_boundary.py`. | A configured provider is not executable until health/detection, enabled state, and execution gates pass. |
| Runtime provider configuration | Implemented as read-only env inspection with fingerprints for secrets. | `GET /api/v1/runtime/provider-configuration`, Runtime & Model Gateway UI. | `tests_py/test_aido_real_runtime_slice.py`, web provider configuration tests. | The endpoint does not store or edit secrets; set environment variables before startup. |
| API model providers | Configurable. Real remote execution is blocked unless credentials, model, explicit health, enabled account, and `AIDO_ENABLE_REAL_PROVIDER_CALLS=true` are present. | Provider configuration endpoint, model gateway health routes, Runtime & Model Gateway UI. | `tests_py/test_model_runtime_gateway.py`, `tests_py/test_developer_agent_real_runtime.py`, `tests_py/test_architect_agent_real_runtime.py`. | Disabled by default. Missing key, base URL, model, health, or flag returns blocked/configuration-required/unavailable. |
| CLI runtimes | Detectable and version-checked. Productive execution requires configured command, installed CLI, enabled account, `AIDO_ENABLE_CLI_RUNTIMES=true`, workspace boundary, and patch capability. | `GET /api/v1/runtime/providers`, Command Center runtime picker. | `tests_py/test_aido_real_runtime_slice.py`, `tests_py/test_optional_smoke_profiles.py`, Playwright Command Center tests. | The CLI must expose a real workspace-bound issue-to-patch/code-edit argv contract; installed alone is not enough. |
| `issue_to_patch` workflow | Implemented as fail-closed execution slice. It allocates a Git worktree, runs an executable runtime through policy/sandbox, captures diff/artifacts, runs QA, and creates review evidence. | `POST /api/v1/workflows/issue-to-patch`, Command Center `issue_to_patch real runtime`. | `tests_py/test_aido_real_runtime_slice.py`, `tests_web/control-center.spec.js`. | `completed` requires real runtime execution, non-empty diff, passed QA evidence, valid evidence package, and `requireApproval=false`; otherwise it stops as blocked/review/failed. |
| Missing runtime behavior | Implemented. Missing executable runtime returns `runtime_unavailable` with a technical reason and diagnostic evidence. | `POST /api/v1/workflows/issue-to-patch`, Command Center result panel. | `test_issue_to_patch_without_executable_runtime_finishes_unavailable_not_success`, web blocked-runtime test. | No code is edited and no workflow is marked completed. |
| Evidence and artifacts | Implemented for evidence packages, normalized test results, artifact ingestion/download, retention review, and QA report export. | `/api/v1/evidence`, `/api/v1/evidence/{id}`, artifact download/report endpoints, Evidence & QA UI. | Evidence, workflow, agent, and web suites. | Artifact files are local under the configured artifact root and served only through token-protected endpoints. |
| Local quality gate | Implemented as a local PowerShell command. | `corepack pnpm@10.24.0 run quality`, `scripts/quality-local.ps1`. | `tests_py/test_no_mock_productive_scanner.py`, architecture guardrail tests, Python/web/security commands. | Requires local toolchain availability: Node engine, PNPM/Corepack, uv, Playwright dependencies, gitleaks, and Semgrep. |

## What It Is Not

- It is not a production deployment platform.
- It does not permit dangerous shell execution without policy gates.
- It does not require WSL.
- It does not use Node as backend business runtime.
- It does not keep compatibility routes as active product surface.
- It does not ship a product runtime named `internal_mock`.
- It does not mark unavailable providers, missing CLIs, missing credentials, or
  skipped QA as completed work.

## Setup

AIDO's frontend toolchain targets Node >=24.16.0 <25.0.0 with PNPM 10.24.0.
Use the repository `.nvmrc` and helper script before running web builds or
tests so engine warnings indicate a real environment mismatch rather than an
implicit downgrade.

Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File local-control-center/scripts/use-node.ps1
corepack enable
corepack pnpm@10.24.0 install
uv sync --extra dev --extra test
```

Linux/macOS shell:

```bash
nvm install 24.16.0
nvm use 24.16.0
corepack enable
corepack pnpm@10.24.0 install
uv sync --extra dev --extra test
```

Optional FAISS install:

```powershell
uv sync --extra faiss --extra dev --extra test
```

## Configure Real Providers

Provider configuration is process-local. Set environment variables before
starting the backend, then inspect `GET /api/v1/runtime/provider-configuration`
and `GET /api/v1/runtime/providers`. Secret values are never returned; the
configuration endpoint shows configured/missing state and non-reversible
fingerprints only.

OpenAI-compatible API:

```powershell
$env:AIDO_OPENAI_COMPATIBLE_BASE_URL = "https://provider.example/v1"
$env:AIDO_OPENAI_COMPATIBLE_API_KEY = "<real API key>"
$env:AIDO_OPENAI_COMPATIBLE_MODEL = "provider/model"
$env:AIDO_ENABLE_REAL_PROVIDER_CALLS = "true"
```

OpenRouter:

```powershell
$env:AIDO_OPENROUTER_API_KEY = "<real API key>"
$env:AIDO_OPENROUTER_MODEL = "provider/model"
$env:AIDO_ENABLE_REAL_PROVIDER_CALLS = "true"
```

NVIDIA NIM / Build:

```powershell
$env:AIDO_NVIDIA_API_KEY = "<real API key>"
$env:AIDO_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
$env:AIDO_NVIDIA_MODEL = "provider/model"
$env:AIDO_ENABLE_REAL_PROVIDER_CALLS = "true"
```

Ollama:

```powershell
$env:AIDO_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
```

The Ollama daemon must respond to `/api/tags` before it is available.

CLI runtimes:

```powershell
$env:AIDO_CODEX_COMMAND = "codex"
$env:AIDO_CLAUDE_COMMAND = "claude"
$env:AIDO_ENABLE_CLI_RUNTIMES = "true"
```

The command must resolve locally and pass the safe version check. Productive
execution still requires an executable provider account, structured argv,
workspace containment, policy allow/grant, and a supported patch capability.

## Run Real `issue_to_patch`

Start the local control center, get the loopback token, verify an executable
runtime, then submit the workflow:

```powershell
corepack pnpm@10.24.0 run start
```

In another PowerShell session:

```powershell
$base = "http://127.0.0.1:4310"
$token = (Invoke-RestMethod "$base/api/v1/security/handshake").token

Invoke-RestMethod "$base/api/v1/runtime/providers" |
  Select-Object -ExpandProperty providers |
  Select-Object id, configured, available, executable, capabilities, reason

$body = @{
  projectId = "<existing-project-id>"
  title = "Fix the failing behavior"
  issueText = "Concrete issue description and acceptance criteria."
  preferredRuntime = "codex_cli"
  qaCommands = @(
    @("uv", "run", "pytest", "tests_py", "-q")
  )
  requireApproval = $true
} | ConvertTo-Json -Depth 8

Invoke-RestMethod "$base/api/v1/workflows/issue-to-patch" `
  -Method Post `
  -Headers @{ "X-AIDO-Token" = $token } `
  -ContentType "application/json" `
  -Body $body
```

Use an actual project id from `GET /api/v1/projects` or the dashboard. The
project path must be a Git repository for productive CLI execution; otherwise
the workflow returns `runtime_unavailable` with the workspace reason. With
`requireApproval=true`, a successful implementation and QA run stops at
`evidence_ready`/`needs_human_review`. A workflow reports `completed` only when
real runtime execution, non-empty diff evidence, passed QA, valid evidence, and
`requireApproval=false` are all true.

If runtime configuration is missing, a CLI is not installed, a health check
fails, QA commands are absent or skipped, or policy blocks execution, the API
returns `runtime_unavailable`, `configuration_required`, `blocked`,
`qa_failed`, or `evidence_ready` with a technical reason. It does not simulate a
patch or mark the workflow completed.

## Common Commands

```powershell
corepack pnpm@10.24.0 run start
uv run python local-control-center/scripts/start_control_center.py --dashboard-host 127.0.0.1 --dashboard-port 4310
corepack pnpm@10.24.0 run build:control-center
corepack pnpm@10.24.0 run openapi:generate
corepack pnpm@10.24.0 run test:py
corepack pnpm@10.24.0 run test:web
corepack pnpm@10.24.0 run quality
```

`quality` runs `scripts/quality-local.ps1`, which executes:

- `quality:productive-truth`
- Python tests
- Playwright web tests
- frontend build
- web typecheck when declared
- lint when declared
- architecture guardrails
- secret scan
- Semgrep SAST

The productive truth scanner fails if productive code contains prohibited mock,
fake, dummy, or internal mock runtime tokens outside tests/docs, if runtime
providers hardcode `available=true`, or if `shell=True` appears outside
controlled tests.

Direct backend:

```powershell
uv run python -m local_control_center --dashboard-host 127.0.0.1 --dashboard-port 4310
```

Native process startup:

- Cross-platform default:
  `uv run python local-control-center/scripts/start_control_center.py`
- Windows wrapper:
  `corepack pnpm@10.24.0 run start:windows`
- Default URL: `http://127.0.0.1:4310`
- Stop: press `Ctrl+C` in the terminal that owns the process.

The native launcher builds the dashboard when missing, serves the built
frontend through FastAPI, and sets safe defaults:
`AIDO_ENABLE_REAL_PROVIDER_CALLS=false`,
`AIDO_ENABLE_CLI_RUNTIMES=false`, and `AIDO_REDACT_SECRETS=true`.

Optional local smokes:

```powershell
$env:AIDO_RUNTIME_SMOKE = "1"
local-control-center/scripts/smoke-runtime-adapters.ps1

$env:AIDO_OTEL_SMOKE = "1"
local-control-center/scripts/smoke-otel-exporter.ps1
```

The smoke scripts skip unless explicitly opted in. OpenHands, SWE-agent, MCP
servers, Docker, and external OTEL collectors are optional adapters, not startup
requirements.

This repository intentionally does not ship a GitHub quality workflow. Run
quality checks locally before pushing. Repository rulesets protect branches and
require pull request review for protected work. The owner may push directly to
`dev` for local-first integration work.

```powershell
local-control-center/scripts/protect-repository-branches.ps1
```

The legacy `protect-main-branch.ps1` name is a temporary warning-only wrapper
scheduled for removal on 2026-09-01.

## Security

Mutating API calls require the loopback handshake token. Shell and tool actions
are evaluated through the internal policy engine. Critical actions require human
approval; low-risk test/build/read-only actions can be allowed by policy.

Never commit `.env`, local SQLite databases, build outputs, dependency folders,
or generated artifacts.

## Documentation

- `docs/architecture-audit.md`
- `docs/project-map.md`
- `docs/backend.md`
- `docs/security-policy.md`
- `docs/workflows.md`
- `docs/workspaces.md`
- `docs/agents.md`
- `docs/runtime-providers.md`
- `docs/evidence.md`
- `docs/governance.md`
- `docs/frontend.md`
- `docs/license-audit.md`
- `docs/development.md`
- `docs/credentials.md`

## Contributing

Contributions are welcome through pull requests. Keep changes small, include
tests for behavior changes, do not commit secrets or generated artifacts, and
expect protected branches to require owner review before merge.
