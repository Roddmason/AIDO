# Development

## Tooling

- JavaScript package manager: PNPM through Corepack.
- Python environment manager: `uv`.
- Python linting: Ruff.
- Secret scanning: Gitleaks.
- SAST: Semgrep.

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
corepack pnpm@10.24.0 run test:web
corepack pnpm@10.24.0 run quality
```

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

The root `src/` tree remains excluded from the active AIDO source until license
provenance is resolved.
