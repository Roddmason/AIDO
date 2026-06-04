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
```

`openapi:generate` is local-only and imports the FastAPI app directly; it does
not fetch schemas over the network. There is no GitHub quality workflow in this
repo; run the verification commands locally before pushing.
The frontend engine contract is Node >=24.16.0 <25.0.0. `node:use` should leave
`node --version` on that range before OpenAPI generation, typecheck, build, or
Playwright runs. Engine warnings mean the local shell is not honoring the repo
runtime contract, even when a command happens to pass.
`test:all` includes `typecheck:web`, the production web build, and Python tests.
`quality` repeats `typecheck:web` as an explicit gate before the security scans
so TypeScript contract drift is caught even when Vite can still transpile.

Runtime adapter release validation is intentionally separate from local quality
because it requires installed OpenHands/SWE-agent CLIs and explicit issue text
environment variables. Use `pnpm run smoke:runtime:preflight` locally, and run
`pnpm run smoke:runtime:release:preflight` before starting a prepared release
validation machine. `pnpm run smoke:runtime:release` repeats that strict
preflight before submitting brokered runtime smoke through the running server.
These scripts write ignored JSON evidence under `.tmp/runtime-validation/`;
keep those reports with release notes when validating optional external
runtimes.

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
local-control-center/scripts/protect-main-branch.ps1
```

Use `-DryRun` to inspect the GitHub API payload before applying it.

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
