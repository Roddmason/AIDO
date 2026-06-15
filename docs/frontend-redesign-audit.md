# AIDO Studio — Frontend Redesign Audit

> **Status:** Documentation only. No product code is changed by this document.
> **Branch:** `dev` · **Scope of code reviewed:** `local-control-center/web/src/**` + `tests_web/control-center.spec.js`
> **Target:** **AIDO Studio** — a modern, professional IDE-style shell with a workspace **gallery** Home and an IDE **Workbench**, organized around the loop *open folder → detect project → work → review diff/evidence → approve*.

---

## 1. Method

This audit was produced by reading the source directly and cross-checking with a parallel senior-agent audit (10 area auditors + an information-architecture/density critic + a must-preserve completeness critic), then reconciling every "cannot lose" claim against the Playwright contract (`tests_web/control-center.spec.js`, ~2157 lines / ~40 tests running against a live FastAPI v1 backend).

Files reviewed first-hand:

- `app/App.tsx`, `app/RadialNavigation.tsx`
- `features/pages.tsx` (CommandCenter, Workspaces, Policy, Memory, Evidence, ModelGateway-legacy, Governance, Integrations, Audit)
- `features/workbench/WorkbenchPage.tsx`, `features/active-projects/ActiveProjectsPage.tsx`, `features/agents/AgentsPage.tsx`, `features/jobs-approvals/JobsApprovalsPage.tsx`, `features/workflows/WorkflowsPage.tsx`, `features/model-gateway/*` (page + 11 panels), `features/settings/SettingsPage.tsx`, `features/overview/OverviewPage.tsx`
- `design-system/*.css`, `components/primitives.tsx`
- `api/client.ts`, `api/types.ts` (generated OpenAPI re-exports)
- `hooks/useControlPlane.ts`, `i18n/I18nProvider.tsx`, `i18n/TranslationMaintainer.tsx`, `motion/useControlMotion.ts`

---

## 2. Executive summary

The frontend is **operationally complete and technically solid**: a Vite/React/TypeScript SPA with a typed, OpenAPI-generated API client, a modern OKLCH dark/light token system, a command palette, drawers, strict (no-raw-JSON) forms, evidence-first approval gates, honest runtime-state reporting, secret redaction, ES/EN i18n, and broad Playwright coverage.

**The problem is not capability — it is information architecture and visual density.** Daily work (pick a workspace, run work, review gates) competes for attention with implementation detail (provider catalogs, routing policy, sandbox profiles, audit logs, MCP registration), because all of them are exposed as **peer navigation destinations**.

The redesign is also **already half-built**, which de-risks it:

- `WorkbenchPage` is already a 3-column IDE layout and is the **default landing route**.
- `tokens.css` already defines IDE shell sizing tokens (`--size-activity-bar`, `--size-explorer`, `--size-inspector`, `--size-status-bar`, `--content-max`) and an `.app-shell-ide` 4-column grid.
- The legacy **radial navigation CSS has already been removed** (the Playwright spec asserts `.rotor-ring` count `0`); only the orphaned `RadialNavigation.tsx` component remains.

The single biggest move is collapsing **25 page IDs / 28 sidebar buttons across 5 sections** into **5 primary destinations: Home · Workbench · Runs · Review · Settings**, and splitting the two density monoliths (Model Gateway, Workflows inspector) into tabs instead of porting them verbatim.

---

## 3. Current state

### 3.1 Architecture snapshot

| Aspect | Reality (verified) |
| --- | --- |
| Stack | Vite + React + TypeScript SPA, single global CSS design system loaded from `main.tsx` (no CSS modules / scoping). |
| Routing | `window.location.hash` → `PageId` switch in `App.tsx`; `routeAliases` map a few friendly slugs (`home`/`ide`/`workspace` → `workbench`, `active` → `projects-active`, `settings` → `settings-projects`). |
| Data layer | `useControlPlane` performs a **token handshake** + full `overview` fetch on mount, then **5s silent polling** (`setInterval`); `mutate(op,{awaitRefresh})` injects the `X-Local-Control-Token` write header and refreshes after writes. |
| "SSE" | **There is no SSE/WebSocket.** `state.connected` is initialized `false` and never set `true`; the "SSE connected" indicators always render the fallback. Transport is purely polling. |
| i18n | `I18nProvider` loads a runtime-editable ES/EN catalog from `/api/v1/i18n/catalog`, plus a body-wide `MutationObserver`/`TreeWalker` DOM-string localizer layered on top of keyed `t()`. `TranslationMaintainer` PUTs the catalog back without a rebuild. |
| Motion | `useMotionPreference` + `usePageMotion` honor `prefers-reduced-motion` (`<html data-motion="reduced">`, `window.__aidoMotionReduced`). |

### 3.2 Existing top-level page model

`App.tsx` (`pageIds`, lines 69–95) defines **25 page IDs**, rendered through a **5-section sidebar** (`navigationSections`, lines 368–428) carrying **28 nav buttons** — i.e. several buttons resolve to the same route:

