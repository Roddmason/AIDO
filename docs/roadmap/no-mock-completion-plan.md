# No-Mock Completion Plan

This roadmap records real completion state for the no-mock/productive-truth
slice. It is not a promise that unavailable runtimes are executable.

## Capability Matrix

| Capability | Real state | Endpoint/UI | Tests | Limitations |
| --- | --- | --- | --- | --- |
| Product runtime catalog without internal mock provider | Closed. Product provider APIs and UI do not expose `internal_mock` as a runtime. | `GET /api/v1/runtime/providers`, Command Center, Runtime & Model Gateway. | `tests_py/test_internal_mock_product_boundary.py`, `tests_web/control-center.spec.js`. | Tests may still use doubles inside test-only paths. |
| Provider readiness truth | Closed. `configured`, `available`, and `executable` come from config, health/detection, enabled state, capability, and execution flags. | `/api/v1/runtime/providers`, `/api/v1/runtime/provider-configuration`. | `tests_py/test_aido_real_runtime_slice.py`. | Real remote providers need operator-supplied credentials and health checks. |
| `issue_to_patch` completion gate | Closed for local contract. Completion requires real runtime execution, Git worktree, non-empty diff, QA evidence, valid evidence, and approval state. | `POST /api/v1/workflows/issue-to-patch`, Command Center. | `tests_py/test_aido_real_runtime_slice.py`, web Command Center tests. | Installed CLIs must provide a real workspace-bound issue-to-patch/code-edit command contract. |
| Quality no-mock scanner | Closed. Local quality fails on prohibited product tokens, hardcoded provider availability, and `shell=True` outside controlled tests. | `scripts/productive-truth-scan.py`, `pnpm run quality:productive-truth`. | `tests_py/test_no_mock_productive_scanner.py`. | Markdown docs and test folders are allowed to discuss prohibited terms. |
| Local quality gate | Closed as local command. | `corepack pnpm@10.24.0 run quality`, `scripts/quality-local.ps1`. | Prior local run covered scanner, Python tests, web tests, build, typecheck, lint, architecture guardrails, gitleaks, and Semgrep. | It depends on local CLIs and Node engine; no mandatory GitHub Actions workflow is shipped. |
| Release certification runner | Closed as optional local command. | `corepack pnpm@10.24.0 run release:certify`, `scripts/release-certify.ps1`. | `tests_py/test_release_certification_runner.py`; runner executes the full `quality` gate and configured release smokes. | Optional CLI smokes report `configuration_required` with skipped execution unless real env vars are present; use `-FailOnSkippedSmokes` for strict release machines. |
| Evidence-backed blocked state | Closed. Missing runtime produces `runtime_unavailable` with evidence and reason, not success. | Workflow response, Evidence & QA UI. | `test_issue_to_patch_without_executable_runtime_finishes_unavailable_not_success`. | Diagnostic evidence is not implementation evidence. |

## Closed Modules

- Runtime provider truth source: product responses expose configured,
  available, executable, reason, capability, and required-configuration state.
- Internal mock product boundary: product provider APIs, workflows, UI runtime
  selectors, model gateway, seeds, and agent profiles reject or omit the
  internal mock runtime.
- `issue_to_patch` fail-closed runner: missing runtime, missing Git worktree,
  missing QA, empty diff, failed QA, missing evidence, and pending approval do
  not produce completed workflow state.
- Evidence contract: workflow, job, agent run, workspace, runtime, artifact,
  QA, policy, approval, and hash links are persisted for the execution path.
- Local quality command: `scripts/quality-local.ps1` runs scanner, Python
  tests, web tests, build, optional typecheck/lint, architecture guardrails,
  secret scan, and Semgrep.
- Release certification runner: `scripts/release-certify.ps1` writes redacted
  report/log artifacts under `.tmp/release-certification/`, always runs
  `quality`, and runs only release smokes with real configured env vars.
- Productive truth scanner: productive code fails for no-mock token leakage,
  hardcoded runtime `available=true`, and `shell=True` outside controlled tests.
- Documentation state: README and target docs now distinguish configured,
  available, executable, blocked, and completed.

## Final Semantic Audit - 2026-06-13

