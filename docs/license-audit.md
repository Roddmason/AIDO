# License Audit

Date: 2026-05-20
Project license strategy: MIT open source.

## Decision

AIDO is open source under the MIT License. The repository metadata, root
license, notice file, and package metadata must all agree on `MIT`.

The project remains local-first and security-gated. Open source availability
does not change the runtime safety model: secrets must not be committed,
dangerous runtime flags stay blocked, and real provider/CLI execution remains
behind local policy and approval gates.

## Core Dependency Policy

- Core embedded dependencies must be OSI-compatible.
- AGPL dependencies are allowed only as separately configured optional services
  after an architecture decision.
- Source-available, fair-code, no-commercial-use, or ambiguous-license projects
  must not become core dependencies.
- n8n is not a core dependency.

## Python Dependencies

Runtime:

- `fastapi`: open source, compatible.
- `uvicorn`: open source, compatible.
- `numpy`: BSD-style, compatible.
- `faiss-cpu`: optional; AIDO must work in NumPy mode without FAISS.

Optional:

- `openai-agents`: optional integration; audit exact license and transitive
  dependencies before making it core.
- `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http`: optional
  Apache-2.0 telemetry exporters; AIDO must run without them unless
  `AIDO_OTEL_EXPORTER=otlp_http` is configured.

Development/security:

- `ruff`: MIT, compatible.
- `pre-commit`: MIT, compatible.
- `pip-licenses`: MIT, compatible.
- `semgrep`: acceptable as a development/security tool, not embedded runtime.

## JavaScript Dependencies

Managed by PNPM and audited with:

```powershell
corepack pnpm@10.24.0 licenses list
```

Current status:

- React, ReactDOM, Vite, TypeScript, Playwright, lucide-react, and
  `@xyflow/react` are compatible with the MIT project.
- `@radix-ui/react-dialog`, `@radix-ui/react-dropdown-menu` and
  `@radix-ui/react-tooltip` were **removed**: zero imports in `web/src` (the
  Dialog/Drawer/Tooltip UI is hand-rolled in `components/primitives.tsx`).
- New justified dependencies — added at **exact** versions, all **MIT**, with
  peer ranges compatible with React 18.3.1 and Vite 6:
  - `motion@12.40.0` — shell/UI animations (planned consumer: replaces the
    hand-rolled `motion/useControlMotion.ts`).
  - `react-resizable-panels@4.11.2` — resizable IDE shell panels (planned
    consumer: Explorer/Inspector split in `app/AppShell.tsx`).
  - `@tanstack/react-virtual@3.14.3` — list/table virtualization (planned
    consumer: long logs / usage-ledger tables).
- TanStack supply-chain note: `@tanstack/react-virtual` is MIT, headless,
  dependency-light and actively maintained by the TanStack org; approved for
  use. This supersedes the prior "avoid TanStack" hold.
- `gsap` and `@gsap/react` were removed from core dependencies after PNPM
  reported the GSAP standard license rather than an OSI license; `motion`
  (MIT) is the approved animation library going forward.
- `@xyflow/react` is retained for now and will be removed after the DAG is
  replaced.
- Do not add CDN-hosted fonts, scripts, or styles.

## Required Audit Commands

```powershell
uv run --extra dev python -X utf8 -m piplicenses --format=markdown
corepack pnpm@10.24.0 licenses list
gitleaks detect --no-git --source . --config .gitleaks.toml --redact
uv run --extra dev semgrep scan --config .semgrep.yml --no-git-ignore local_control_center tests_py local-control-center/web/src tests_web
```

## Current Conclusion

The active Python/FastAPI backend and React console can ship under MIT. The
dependency policy remains compatible with public open-source distribution; the
known GSAP license blocker has been removed from the core frontend.

`pip-licenses` currently reports `peewee` as `UNKNOWN` because its installed
metadata omits a license field. Manual local verification found
`.venv/Lib/site-packages/peewee-3.19.0.dist-info/licenses/LICENSE` with MIT
license text. It is pulled through development/security tooling rather than
AIDO runtime code, so it is not a core dependency blocker.