| Section (label) | Buttons | Routes behind them |
| --- | --- | --- |
| Workspace | Workbench | `workbench` |
| Projects | Projects, Active, Finished, With error, Cancelled | `projects-active` (×2), `-finished`, `-error`, `-cancelled` |
| Operations | Command Center, Workflows, Jobs & Approvals, Agents, Workspaces, History | `command`, `workflows`, `jobs`, `agents`, `workspaces`, `audit` |
| Governance *(id: `knowledge`)* | Policy & Security, Memory & Retrieval, Evidence & QA, Model Gateway, Governance, Audit Log, Integrations | `policy`, `memory`, `evidence`, `models`, `governance`, `audit`, `integrations` |
| Settings | Settings, Project selection, User, CLI, API, Parameters, Mantenedores, Workspaces, Defaults | `settings-projects` (×2), `-user`, `-cli`, `-api`, `-parameters`, `-maintainers`, `-workspaces`, `-defaults` |

This is operationally complete but **too flat for an IDE** — the sidebar reads as a product sitemap, not an explorer.

### 3.3 Migration already underway (do not re-do)

- `WorkbenchPage` is the strongest IDE shape today: session rail + workspace selector + chat intake + delivery flow + progress + AI team + execution lanes + deliverables + session pipelines.
- `tokens.css` already carries IDE shell sizing tokens and an `.app-shell-ide` 4-column grid.
- Radial/wheel CSS classes (`.rotary-wheel`, `.rotor-ring`, `.wheel-*`) **no longer exist** in any CSS file; only `RadialNavigation.tsx` is left orphaned.
- `tokens.css` keeps an explicit *"Legacy aliases kept so current components keep compiling while the UI migrates"* block (lines 111–137).

### 3.4 Strengths worth preserving

- **Evidence-first review** (`JobsApprovalsPage`): per-action review drawer, full diff + security findings before approval, 13-check evidence-completeness gate, approve/reject with mandatory reason, promote-branch / create-PR with honest `pr_unavailable`.
- **Run forensics** (`WorkflowsPage`): run-centric correlation of steps/evidence/tests/artifacts/model-calls/tool-calls/policy/approvals/jobs/workspaces + merged timeline + completion-gap checklist + blockers.
- **Runtime honesty** (`AgentsPage`, Model Gateway, Command Center): separate Detected/Configured/Available/Executable states, `missing_config` with exact env-var, never "Ready" when not executable, `internal_mock`/`manual` excluded from selectable runtimes.
- **Token-protected evidence artifacts** with sha256/mime + secret redaction everywhere.
- **Design system**: modern OKLCH dark/light tokens + generic primitives (`Surface`, `DataTable`, `Drawer`, `Badge`, `StatusDot`, `EmptyState`, `PageHeader`) + accessibility baseline (`:focus-visible`, `.sr-only`, reduced-motion).

---

## 4. Problems

### 4.1 Navigation problems

| # | Problem | Evidence (file:line) | Redesign direction |
| --- | --- | --- | --- |
| N1 | **Too many first-level destinations.** 25 page IDs / 28 buttons / 5 sections. | `App.tsx:69-95`, `:368-428` | Collapse to **5**: Home, Workbench, Runs, Review, Settings (~5× reduction). |
| N2 | **Duplicate nav rows → same route.** "Projects" and "Active" both → `projects-active`; "Settings" and "Project selection" both → `settings-projects`. | `App.tsx:381-382`, `:417-418` | One canonical link per destination. |
| N3 | **Same screen under two labels in two sections.** `audit` appears as "History" (Operations) and "Audit Log" (Governance). | `App.tsx:397`, `:409` | Single Audit/History surface, reached from Runs/Review context. |
| N4 | **Four routes for one filtered list.** `projects-active/-finished/-error/-cancelled` all render `ActiveProjectsPage`, differing only by a `statusView` prop. | `App.tsx:71-74`, `:99-104`, `:455-467` | One **Home gallery** + status segmented control + URL state. |
| N5 | **Settings is a second hidden nav tree.** 8 `settings-*` routes + a 9-item settings nav section; 4 tabs (user/cli/api/parameters) are static read-only; `settings-defaults` is a route but **not** in `tabOrder` (dead as a tab). | `App.tsx:87-94`, `SettingsPage.tsx:176` | One Settings route with grouped sections; deep links only for palette/search. |
| N6 | **"Governance" section is a junk drawer.** 7 heterogeneous destinations (a config console, a retrieval store, a QA viewer, a CRUD console, a read-only log, an MCP registrar) under one label. | `App.tsx:400-412` | Re-group by operator intent into Review vs Settings. |
| N7 | **Entry-point sprawl.** The command palette re-exposes Workflows/Jobs/Governance/Policy/Events, and Workbench deep-links to command/workflows/jobs/workspaces/evidence — three parallel navigation systems. | `App.tsx:354-360`, `:447-451` | Workbench + palette invoke the **same** canonical actions; no third path. |
| N8 | **Misleading global status chrome.** Connection signal rendered twice, both bound to a `connected` flag that is never set true (no SSE exists). | `App.tsx:558`, `:603`; `useControlPlane.ts:30` | Single honest "live (polling) · updated HH:MM:SS" indicator from `lastUpdatedAt`. |

### 4.2 Visual density problems

