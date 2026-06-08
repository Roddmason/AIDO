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
- Productive truth scanner: productive code fails for no-mock token leakage,
  hardcoded runtime `available=true`, and `shell=True` outside controlled tests.
- Documentation state: README and target docs now distinguish configured,
  available, executable, blocked, and completed.

## Pending Modules

- Future: real release-runner smoke for OpenHands/SWE-agent `issue_to_patch`
  with operator-supplied argv contracts and installed CLIs.
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
- No local Codex, Claude, OpenHands, or SWE-agent issue-to-patch execution was
  performed by this documentation update. CLI providers remain non-executable
  until commands resolve, version checks pass, execution flags are enabled, and
  the CLI exposes a real workspace-bound patch contract.
- No GitHub Actions quality workflow was added. The quality gate is intentionally
  local through `scripts/quality-local.ps1`.
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
