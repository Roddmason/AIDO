# License Audit

Date: 2026-05-18
Project license strategy: proprietary/private, no commercial use.

## Decision

AIDO is not open source at this stage. The repository uses a private
no-commercial-use license while preserving a dependency policy compatible with a
future Apache-2.0 or dual-license release.

The package metadata must not declare `ISC` because that would grant rights that
do not match the current product decision.

## Core Dependency Policy

- Core embedded dependencies must be OSI-compatible.
- AGPL dependencies are allowed only as separately configured optional services
  after an architecture decision.
- Source-available, fair-code, no-commercial-use, or ambiguous-license projects
  must not become core dependencies.
- n8n is explicitly not a core dependency.

## Python Dependencies

Runtime:

- `fastapi`: open source, compatible.
- `uvicorn`: open source, compatible.
- `numpy`: BSD-style, compatible.
- `faiss-cpu`: MIT-style upstream project; optional because Windows wheels and
  CPU support can vary. AIDO must work in NumPy mode without FAISS.

Optional:

- `openai-agents`: optional integration; audit exact license and transitive
  dependencies before making it core.

Development/security:

- `ruff`: MIT, compatible.
- `pre-commit`: MIT, compatible.
- `pip-licenses`: MIT, compatible.
- `semgrep`: LGPL-2.1 for the open source CLI/runtime components; acceptable as
  a development tool, not embedded runtime.
- `peewee`: appears as `UNKNOWN` in `pip-licenses` as a Semgrep transitive
  dependency. Treat as a dev-tool audit item before public release; do not embed
  it in AIDO runtime.

## JavaScript Dependencies

Current frontend/build dependencies are managed by PNPM and should be audited
with:

```powershell
corepack pnpm@10.24.0 licenses list
```

High-level status:

- React, ReactDOM, Radix, Playwright, esbuild, lucide-react, and
  `@xyflow/react` are compatible for this private project, subject to generated
  notices before public release.
- GSAP is currently present for dashboard motion and reports a standard
  no-charge license rather than a clear OSI license. Keep it out of core backend
  logic and replace it or isolate it before any open-source release.
- Avoid adding TanStack packages until supply-chain review is documented.
- Do not add CDN-hosted fonts, scripts, or styles.

## Legacy/Provenance Risks

The root `src/` tree contains many Claude Code and Anthropic references plus
`src/node_modules`. It should be treated as legacy/audit material, not AIDO
core. Do not copy implementation from it into Python/React core without proving
license provenance.

## Required Audit Commands

```powershell
uv run --extra dev pip-licenses --format=markdown
corepack pnpm@10.24.0 licenses list
gitleaks detect --no-git --source . --config .gitleaks.toml --redact
uv run --extra dev semgrep scan --config .semgrep.yml
```

## Current Conclusion

The active Python/FastAPI and React dashboard code can continue under the
private AIDO license. The dependency policy is compatible with future
open-source release work, but the root `src/` legacy tree must be separated or
removed before any public release.