| # | Problem | Evidence | Redesign direction |
| --- | --- | --- | --- |
| D1 | **Model Gateway is the worst offender:** ~18 stacked `Surface` sections on **one non-tabbed route**, framed by a 14-tile KPI strip + a 6-tile "Settings" strip; tables reach **19 / 16 / 13 / 12 columns** (Usage Ledger / Runtime Providers / Provider Accounts / Role Assignments) → guaranteed horizontal overflow. All 15 datasets eager-load via one `Promise.all` on mount. | `ModelGatewayPage.tsx` | Split into Settings sub-tabs (Providers / Catalog / Routing & Policies / Budgets & Limits) + move telemetry to Runs; lazy-load per tab. |
| D2 | **Workflows inspector drawer stacks ~16 Surfaces / ~14 DataTables** for one run, with three competing views (a *structurally fake* React Flow graph — linear `nodes[i]→nodes[i+1]`, capped at 12 — a merged timeline, and a dozen raw tables). | `WorkflowsPage.tsx` | Tabbed run-detail (Overview & gates / Timeline / Steps & agents / Evidence & tests / Artifacts / Policy & approvals / Jobs & runtime); drop or rebuild the fake DAG. |
| D3 | **Jobs review drawer is a scroll wall:** badges → 6 detail rows → raw `prettyJson` of evidenceRefs/diffRefs/runtime → linked-evidence table → linked-artifacts table → 13-item checklist as a `<pre>` → full diff `<pre>` → security `<pre>` → only then the decision textarea + buttons. | `JobsApprovalsPage.tsx` | Structured "Run health" evidence panel with grouped pass/fail + deep links; decision controls reachable without scrolling past raw JSON. |
| D4 | **Badge soup in status tables:** 4 boolean badges per row (detected/configured/available/executable) across Agents + Model Gateway + asserted by E2E. | `AgentsPage.tsx`, `ModelGatewayPage.tsx` | Single composite state indicator + expandable detail. |
| D5 | **Dashboard-itis / duplicate KPIs:** Model Gateway renders `money(totalCost)` twice (Settings tile + Cost-ledger card); its 14-tile strip competes with the dedicated `OverviewPage` and the 6-tile Settings strip. Active Projects shows 4 global KPI cards + 2 secondary tables that restate them — unchanged even on the Cancelled view. | `ModelGatewayPage.tsx`, `OverviewPage.tsx`, `ActiveProjectsPage.tsx` | Summary strip → master list → inspector; remove duplicate tiles. |
| D6 | **Raw JSON as user-facing UI:** routing-profile rules, provider metadata, route-preview budget/quota results, evidenceRefs/diffRefs dumped into `<div class="mono">`/`<pre>`. | Model Gateway, Jobs, Workflows | Human-framed fields; raw JSON behind a "developer details" disclosure. |
| D7 | **Design tokens fight density:** every control is hard-locked to `2.75rem`/44px (`button/input/select/nav-item`), `.session-item` 4rem, `.command-item` 3.5rem; `.data-table` global `min-width: 48rem` (44rem mobile). No compact density dimension exists. | `components.css`, `layout.css` | Add `--control-height-sm/md` density tokens so Runs/Review/Settings tables can run compact. |
| D8 | **"Console" aesthetic as default:** `*-card-meta`, `signal-card`, `nav-section-label`, `page-kicker`, `diff-head`, `data-table th` force `text-transform:uppercase` + mono + bold small text on every surface; `.console-grid` overlay is always visible. | `components.css`, `layout.css` | Make uppercase/mono/grid opt-in tokens so Studio reads cleaner; calmer content-first Home. |

### 4.3 Bureaucratic flow problems

Each is real friction; **the underlying guarantee must be kept** — the fix is presentation, not removal.

| # | Flow | Why it's bureaucratic | Fix (preserve the contract) |
| --- | --- | --- | --- |
| B1 | **Cross-page project gate.** Governance writes and Command Center runs are blocked until an operational project is selected — in **Settings**, on a different route. | Save handlers short-circuit with "no operational project"; the only unblock lives elsewhere with no inline picker. (`pages.tsx:284-290`, `:370`, `:1313`) | Inline project picker in the page header; Home "open workspace" sets the operational target. Carry selection in the shell. |
| B2 | **Three separate reason fields** on Jobs & Approvals (decision / job / workflow-operation), overlapping semantics, no persistence. | `decisionReason`/`jobReason`/`workflowOperationReason` are independent; operators re-type the same justification 3×. | Keep the audited-reason requirement; unify into one reason model with per-action presets, pre-filled within a flow. |
| B3 | **Shipping one patch = multi-gate, multi-reason, multi-call ceremony:** review → read diff → read security → 13 green checks → reason → Approve → (separate) Promote + reason → (separate) Create PR + reason. Approve silently fires two API calls. | A transient artifact-fetch error silently disables Approve; each stage re-types a reason. | Keep the evidence-first gate + status guards (E2E-locked). Render as one linear "Run delivery" stepper inside Runs with a carried-forward reason and explicit "why disabled". |
| B4 | **13-check completeness list** as a newline-joined `<pre>` the operator must read before Approve enables; duplicates human + machine verification. | No grouping/icons/severity; reads as prerequisites, not next steps. | Structured grouped panel; each failed check deep-links to the exact missing artifact. |
| B5 | **4-step New Project wizard** where step 2 (template = one select) and step 3 (directory = one usually-disabled checkbox) spend full steps on near-zero decisions; "Detect technologies" is redundant (discovery auto-runs on folder pick). | Uneven step distribution; identity step overloaded. | Collapse to ~2 meaningful screens (identify/import-or-create → review). Keep both flows, validation, final-path preview, duplicate-name guard. |
| B6 | **Strict-profile-first agent creation:** ~14 flat fields, Save hard-disabled until 4 discovery catalogs all load; no quick/edit/clone path; single-select tool/provider/runtime. | Whole form blanks if any catalog fails. | Keep the `catalogAvailable` safety gate + governance contract; group fields with progressive disclosure; add edit/clone; disable only the affected fields on partial catalog failure. |
| B7 | **Per-row, one-at-a-time provider ops** (enable/disable, health, discover, refresh) each calling full `reload()`, no bulk. | N providers = N clicks + N full re-hydrations (15-endpoint `Promise.all`). | Keep the real lifecycle controls; add bulk multi-select + targeted optimistic updates (helpers already exist); lazy-load panels. |
| B8 | **Manual benchmark entry** (6-field form) whose own UI disclaims output as "not objective proof". | Friction producing data the surface distrusts. | Demote to secondary, progressively-disclosed action; keep honest "operator-reported" labeling; prioritize objective capture from real runs. |

