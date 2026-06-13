# Development

## Tooling

- JavaScript package manager: PNPM through Corepack.
- Python environment manager: `uv`.
- Python linting: Ruff.
- Secret scanning: Gitleaks.
- SAST: Semgrep with local AIDO rules for shell execution, `shell=True`,
  plaintext secret persistence, and optional runtime adapter bypasses.

## Setup

```powershell
corepack enable
corepack pnpm@10.24.0 install
uv sync --extra dev --extra test
```

## Verification

```powershell
corepack pnpm@10.24.0 run node:use
uv run pytest tests_py -q
uv run --extra dev ruff check .
corepack pnpm@10.24.0 run openapi:generate
corepack pnpm@10.24.0 run typecheck:web
corepack pnpm@10.24.0 run build:control-center
corepack pnpm@10.24.0 run test:web
corepack pnpm@10.24.0 run security:secrets
corepack pnpm@10.24.0 run security:sast
corepack pnpm@10.24.0 run smoke:runtime:preflight
corepack pnpm@10.24.0 run quality
corepack pnpm@10.24.0 run release:certify
```

`quality` runs the local PowerShell gate at `scripts/quality-local.ps1`. It
executes the productive truth scanner, Python tests, Playwright web tests,
frontend build, web typecheck when declared, lint when declared, and the
architecture guardrails, followed by gitleaks and Semgrep. The scanner fails
when productive code contains mock, fake, dummy, or internal mock runtime tokens
outside tests/docs, when runtime providers hardcode `available=true`, or when
`shell=True` appears outside controlled tests.

`openapi:generate` is local-only and imports the FastAPI app directly; it does
not fetch schemas over the network. There is no GitHub quality workflow in this
repo; run the verification commands locally before pushing.
The frontend engine contract is Node >=24.16.0 <25.0.0. `node:use` should leave
`node --version` on that range before OpenAPI generation, typecheck, build, or
Playwright runs. Engine warnings mean the local shell is not honoring the repo
runtime contract, even when a command happens to pass.
`test:all` includes `typecheck:web`, the production web build, and Python tests.
`quality` is stricter than `test:all`: it also runs web tests, lint,
architecture guardrails, security scans, and the productive truth scanner.

Runtime adapter release validation is intentionally separate from local quality
because it requires installed OpenHands/SWE-agent CLIs and explicit issue text
environment variables. Use `pnpm run smoke:runtime:preflight` locally, and run
`pnpm run smoke:runtime:release:preflight` before starting a prepared release
validation machine. `pnpm run smoke:runtime:release` repeats that strict
preflight before submitting brokered runtime smoke through the running server.
These scripts write ignored JSON evidence under `.tmp/runtime-validation/`;
keep those reports with release notes when validating optional external
runtimes.

For a reproducible release certification run, use:

```powershell
corepack pnpm@10.24.0 run release:certify
```

The release runner is local-first and opt-in. It always executes
`corepack pnpm@10.24.0 run quality`, writes redacted logs and JSON reports under
`.tmp/release-certification/<timestamp>/`, and then runs each real CLI release
smoke only when its required environment variables are present. Missing optional
runtime configuration is recorded as `status=configuration_required` with
`execution=skipped`, and the top-level `certificationScope` remains
`quality_with_configuration_required_smokes`; configured smoke failures fail the certification. Use
`scripts/release-certify.ps1 -FailOnSkippedSmokes` when a release requires every
optional smoke profile to be configured and executed.

## Real Capability Table

| Capability | Real state | Endpoint/UI | Tests | Limitations |
| --- | --- | --- | --- | --- |
| Local quality command | Implemented as `corepack pnpm@10.24.0 run quality`. | `scripts/quality-local.ps1`. | Full local quality sequence when tooling is installed. | No GitHub Actions dependency; local machine must provide required CLIs and Node engine. |
| Release certification runner | Implemented as `corepack pnpm@10.24.0 run release:certify`. | `scripts/release-certify.ps1`. | `tests_py/test_release_certification_runner.py` plus the full local quality command during certification. | Optional smokes run only with real configured CLIs; absent env vars are reported as `configuration_required`, not treated as runtime success. |
| Productive truth scanner | Implemented as required first quality step. | `scripts/productive-truth-scan.py`, `pnpm run quality:productive-truth`. | `tests_py/test_no_mock_productive_scanner.py`. | Allows prohibited terms in tests/docs/readmes only; product code fails. |
| Architecture guardrails | Implemented as focused pytest script. | `pnpm run quality:architecture`. | `tests_py/test_real_readiness_architecture.py`, boundary/slice/web guardrails. | Guardrails are static/contract tests; they do not replace runtime smokes. |
| Frontend validation | Implemented through Vite build, TypeScript check, and Playwright. | `build:control-center`, `typecheck:web`, `test:web`. | Playwright and TypeScript checks. | Playwright requires browser dependencies installed locally. |
| Security validation | Implemented through gitleaks and Semgrep scripts. | `security:secrets`, `security:sast`. | Local commands and Semgrep rules. | These tools must be installed/resolvable in the developer environment. |

Observed local caveat: this repository requires Node >=24.16.0 <25.0.0.
Commands may still pass under another Node version, but that is not the
documented runtime contract and should be fixed before treating local evidence
as release-grade.

## Branch Protection

Protected repository branches must be controlled by GitHub rulesets, not by a
product CI workflow. The current policy protects every branch except `dev`:

- Protected branches require pull request review, code owner review, resolved
  review threads, linear history, and non-fast-forward protection.
- `dev` remains the integration branch. The repository owner can push directly
  to `dev`; outside contributions should come through pull requests.
- Branch deletion is blocked for protected branches.

The repo includes a helper that creates or updates a repository ruleset for
`refs/heads/*` while excluding `refs/heads/dev`, without required status checks:

```powershell
local-control-center/scripts/protect-repository-branches.ps1
```

Use `-DryRun` to inspect the GitHub API payload before applying it.
The legacy `protect-main-branch.ps1` wrapper is deprecated and scheduled for
removal on 2026-09-01.

## Source Hygiene

Do not commit:

- `node_modules`
- `local-control-center/dist`
- `.venv`
- `__pycache__`
- `.pytest_cache`
- `.env`
- SQLite runtime files
- `.tmp`
- `test-results`

The removed root `src/` tree had unclear provenance. Do not restore it as
product source unless it has a separate license review and migration plan.
