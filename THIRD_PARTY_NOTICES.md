# Third-Party Notices

AIDO uses third-party open source dependencies for its Python backend, React
frontend, build tooling, tests, and local quality checks.

This file is the human-maintained notice index. Generated dependency reports
should be produced during release or audit work with:

```powershell
uv run --extra dev pip-licenses --format=markdown
corepack pnpm@10.24.0 licenses list
gitleaks detect --no-git --source . --config .gitleaks.toml --redact
```

Current policy:

- Core runtime dependencies must be OSI-compatible.
- `faiss-cpu` is optional because Windows installation can vary; NumPy remains
  the default fallback retrieval backend.
- AGPL, source-available, fair-code, or no-commercial-use dependencies must not
  be embedded in the core without a written architecture decision.
- This repository itself is proprietary/no-commercial-use until relicensed.