---

## 5. Reusable components

**Keep and evolve (do not introduce a new UI framework):**

- Primitives: `PageHeader`, `Surface`, `DataTable<T>`, `Drawer` (Escape + `aria-modal` + scrim), `Badge`, `StatusDot`, `EmptyState` (`components/primitives.tsx`).
- Form primitives: `form-grid`, `field`, `field-help`, `field-label`, `input`, `textarea`, `select`, `checkbox-row`, `form-error`, `form-success`.
- Review/evidence primitives: `artifact-preview`, `diff-grid`/`diff-row`, `review-grid`, `workflow-timeline-*`.
- Workbench primitives: `workbench-layout`, `workbench-session-rail`, `chat-transcript`, `delivery-rail`, `session-item`, `command-list`/`command-item`/`command-kbd`.
- Tabs: `tabs`/`panel-tabs`/`panel-tab` (`aria-selected`) — reuse for Settings + inspector tabs; do **not** duplicate tabs as sidebar routes.
- Shell tokens: `.app-shell-ide` 4-col grid + IDE sizing vars → Workbench layout.
- Cross-cutting hooks/providers: `useControlPlane` (state/polling/token/mutate/refresh), `I18nProvider`/`useI18n`, `useMotionPreference`/`usePageMotion`, the typed `apiRequest`/`requestGeneratedOperation` client, `lib/format` (`toneForStatus`, `countByStatus`, `shortId`), `lib/redaction`, `lib/diff`, `lib/artifacts`.

**Evolve / consolidate:**

- Collapse the 4 near-duplicate card classes (`.workspace-card`/`.task-card`/`.run-card`/`.setting-card`, differing only by a 3px left border) into one `.card` + tone modifier.
- Replace `.masonry-grid` CSS-columns (breaks reading/tab order) with real CSS grid for the Home gallery.
- Add the missing `Badge`/`StatusDot` **`info`** tone rule (primitives accept `info`; `components.css` only styles ok/warn/danger).
- Extract the verbatim-duplicated `mergeNewestById`/`upsertNewestById`/`recordTimestamp` (in `pages.tsx`, `AgentsPage.tsx`, `ModelGatewayPage.tsx`) into one shared util.
- Replace `PanelShell.tsx` (a pass-through around `Surface title`) and the duplicate local `Metric`/`money` (page-local vs `utils.tsx`) with single shared versions.

**Do not reuse (disallowed in AIDO Studio):** `RadialNavigation` component + `RadialNavigationOption`/`Module` types. Its CSS (`.radial-nav-list`, `.wheel-*`, `.rotary-wheel`, `.rotor-ring`) is already deleted.

---

## 6. Real API inventory — *reuse, do not reinvent*

All endpoints are the typed, generated v1 OpenAPI operations exposed through `api/client.ts`. The redesign must keep every one reachable. **No mock/demo data in product flows.**

**Reads (GET):** `getHandshake` (security token), `getOverview` (the aggregate feeding every page), `getRetrievalStatus`, `getRuntimeProviders`, `getRuntimeProviderConfiguration`, `getEvidenceDetail`, agent statuses (`getDeveloperAgentStatus`, `getDevOpsAgentStatus`, `getArchitectAgentStatus`, `getSecurityAgentStatus`), `getI18nCatalog`, `listProjects`, `listProjectTemplates`, `listProviders`, `listTeams`, `listAgents`, and the full Model Gateway read set (`getModelGatewayOverview`, `…Providers`, `…Models`, `…RoutingProfiles`, `…RolePolicies`, `…UsageLedger`, `…UsageSummary`, `…RoutingDecisions`, `…ProviderLimits`, `…BudgetRules`, `…CliRuntimes`, `…CliSessions`, `…Benchmarks`, `…BenchmarkOutcomes`).