This audit closes only repo-local truth guarantees. It does not certify that
missing external CLIs, provider credentials, endpoints, workspaces, QA commands,
or release environments exist.

### Closed

- Productive truth scanner prunes ignored/generated directories before
  recursion and rejects quoted or unquoted hardcoded product
  `available=true` patterns.
- Product runtime providers, model gateway, retrieval indexing, workflow
  runners, and agent contracts no longer depend on `internal_mock` or simulated
  success paths for product readiness.
- Provider account create/patch requests cannot write server-owned health
  fields; config changes reset health to `unknown` until a real health check
  records a new state.
- Runtime, sandbox, telemetry, QA, security, architecture, developer, DevOps,
  and model gateway health fields derive `available`, `executable`, `status`,
  and `reason` from observed config/execution state.
- Model-call cost metadata and frontend cost/token surfaces preserve unknown
  values instead of coercing missing values to zero.
- Agents UI no longer falls back to synthetic provider, routing, role-policy, or
  runtime catalogs. Catalog load failures block save with
  `configuration_required`.
- Control-plane polling does not reuse stale optional retrieval/runtime health
  snapshots when the backend omits or cannot refresh them.
- `issue_to_pr` and `issue_to_patch` blocked states report unavailable runtime
  health with a technical reason instead of executable success.

### Requires Real Environment

- Provider accounts need real credentials, configured endpoints, and successful
  server-side health checks before product code may report them available.
- CLI runtimes such as Codex, Claude, OpenHands, and SWE-agent need installed
  binaries, explicit argv contracts, enabled execution flags, a real workspace,
  and executable smoke evidence before workflows may run them.
- External scanners, OTLP exporters, GitHub/PR publication, and release smokes
  remain unavailable or configuration-required until their CLIs, tokens,
  endpoints, and policies are present.
- QA evidence must come from real configured commands and artifacts. Missing QA
  command, failed execution, missing evidence, or empty diff remains blocked.

### Out Of Scope

- Supplying secrets, provider accounts, paid API access, external endpoints, or
  organization-specific release infrastructure.
- Certifying external provider behavior beyond local contracts and healthcheck
  boundaries.
- Publishing commits, pushing branches, creating pull requests, or adding CI
  workflows without an explicit release request.
- Replacing human review for sensitive patches, destructive operations,
  production releases, or secret-bearing configuration.

## Pending Modules

- Future: richer provider account editing UI for credentials or provider
  account lifecycle, if the product decides to manage secrets instead of
  environment-only configuration.
- Future: devcontainer execution, only after sandbox policy, image catalog,
  evidence capture, and explicit approval semantics are complete.
- Future: broader external provider health workflows for every gateway type,
  using real endpoints and preserving secret redaction.

## Not Completed

- No external provider credential was configured by this documentation update.
  API providers remain configuration-required or unavailable until a developer
  supplies real env vars and passes health.
- No local Codex, Claude, OpenHands, or SWE-agent issue-to-patch execution is
  implied by the release runner alone. CLI providers remain non-executable
  until commands resolve, version checks pass, execution flags are enabled, and
  the CLI exposes a real workspace-bound patch contract.
- No GitHub Actions quality workflow was added. The quality and certification
  gates are intentionally local through `scripts/quality-local.ps1` and
  `scripts/release-certify.ps1`.
- No product fallback was added for missing runtime, missing CLI, missing QA, or
  missing evidence.

## Residual Risks

- Local validation is only as strong as the developer environment. The repo
  requires Node >=24.16.0 <25.0.0; engine warnings mean the shell is outside the
  documented frontend runtime contract even if commands pass.
- Real provider health can drift after startup. Operators should re-check
  `/api/v1/runtime/providers` before trusting a runtime for implementation.
- Optional external CLIs may change their command syntax. Release validation
  must use explicit argv environment variables and fail if the installed CLI
  does not match the documented contract.
- The scanner blocks high-risk simulation tokens in product code, but it is a
  pattern guardrail, not a semantic proof of every possible false-success path.
- Evidence proves what the local control plane captured. It does not replace
  human review for sensitive patches, secrets, destructive changes, or
  production release decisions.
