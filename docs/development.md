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
uv run pytest tests_py -q
uv run --extra dev ruff check .
corepack pnpm@10.24.0 run build:control-center
corepack pnpm@10.24.0 run typecheck:web
corepack pnpm@10.24.0 run openapi:generate
corepack pnpm@10.24.0 run test:web
corepack pnpm@10.24.0 run smoke:runtime:preflight
corepack pnpm@10.24.0 run quality
```

`openapi:generate` is local-only and imports the FastAPI app directly; it does
not fetch schemas over the network. There is no GitHub quality workflow in this
repo; run the verification commands locally before pushing.
`test:all` includes `typecheck:web`, the production web build, and Python tests.
`quality` repeats `typecheck:web` as an explicit gate before the security scans
so TypeScript contract drift is caught even when Vite can still transpile.

Runtime adapter release validation is intentionally separate from local quality
because it requires installed OpenHands/SWE-agent CLIs and explicit issue text
environment variables. Use `pnpm run smoke:runtime:preflight` locally, and run
`pnpm run smoke:runtime:release` only on a prepared release-validation machine.

## Main Branch Blocking

`main` must be blocked in GitHub settings, not by a product CI workflow. The
repo includes a helper that creates or updates a repository ruleset targeting
`refs/heads/main`, without required status checks:

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