**Writes (POST/PATCH/PUT, token-gated via `X-Local-Control-Token`):**

- Projects/workspaces: `createProject`, `discoverProject`, `selectLocalDirectory`.
- Work intake: `createSession`, `createChat`, `createPipeline`, `createWorkflow`/`createWorkflowWithBody`.
- SDLC workflows: `runIssueToPatch`, `runIssueToPr`, `approveIssueToPatch`, `approveIssueToPr`, `promotePatchToBranch`, `promoteIssueToPrBranch`, `createPullRequestFromPromotedBranch`, `createPullRequestFromIssueToPr`.
- Jobs/approvals: `approveAction`, `denyAction`, `cancelJob`, `retryJob`.
- Agents: `runDeveloperAgent`, `runDevOpsAgent`, `runQAAgent`, `runSecurityAgent`, `runArchitectAgent`, `createAgentProfile`.
- Governance/policy: `createRisk`, `updateRisk`, `createArchitectureDecision`, `createNextStep`, `updateSandboxProfile`.
- Integrations: `registerMcpServer`.
- Model Gateway: `createModelGatewayRolePolicy`, `previewModelRoute`, `patchModelGatewayProvider`, `healthCheckModelGatewayProvider`, `discoverModelGatewayProviderModels`, `detectModelGatewayCliRuntime`, `recordModelGatewayBenchmarkOutcome`.
- i18n: `updateI18nCatalog`.
- Evidence artifacts: `fetchEvidenceArtifact` / `downloadEvidenceArtifact` → `GET /api/v1/evidence/{id}/artifacts/{id}` (returns `X-AIDO-Artifact-Id/Hash`, `Content-Disposition`).

---

## 7. Legacy & dead-code inventory — *eliminate (in a later code task)*

These are safe-to-remove or migration-debt items found during the audit. **Per project guardrails, radial deletion and token migration should be their own verified change, not bundled into the Studio redesign.**

| Item | Evidence | Action |
| --- | --- | --- |
| **`RadialNavigation.tsx` (+ its types)** | Imported nowhere outside itself; its CSS is already gone. | Delete component + `RadialNavigationOption`/`Module` types. |
| **Duplicate `ModelGatewayPage` in `pages.tsx`** | `App.tsx` imports the dedicated `features/model-gateway/ModelGatewayPage`; the `pages.tsx` export (lines 1066–1262) is **unused**. | Delete the `pages.tsx` copy. |
| **Fake "SSE connected" indicators (×2)** | `state.connected` never set true; no EventSource/WebSocket exists. | Replace both with one honest "live (polling)" + `lastUpdatedAt` freshness label. |
| **Integrations "IDE connections" table** | Client-side filter of `auditEvents` for `action.includes('ide')`; no real connections API; overlaps Audit. | Drop, or back with a real API before surfacing. |
| **`ProviderAccountsPanel` "Cost today" column** | `render: () => 'cost unavailable'` for every row. | Remove the dead column. |
| **Duplicate cost tiles (Model Gateway)** | Settings "legacy model usage total" and "Cost ledger" both render `money(totalCost)`. | Keep one. |
| **Legacy token alias block** | `tokens.css:111-137` (`--ink`, `--moss`, `--line`, `--surface-page`, …) still consumed by ~18 call sites in `base/layout/components.css` + `WorkflowsPage.tsx`. | Port call sites to canonical `--color-*`, then delete. |
| **`--radius-lg` / `--radius-xl`** | Both `0.5rem` (xl is a no-op alias). | Collapse. |
| **DOM-mutation localizer** | Body-wide `TreeWalker`+`MutationObserver` rewriting text by exact-string match, layered over `t()`; risks mistranslating user content + continuous observer cost. | Migrate fully to keyed `t()`; **keep** the runtime-editable catalog + `TranslationMaintainer`. |
| **Duplicated helpers** | `mergeNewestById`/`upsertNewestById`/`recordTimestamp` copied in 3 files; `Metric`/`money` defined twice; `PanelShell` pass-through. | Extract to shared utils. |
| **i18n gaps** | "Mantenedores" hardcoded in both en+es maps; "Workspace name already exists." untranslated in es; `document.documentElement.lang` set in both `I18nProvider` and `App.tsx`. | Fix translations; remove the duplicate effect. |
| **`ActiveProjectsPage` status column** | Hardcodes `Badge tone='ok'` for every row (error/cancelled show green). | Use `toneForStatus(status)`. |
| **`SettingsPage` synthesized workspace rows** | Fabricated `taskId:'project-root'` fallback rows masquerade as runtime workspaces. | Mark clearly as derived, or drop. |

---

## 8. New AIDO Studio map

Five primary destinations. Cross-cutting guarantees (Section 10) hold in **every** section.

### 8.1 Home — *workspace gallery & landing*
- Card-based project/workspace gallery (replace `.masonry-grid` columns with CSS grid).
- One status **segmented control** (active / finished / error / cancelled) + search by name/path/template/status (replaces the 4 project routes).
- Compact operational stats: pending approvals, running jobs, latest evidence, runtime health.
- Quick actions: **open workspace** (sets operational project), **import/create workspace** (wizard as a focused overlay), open recent run.
- **Preserve:** active-only mutation guard; ignore stale `aido:selectedProjectId`; visibility of finished/error/cancelled for audit; no fake/demo workspace records.

