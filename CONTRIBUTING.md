# Contributing

Thanks for considering a contribution to AIDO.

## Workflow

- Open an issue or discussion for large design changes before writing a large
  patch.
- Use focused pull requests with tests for behavior changes.
- Keep generated artifacts, local databases, `.env`, dependency directories,
  and `.tmp` outputs out of commits.
- Do not include secrets, API keys, provider tokens, prompt dumps, or local
  workspace snapshots containing private data.
- Expect protected branches to require owner review before merge.

## Local Verification

Run the relevant checks before opening a pull request:

```powershell
uv run pytest tests_py -q
corepack pnpm@10.24.0 run typecheck:web
corepack pnpm@10.24.0 run build:web
corepack pnpm@10.24.0 run test:web
corepack pnpm@10.24.0 run security:secrets
corepack pnpm@10.24.0 run security:sast
```

## Security-Sensitive Work

Runtime adapters, provider execution, approvals, policy gates, credential
handling, workspace boundaries, and migrations require extra care. Keep those
changes narrow and include regression tests.
