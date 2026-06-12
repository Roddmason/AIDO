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
- Workflows graph, step table, agent run list, and evidence detail are scoped to
  the selected/default workflow. The page must not mix a selected workflow with
  global step rows.
- The Command Center renders runtime/provider state from
  `/api/v1/runtime/providers`, including unavailable reasons, required
  configuration, and executable state. The `issue_to_patch` form requires an
  explicit project, issue title, issue text, executable provider with
  DeveloperAgent `code_edit` capability, QA preset, and approval setting.
  It must not list `internal_mock` or non-executable providers. When no
  executable runtime exists it shows `runtime_unavailable` with the technical
  reason and disables submission. Results show the workflow timeline plus
  evidence, diff, and workflow links derived from the API response.
- Model Gateway owns the `Runtime & Model Gateway` table for runtime truth. It
  renders provider `id`, `kind`, `configured`, `available`, `executable`,
  capabilities, reason, version, and detected command directly from
  `/api/v1/runtime/providers`, merges missing configuration names from
  `/api/v1/runtime/provider-configuration`, never labels a non-executable
  provider as ready, and redacts secret-like values before rendering text.
  Refresh actions must call the real backend health/detection endpoint for that
  provider type.
- Evidence and workflow inspectors preview and download artifacts only through
  `GET /api/v1/evidence/{evidenceId}/artifacts/{artifactId}` with the local
  control token. The UI never reads local artifact paths directly.
- Evidence & QA owns the evidence detail viewer. Selecting a package calls
  `GET /api/v1/evidence/{evidenceId}` and renders package metadata,
  workflow/job/agent links, QA verdict, artifact SHA-256 hashes, diff patch
  text, security findings, model calls, tool calls, policy decisions, and
  approvals. Diff text and findings are fetched through the artifact download
  endpoint, not from local paths. Secret-like values are redacted before
  rendering, and an empty patch is shown as `no real changes`, never as a
  successful code change.
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
- Governance records can be filtered in the dashboard, and risk status updates
  use the typed `PATCH /api/v1/risks/{id}` helper instead of manual API or JSON
  edits.
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

## Real Capability Table

| Capability | Real state | Endpoint/UI | Tests | Limitations |
| --- | --- | --- | --- | --- |
| Operational dashboard | Implemented Vite/React/TypeScript console served by FastAPI after build. | Dashboard root, `/api/v1/overview`. | Playwright control-center suite, production build, typecheck. | The UI renders backend state; it does not invent policy, provider, or completion outcomes. |
| Command Center `issue_to_patch` | Implemented form and result viewer using runtime truth and workflow API. | Command Center, `POST /api/v1/workflows/issue-to-patch`. | `tests_web/control-center.spec.js` runtime tests. | The submit button stays disabled without executable provider and required form fields. |
| Runtime & Model Gateway view | Implemented provider table and configuration state rendering. | `GET /api/v1/runtime/providers`, `GET /api/v1/runtime/provider-configuration`. | Web provider status/configuration tests. | Non-executable providers must not be labeled ready. Secret-like values are redacted. |
| Evidence viewer | Implemented package detail, artifact preview/download, hashes, diff/security/model/tool-call rendering. | Evidence & QA UI, evidence detail/artifact endpoints. | Web evidence tests and backend evidence tests. | Artifact content is fetched through authenticated API, not local file paths. |
| Typed API client | Implemented generated OpenAPI operation helpers plus UI-specific refinements. | `local-control-center/web/src/api/generated/openapi.ts`. | `openapi:generate`, typecheck, OpenAPI drift tests. | Regenerate after backend schema changes. |