### 8.2 Workbench — *canonical IDE work surface for one workspace*
- Explorer rail: workspace root, sessions, recent artifacts.
- Center: chat intake → task prompt → delivery flow → current progress.
- Right inspector: AI delivery team, runtime status, execution lanes, deliverables/evidence, session pipelines.
- Inline project context (resolves B1) + command-palette parity.
- **Preserve:** chat intake creates real chat+session+pipeline linked to the project; governed `issue_to_patch` with runtime selection + QA gate; explicit `runtime_unavailable`; links to approvals/evidence/workflows/workspaces.

### 8.3 Runs — *execution observability & technical trace*
- Master list of workflows/runs + **tabbed run-detail** (Overview & gates / Timeline / Steps & agents / Evidence & tests / Artifacts / Policy & approvals / Jobs & runtime).
- Absorbs: Command Center run-launch, the Jobs **queue** (retry/cancel/lease), the promote/PR **delivery** actions (in-context "Run delivery" stepper), and per-execution telemetry from Model Gateway (Usage Ledger / Routing Decisions / CLI Sessions), plus Audit/History as a filterable activity view.
- **Preserve:** run-centric correlation; completion-gap checklist + blockers; per-step gate overlay; token-protected artifact preview/download; secret redaction; chronological timeline; model-call cost/token & QA-verdict visibility; honest `runtime_unavailable`; `internal_mock`/`manual` never selectable.

### 8.4 Review — *human gates, evidence, QA, governance decisions*
- Pending-approvals **inbox** + structured evidence panel (command scope, argv, runtime, workspace, policy reason).
- Full diff + security findings + evidence/QA tabs before approval; approve/reject with reason; promote/PR after evidence-backed approval.
- Governance (risks/ADRs/next steps) + Policy revision diffs + Audit as decision-supporting tabs.
- **Preserve:** approving one action never approves others; patch approval blocked without complete evidence + real diff + non-blocking security findings; reject stays lighter (reason-only); honest `pr_unavailable`; policy/sandbox revision diffs auditable; both `issue_to_patch` and `issue_to_pr` families.

### 8.5 Settings — *grouped configuration, not the default flow*
- Groups: Projects & Workspaces · User Preferences · Runtime & Models (Model Gateway config split into Providers / Catalog / Routing & Policies / Budgets & Limits) · Agents · Policy & Sandbox · Integrations · Catalog Maintainers (+ TranslationMaintainer behind disclosure) · Memory/Retrieval diagnostics · API/CLI Diagnostics · Defaults (collapsed).
- **Preserve:** new-project wizard (both flows) — best surfaced from Home but authoritative project guard stays here; manifest/runtime detection; native directory picker + manual fallback; duplicate-name blocking; strict agent/model-policy/sandbox/MCP forms (no raw JSON); runtime/provider status without secrets; sandbox update requires reason; defaults collapsed.

---

## 9. Page mapping (current → new)

| Current route / component | New home | Notes |
| --- | --- | --- |
| `workbench` / `WorkbenchPage` | **Workbench** | Promote as the core Studio experience. |
| `projects-active/-finished/-error/-cancelled` / `ActiveProjectsPage` | **Home** | One gallery + status segmented control; selecting a card = "open workspace". |
| `command` / `CommandCenterPage` | **Workbench** command panel + **Runs** (run launch) + palette | Not a top-level page. Preserve strict runtime/QA gates + honest `runtime_unavailable`. |
| `workflows` / `WorkflowsPage` | **Runs** | Tabbed run-detail; drop/rebuild the fake DAG. |
| `jobs` / `JobsApprovalsPage` | **Review** (approvals) + **Runs** (queue + promote/PR) | Split human-decision vs queue/delivery. |
| `agents` / `AgentsPage` | **Settings > Agents**; runtime matrix → **Settings > Runtime** (or Home status widget) | Creation = config; status = diagnostics. |
| `workspaces` / `WorkspacesPage` | **Settings > Projects & Workspaces** + **Runs** inspector | Allocation records are run context, not top-level nav. |
| `policy` / `PolicySecurityPage` | **Review > Policy** (decisions/revisions) + **Settings > Policy & Sandbox** (editable profile) | |
| `memory` / `MemoryPage` | **Settings > Memory/Retrieval diagnostics** | Retrieval health is diagnostic. |
| `evidence` / `EvidencePage` | **Review > Evidence** + **Runs** inspector | Keep diff viewer + redaction + token-protected preview. |
| `models` / `ModelGatewayPage` (folder) | **Settings > Runtime & Models** (config) + **Runs** (telemetry) | Split the 18-section monolith. |
| `governance` / `GovernancePage` | **Review > Governance** | Risks/ADRs/next steps support decisions. |
| `audit` / `AuditPage` | **Runs/Review** activity view | Filterable, keyboard-dismissable; reachable from approvals/runs. |
| `integrations` / `IntegrationsPage` | **Settings > Integrations** | Drop the dead "IDE connections" table. |
| `settings-projects` | **Settings > Projects & Workspaces** | Default Settings section. |
| `settings-user` | **Settings > User Preferences** | Language/density/motion. |
| `settings-cli` / `settings-api` | **Settings > API/CLI Diagnostics** | |
| `settings-parameters` | **Settings > Runtime & Models** | Model-policy parameter table. |
| `settings-maintainers` | **Settings > Catalog Maintainers** | Templates/providers/teams/agents + TranslationMaintainer. |
| `settings-workspaces` | **Settings > Projects & Workspaces** | IDE workspace roots. |
| `settings-defaults` | **Settings > Defaults** | Collapsed by default (note: currently dead as a tab). |

