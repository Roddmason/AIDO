# Frontend Architecture

The active console is Vite + React + TypeScript. Node remains only the frontend
toolchain; business runtime and state belong to Python/FastAPI.

## Active Structure

```text
local-control-center/web/
├── index.html
├── vite.config.ts
├── tsconfig.json
└── src/
    ├── main.tsx
    ├── app/App.tsx
    ├── api/client.ts
    ├── api/types.ts
    ├── components/primitives.tsx
    ├── design-system/
    ├── features/
    ├── hooks/
    ├── lib/
    └── motion/useControlMotion.ts
```

## Product Surfaces

- Overview
- Command Center
- Workflows
- Jobs & Approvals
- Agents
- Workspaces
- Policy & Security
- Memory & Retrieval
- Evidence & QA
- Model Gateway
- Governance
- Integrations
- Audit Log
- Settings

## Data Rules

- The console consumes `/api/v1/overview`, `/api/v1/retrieval/status`, and
  `/api/v1/runtime/providers`.
- Mutations use the loopback handshake token.
- React does not decide policy, approval, workspace, agent, or evidence
  outcomes. It renders backend state and submits explicit commands.
- Missing backend state must be added to the API rather than fabricated in the
  client.
- Approval and event drawers render overview state directly. They are
  inspection/control surfaces; policy decisions remain backend-owned.
- The command palette uses only v1-backed actions. It can create workflows,
  navigate to operational surfaces, focus pending approvals, and open searchable
  event state; it does not bypass policy.
- Keyboard shortcuts mirror existing read/control surfaces without adding
  hidden mutation paths: `Ctrl+Alt+A` opens approvals, `Ctrl+Alt+E` opens the
  event ledger, and `Ctrl+Alt+W` navigates to workflows. Shortcuts are ignored
  while focus is inside editable form controls.
- Workflow inspectors read linked workflow runs, steps, workspaces, jobs, agent
  runs, tool calls, policy decisions, evidence packages, artifacts, test
  results, and approvals from `/api/v1/overview`.
- Evidence and workflow inspectors preview and download artifacts only through
  `GET /api/v1/evidence/{evidenceId}/artifacts/{artifactId}` with the local
  control token. The UI never reads local artifact paths directly.
- Policy & Security renders `policyRevisions` from `/api/v1/overview` as
  operator-readable diffs. The diff drawer shows field, before, and after
  columns so reviewers are not forced to inspect raw JSON or infer changes by
  color alone.
- Configuration changes must use strict forms with controlled inputs. Agent
  profiles, model policies, workflows, governance records, sandbox profiles,
  and MCP registry entries are edited through labeled inputs, selects,
  checkboxes, and numeric fields; raw JSON editing is not a supported operator
  path.
- Stable mutation helpers use generated OpenAPI `OperationRequestBody` and
  `OperationResponse` contracts. Form state for workflow kind, agent role,
  runtime mode, permission profile, risk severity, decision status, next-step
  priority, and MCP transport is typed against those generated unions.
- `pnpm run typecheck:web` runs `tsc --noEmit` against the Vite TypeScript
  project. This is separate from the Vite production build because esbuild
  transpilation alone does not prove the contracts are type-safe.

## Visual Direction

AIDO uses an operational control-room style: graphite, off-white, muted moss,
amber, and oxblood. The UI should read as an engineering ledger, not a SaaS
marketing dashboard. Avoid neon palettes, liquid-glass effects, decorative
glows, gradient text, and external font/CDN dependencies.

## Motion

Motion uses a small React/CSS layer, not a non-OSI animation runtime:

- section transitions;
- new events;
- approval drawer state;
- workflow focus;
- command-palette and drawer movement.

The implementation animates only `opacity` and `transform`, keeps transitions
short, and must respect `prefers-reduced-motion`. The smoke suite asserts the
reduced-motion path. `gsap`/`@gsap/react` were removed from core dependencies
because the package license is not OSI-compatible.

## Validation

```powershell
corepack pnpm@10.24.0 run build:control-center
corepack pnpm@10.24.0 run test:web
```

The Playwright suite covers strict configuration forms on desktop and mobile:
invalid ids must show inline errors, valid agent profiles, model policies,
workflows, governance records, sandbox edits, and MCP registrations must
persist through v1 APIs, and the configuration pages must not expose `textarea`
JSON editors.

The generated OpenAPI client lives in
`local-control-center/web/src/api/generated/openapi.ts` and is rebuilt with:

```powershell
corepack pnpm@10.24.0 run openapi:generate
```

CI fails if that generated file drifts from the FastAPI OpenAPI schema. The
client includes operation-id lookup, typed operation aliases, path parameter
interpolation, query serialization, loopback-token headers, and JSON error
handling. It also emits `OperationRequestBody<T>` and
`OperationResponse<T>` aliases from the OpenAPI schemas. The active API client
now routes JSON calls through generated operation IDs. Active v1 mutating
operations no longer generate `unknown` request bodies, and active v1 read
operations no longer generate raw `JsonObject` operation responses. Frontend
domain interfaces remain as intentional row-level refinements while backend
metadata fields continue to evolve; the generated client now owns the route
contract, and the feature models own UI-specific narrowing.