---

## 10. Functionalities that cannot be lost

Non-negotiable: these protect backend contracts, runtime truth, security, QA evidence, accessibility, or are pinned by the Playwright spec. (Consolidated and de-duplicated; the four surfaces the per-area audits nearly missed — **Command Center, Policy & Security, Memory & Retrieval, Evidence & QA** — are explicitly included.)

**Data & shell contracts**
- API-driven data only; **no mock/demo/fake-success** in product flows.
- Generated v1 OpenAPI client usage (Section 6) and typed `mutate(op,{awaitRefresh})` + `refresh(silent)` signatures.
- Token handshake → `X-Local-Control-Token` write header on every mutation.
- 5s polling + post-mutation refresh; resilient loading (optional `retrievalStatus`/`runtimeProviders` non-fatal via timeout→null; silent-poll errors keep last-good data); `AbortController` cancellation on unmount; `lastUpdatedAt` freshness.
- Operational-project selection restricted to **active-only**, persisted (`aido:selectedProjectId`), owned by Settings; Workbench landing shows only active projects and ignores stale storage.

**Work intake & runtime honesty**
- Workbench chat intake creating a linked chat+session+pipeline; landing headings preserved.
- Command Center `issue_to_patch` launcher: active project + executable runtime (excludes `manual`/`internal_mock`) + non-empty QA preset (`qa_not_selected` disables Run) + title/text gating; honest `runtime_unavailable` + timeline.
- Honest provider states: separate Detected/Configured/Available/Executable, `missing_config` with exact env-var, version/last-error/reason; never "Ready" when not executable; `internal_mock` string never appears.

**Approvals, runs, evidence**
- Evidence-first approval gating: Approve disabled until full diff + security findings seen, `evidence_complete`, **and** a recorded reason; the 13-check completeness gate; Reject stays lighter (reason-only); per-action isolation (approving one never approves others).
- `issue_to_patch` **and** `issue_to_pr` lifecycle: two-call approve chain, status-gated promote/PR, honest `pr_unavailable` + "AIDO_GITHUB_TOKEN is missing."
- Job queue retry/cancel with mandatory reason + lease visibility.
- Run-centric correlation + completion-gap checklist + blockers + per-step gate overlay + chronological timeline + model-call cost/token + QA-verdict/diff-summary.
- Evidence & QA: persisted test-result records; token-protected artifact preview/download (content + mime + sha256 + binary fallback); full evidence detail (Workflow/Job/Agent-run links, verdict, patch hash, Diff viewer, Security findings, Model & tool calls).
- Diff truthfulness: empty patches → "no real changes / changed files 0"; malformed non-unified patches never count as real changes.
- **Secret redaction everywhere** (`[redacted]`/`[redacted_secret]`): provider errors/base-URLs/metadata, evidence model/tool-call metadata, blocker reasons, job-run summaries, route preview, runtime reasons, artifact bodies.

**Governance, policy, memory, integrations, agents, models**
- Governance project-scoped CRUD via real endpoints (severity-conditional mitigation; accepted-decision context+text) + filters + risk-status transitions.
- Policy & Security: tool-call execution state (`not_executed`/`allowlisted_diagnostic`) + viewable sandbox **policy-revision diffs** gated behind a mandatory "Sandbox update reason".
- Memory & Retrieval: backend status, "SQLite is canonical", memory items.
- MCP registration: strict id regex + shell-operator rejection + stdio-only + broker/policy/sandbox contract (do not loosen).
- Agent profile governance model (role + runtimeMode + permissionProfile + routing/role-policy + allowed providers/runtimes/tools + token/cost limits + remote/cli/api flags); slug + numeric validation; `catalogAvailable` hard-gate; `configuration_required` surfacing; runtime detection matrix + CLI healthcheck.
- Model Gateway: provider lifecycle (enable/disable/health/discover); route preview with candidate scoring + budget/quota/unknown-cost outcome (no credentials); usage ledger + routing-decisions audit (with filters); role/model policy creation; budget rules + provider limits; model catalog with pricing/capability flags; model-calls/cost from real traces; benchmark provenance honesty ("operator-reported", never fabricated 100%).
- Strict no-raw-JSON forms (`textarea[data-json-editor]` count must stay 0) with slug validation + inline errors across agents/model-policies/governance/sandbox/MCP, persisting the exact typed field sets the spec asserts.

**Cross-cutting UX / a11y / i18n (must hold in every section)**
- IDE primary nav as `role=navigation` grouped into exactly **5** `.nav-section` groups, `aria-current="page"` on active, persistent across context; `.ide-nav`/`.workbench-layout`/`.console-grid` present; **no** `.rotor-ring`.
- ≥44×44px touch targets on nav items; no horizontal overflow at 375px; `prefers-reduced-motion` honored; dark theme; `:focus-visible` ring; `.sr-only`; ARIA roles for navigation/dialog/table/list; `aria-live="polite"` result regions.
- Command palette (searchable) + keyboard shortcuts (Ctrl+Alt+A/E/W) + Event/Approval drawers (filterable, Escape-to-close) — mouse-free entry points.
- Full ES/EN i18n: runtime-editable catalog (GET/PUT, token-protected), localStorage persistence, `<html lang>` sync, `t()` fallback chain, language auto-reset, `TranslationMaintainer` (PUT-without-rebuild); **localized validation messages** (close the current "Mantenedores"/"Workspace name already exists." gaps).
- Design-system foundation: OKLCH dual dark/light tokens + full semantic ramps + spacing/radius/shadow/type/motion/z-index scales + generic primitives + responsive `table-wrap`/`data-label` + tabs/`aria-selected` + command-palette pattern + `.app-shell-ide`.
- Optimistic upsert + merge-by-newest reconciliation; shared enum sets + `toneForStatus` mapping.

---

## 11. Before / After

| Before | After |
| --- | --- |
| Sidebar is a 25-route product sitemap (28 buttons, 5 sections, 3 duplicate pairs). | 5 primary destinations + contextual explorer/inspector. |
| Projects = four route-level status pages (same component). | Home = workspace gallery with a status segmented control. |
| Command Center is its own page; 3 parallel entry systems. | Workbench owns execution; palette/Runs invoke the same actions. |
| Runs, jobs, evidence, approvals are separate mental models. | Runs = execution trace; Review = human decisions + evidence gates. |
| Model Gateway = 18-section infinite scroll; Agents primary. | Config in Settings (sub-tabs); telemetry in Runs; runtime truth in operational summaries. |
| Governance/audit/policy/integrations/memory are primary peers. | Grouped into Review / Settings by operator intent. |
| Dense tables + badge soup + raw JSON dominate scanning. | Cards for discovery; tables for records; tabs/panels for deep evidence; composite state chips. |
| Permanently-false "SSE connected" chrome (×2). | Honest "live (polling) · updated HH:MM:SS". |
| Radial component + duplicate ModelGatewayPage linger in the tree. | Removed in a later, separately-verified cleanup task. |

---

## 12. Design-system evolution

1. **Keep** the OKLCH token core + dual-theme mechanism as the single source of truth; finish porting the ~18 legacy-alias call sites, then delete the alias block.
2. **Add a density dimension** (`--control-height-sm/md`, density spacing tokens) so Workbench/Runs/Review/Settings data views run compact while Home stays comfortable (today everything is locked to 2.75rem/44px).
3. **Consolidate** the 4 card classes into one `.card` + tone; replace `.masonry-grid` CSS-columns with CSS grid (fixes Home reading/tab order).
4. **Reduce the console signature:** make uppercase/mono labels and `.console-grid` opt-in tokens rather than the default on every meta row.
5. **Harden `primitives.tsx`** into the contract layer: add the missing `info` tone; grow shared shell pieces (PanelHeader, Toolbar, Tabs, CommandPalette) the activity-bar/explorer/inspector CSS already implies.
6. **Retire** `RadialNavigation` entirely.

---

## 13. Implementation guardrails (for the later code phase)

- Do not rewrite backend contracts for UX convenience.
- Do not remove or weaken evidence gates, honest runtime states, secret redaction, or diff-truthfulness.
- Do not replace strict forms with JSON editors (`textarea[data-json-editor]` must stay 0).
- Do not introduce demo/mock data to fill empty states.
- Do not put advanced configuration on the first-run happy path.
- Do not drop keyboard paths, ARIA labels, reduced-motion, dark theme, or the 375px no-overflow / ≥44px touch-target guarantees.
- Keep the 5 `.nav-section` structure + `aria-current` + the asserted nav set, or update the Playwright spec deliberately in the same change.
- **Do not delete radial code or migrate legacy tokens in the same change as the Studio redesign** unless that task explicitly includes the cleanup *and* its verification.
- Keep URL deep links for tabs/actions that existing tests or operator workflows depend on.

---

## 14. Verification expected (for the later code phase)

Minimum bar:

- `corepack pnpm@10.24.0 run typecheck:web`
- `corepack pnpm@10.24.0 run build:control-center`
- `corepack pnpm@10.24.0 run test:web`
- Focused Playwright updates for: Home gallery filters + active-project selection; Workbench command-panel/palette parity; Runs tabbed inspector preserving evidence/QA/diff/runtime; Review approval gate preserving evidence-first blocking; Settings grouping preserving strict forms + collapsed defaults; mobile no-horizontal-overflow + reduced-motion.
- Re-confirm the negative/honesty contracts: no `internal_mock` in DOM, `manual`/`internal_mock` excluded from runtime selectors, `.rotor-ring` count 0, `textarea[data-json-editor]` count 0, secret redaction, honest `pr_unavailable`.
