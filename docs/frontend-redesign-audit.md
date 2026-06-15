# AIDO Studio — Frontend Redesign Audit

> **Status:** Documentation only. No product code is changed by this document.
> **Branch reviewed:** `feature/runtime-setup-panel` · **Date:** 2026-06-15
> **Scope of code reviewed:** `local-control-center/web/src/**` + `tests_web/control-center.spec.js` + `tests_web/command-palette.spec.js`
> **Target:** **AIDO Studio** — a modern, professional IDE-style shell with a workspace **gallery** Home and an IDE **Workbench**, organized around the loop *open folder → detect project → work → review diff/evidence → approve*.

---

## 0. Headline: this is a *finish-the-migration* audit, not a *start-it* audit

The previous version of this document (authored on `dev`) audited a **pre-migration** frontend (radial navigation, a flat 25-route sidebar, `RadialNavigation.tsx`, `OverviewPage`, `CommandCenterPage`) and *proposed* the AIDO Studio map. **The team has since built most of that map on this branch.** Auditing the old "before" as if it were current would be fiction, so this rewrite audits the **frontend as it actually is today**.

What is now **built and on disk** (verified first-hand):

- An IDE shell — `AppShell` renders `ActivityBar` + `ExplorerPanel` + `InspectorPanel` + `StatusBar` + `WorkbenchHeader` (`app/AppShell.tsx`).
- The **five primary areas** the redesign called for, defined once in `app/navigation.ts` as `AREAS`: **Home · Workbench · Runs · Review · Settings** (`navigation.ts:112-159`).
- A workspace-gallery **Home** (`features/home/HomePage.tsx`), an IDE **Workbench** with 5 tabs (`features/workbench/`), a kanban **Review board** (`features/review/ReviewPage.tsx`), a **command palette** (`app/CommandPalette.tsx` + `app/commandActions.ts`), and a new **runtime-setup** surface (`features/runtime-setup/`).
- `RadialNavigation.tsx`, `OverviewPage.tsx`, `CommandCenterPage` **no longer exist**; the Playwright contract asserts `.rotor-ring` count `0`.

So the structural redesign is **~85% done**. The remaining work is of two kinds, and this audit is organized around them:

1. **Finish-out / regressions** — the migration is mid-flight and the tree **does not typecheck** (Section 4.0). The Settings area is broken; the new `RuntimeSetupPanel` is orphaned; legacy dense pages were dropped wholesale into route slots without adopting the shell.
2. **Residual density & bureaucracy debt** — Model Gateway, Workflows, Jobs & Approvals, Agents and the `pages.tsx` monolith are still the legacy CommandCenter-era surfaces; they carry the real (must-preserve) capability but present it as walls of wide tables, raw JSON and multi-gate ceremony.

---

## 1. Method

Produced by reading the current source directly and cross-checking with a parallel senior-agent audit (10 area auditors — shell/nav, home, workbench, runs, review, model-gateway, settings/runtime, design-system, data/i18n, and a test-contract auditor). Every high-stakes claim was re-verified first-hand, and the **compile claim was verified by running the typecheck** (Section 4.0).

Files read first-hand (not via subagent): `app/navigation.ts`, `app/App.tsx`, `app/AppShell.tsx`, `app/ActivityBar.tsx`, `app/ExplorerPanel.tsx`, `app/InspectorPanel.tsx`, `app/StatusBar.tsx`, `app/CommandPalette.tsx`, `app/commandActions.ts`, `features/home/HomePage.tsx`, `features/settings/SettingsPage.tsx`, `features/runtime-setup/RuntimeSetupPanel.tsx`, `components/Disclosure.tsx`, `features/model-gateway/ModelGatewayPage.tsx`, `features/pages.tsx`, `hooks/useControlPlane.ts`, and the nav/Settings/Workbench-tab slices of `tests_web/control-center.spec.js`.

Evidence is cited as `file:line`. Facts are distinguished from inference where it matters.

---

## 2. Executive summary

The frontend is **operationally complete, IDE-shaped, and technically honest**: a Vite/React/TypeScript SPA with a typed OpenAPI-generated client, OKLCH dual-theme tokens, an accessible command palette, evidence-first approval gates, honest runtime-state reporting, pervasive secret redaction, ES/EN runtime-editable i18n, and a Playwright contract already migrated to the new shell.

**The redesign's *structure* is built; its *finish-out* is not, and the legacy detail surfaces still carry the old density/bureaucracy.** Concretely:

- **The working tree does not typecheck** (14 errors, all in the Settings/nav id migration — Section 4.0). This is the #1 blocker: it is a hard fact, verified by `tsc`.
- **Settings is broken end-to-end** — most live Settings explorer links fall through to the projects page; the new `RuntimeSetupPanel` is imported nowhere; and the Playwright Settings tests will fail because they assert legacy labels (`CLI settings`, `Workspace settings`) and headings (`CLI configuration`) that `navigation.ts` no longer produces.
- **Two areas are half-migrated**: Review ships a clean new kanban board *and* still routes the 800-line legacy `JobsApprovalsPage` (the board can't even ship a patch); Model Gateway is a 17-surface orphan page with no nav link.
- **The dense legacy surfaces are unchanged** (Model Gateway, Workflows, Agents, `pages.tsx`): real capability, wrong presentation.

The single most valuable next move is **finishing the Settings/runtime wiring to restore a green typecheck and a reachable runtime-config surface**, then **collapsing the Review duplication** and **splitting Model Gateway into Settings-config + Runs-telemetry**. None of it requires touching backend contracts.

---

## 3. Current state (verified)

### 3.1 Architecture snapshot

| Aspect | Reality (verified) |
| --- | --- |
| Stack | Vite + React + TypeScript SPA; single global CSS design system (`design-system/*.css`) loaded from `main.tsx` (no CSS modules). |
| Shell | `app/AppShell.tsx` = `.app-shell-ide` 4-zone grid: `ActivityBar` (5 area icons) · collapsible `ExplorerPanel` · `WorkbenchHeader` + content · toggleable `InspectorPanel`, with a `StatusBar` footer. |
| Navigation | One source of truth: `navigation.ts` `AREAS` (5) + `EXPLORER_LINKS`/`EXPLORER_GROUPS` (per-area sidebar) + `pageIds` (25 page ids). `ActivityBar` selects an area's `leadPage`; `ExplorerPanel` shows that area's contextual links + dynamic project-scope sections. |
| Routing | `window.location.hash` → `PageId` switch in `App.tsx`; `routeAliases` (`App.tsx:67-83`) map legacy slugs (`ide`/`command`/`workspace`→`workbench`, `runs`→`workflows`, `review`→`jobs`, `settings-cli`→`settings-runtime`, etc.). **Mid-refactor and currently broken for Settings** (Section 4.0). |
| Data layer | `useControlPlane` does a token handshake + full `overview` fetch on mount, then **5 s silent polling**; `mutate(op,{awaitRefresh})` injects `X-Local-Control-Token` and refreshes after writes; optional fetches (`retrievalStatus`/`runtimeProviders`/`runtimeProviderConfiguration`) degrade to `null` on a 4 s timeout. |
| "SSE/connected" | **No SSE/WebSocket.** `state.connected` is initialized `false` and **never set `true`** (`useControlPlane.ts:19,32`). The `StatusBar` now honestly renders "polling/sondeo" — the old fake "connected ×2" is gone — but the dead flag still gates one palette command (Section 4.1, N8). |
| i18n | `I18nProvider` loads a runtime-editable ES/EN catalog from `/api/v1/i18n/catalog`, layers a body-wide `MutationObserver`/`TreeWalker` localizer over keyed `t()`, syncs `<html lang>`, and persists via `TranslationMaintainer` (PUT without rebuild). |
| Motion | `useMotionPreference`/`usePageMotion` honor `prefers-reduced-motion`. |

### 3.2 The five-area model **as built**

`navigation.ts` `AREAS` (`:112-159`) — exactly the requested map:

| Area | Icon | Lead page | Explorer pages (current) |
| --- | --- | --- | --- |
| **Home** | `Home` | `home` | home, projects-active/-finished/-error/-cancelled |
| **Workbench** | `Code2` | `workbench` | workbench, workspaces |
| **Runs** | `Workflow` | `workflows` | workflows, agents |
| **Review** | `ClipboardCheck` | `jobs` | review-board, jobs, evidence, governance, policy, audit |
| **Settings** | `Settings` | `settings-project` | settings-project/-runtime/-agents/-security/-workspaces/-integrations/-advanced (+ models, integrations, memory in the area's page list but **not** as explorer links) |

`ActivityBar` is `role=navigation` "Primary navigation" with 5 `.activity-bar-item` buttons, `aria-current="page"` on the active area (`ActivityBar.tsx:29-60`). `ExplorerPanel` renders the area's links, a grouped Settings (Setup/Advanced), and dynamic per-project scope sections (Active runs / Approvals / Workspaces / Evidence) with collapse + `localStorage` persistence (`ExplorerPanel.tsx`).

### 3.3 What is built vs still legacy (per area)

| Area | Built to Studio target | Still legacy / not done |
| --- | --- | --- |
| **Home** | `HomePage` gallery: hero + Open-folder/Create CTAs + 5 card "bands" on a real CSS grid; honest, no mock cards | Status filtering still lives in 4 `ActiveProjectsPage` routes (no in-page segmented control); Home copy uses an inline `COPY` object, not `t()` |
| **Workbench** | 3-pane IDE: explorer + composer + inspector + 5 a11y tabs (Task/Timeline/Diff/Evidence/Logs); dual intake; governed `issue_to_patch`; runtime honesty; token-protected diff/evidence + redaction | 558-line god component; AI-team/delivery-rail are heuristic scaffolding; run-select doesn't scope diff/evidence; no real file tree (`getProjectFiles` unbuilt) |
| **Runs** | — (route slots only) | `WorkflowsPage` + `AgentsPage` are unchanged legacy surfaces (own PageHeader/Surface/Drawer stacks); don't use the Explorer/Inspector split; a *structurally fake* DAG |
| **Review** | `ReviewPage` kanban board (clean `ReviewItem` model, extracted `useReviewDecision`) | `JobsApprovalsPage` (800 lines) still routed and owns the *only* ship controls; `Evidence/Governance/Policy/Audit` trapped in `pages.tsx` (1107 lines); board duplicates the gate verbatim |
| **Settings** | Grouped explorer (`SETTINGS_GROUPS`), `Disclosure` primitive, the excellent new `RuntimeSetupPanel` | **Broken routing (does not compile)**; `RuntimeSetupPanel` orphaned; `SettingsPage` tabs are the *old* vocabulary (Project selection/User/CLI/API/Parameters/Catalogs/Workspaces) |
| **Model Gateway** | Extracted into 12 panel components over the real API | One 17-surface long-scroll; `PanelShell` is a no-op; eager 15-call load; **orphaned** (no explorer link); widest tables 19/16/15 cols |
| **Design system** | OKLCH dual theme; IDE shell grid; density tokens; inert console grid; masonry→CSS grid; `.card`; `info` tone | No global density *mode*; residual 3px card stripes (flagged anti-pattern); no React `Button/Input/Tabs` primitives; legacy token-alias block |
| **Data/i18n** | `useControlPlane`, ~70-op generated client, runtime i18n, lib primitives — **fully built, keep as-is** | Two divergent redaction impls; `getProjectFiles` forward-contract has no backend; i18n catalog endpoints untyped |

---

## 4. Problems

### 4.0 BLOCKER — the working tree does not typecheck (Settings/nav migration is incomplete)

**Verified.** `corepack pnpm@10.24.0 run typecheck:web` (`tsc --noEmit`) **exits 2 with 14 errors**, all from the half-finished Settings/navigation id rename. The plural/legacy `settings-*` ids were removed from `PageId`, but their consumers, the `App.tsx` switch, and a type import were not updated:

| # | Error (abridged) | Location |
| --- | --- | --- |
| TS2305 | `Module '../features/settings/SettingsPage' has no exported member 'SettingsGroupId'` (it only exports `SettingsTab`) | `App.tsx:28` |
| TS2304 | `Cannot find name 'settingsSectionByPage'` (the defined map is `settingsGroupByPage`) | `App.tsx:344` |
| TS2678 ×7 | `case 'settings-projects' / -user / -cli / -api / -parameters / -maintainers / -defaults` not comparable to `PageId` | `App.tsx:329-336` |
| TS2345 ×4 | `navigateTo('settings-projects')` rejected | `App.tsx:283`, `:299`, `:349`, `commandActions.ts:126`, `ExplorerPanel.tsx:253` |

**Runtime consequence (once it builds — note `vite build`/esbuild strips types and does *not* typecheck, so a broken Settings route could ship silently):** of the 7 live Settings explorer links, only `settings-workspaces` has a matching `App.tsx` switch case; the other six (`settings-project/-runtime/-agents/-security/-integrations/-advanced`) fall through to `default` → **`ActiveProjectsPage`**. The Settings landing, runtime config, agents, security and integrations panels are therefore unreachable from the shell. Confirmed from four independent angles: the typecheck, the `App.tsx` switch, the `commandActions` "Open settings group" action, and the `ExplorerPanel` Settings button.

**The new `RuntimeSetupPanel` is also orphaned.** It is imported nowhere — only the smaller `RuntimeSetupInspectorCard` is wired (into `WorkbenchInspector.tsx:12,49`). And `navigation.ts` labels `settings-runtime` "Runtime & Models" to host it, but `SettingsPage` has no `runtime` tab (`SettingsTab` = projects/user/cli/api/parameters/maintainers/workspaces/defaults). So even after the routing is fixed, the runtime-config surface needs wiring into a section.

> This block must be the first code task. It also breaks several Playwright tests (Section 4.4 / Section 11) — the contract and the code disagree about Settings.

### 4.1 Navigation problems

| # | Problem | Evidence | Direction |
| --- | --- | --- | --- |
| N1 | **Settings routing broken** (see 4.0). | `App.tsx:28,283,299,329-336,344,349`; `commandActions.ts:126`; `ExplorerPanel.tsx:253` | Drive the switch off the singular `PageId`s, define the section map, wire `RuntimeSetupPanel` into a `settings-runtime` section. |
| N2 | **Model Gateway is orphaned.** `models` is in the Settings area's page list but is **not** in `EXPLORER_LINKS.settings`; the only entry is the palette's `configure-runtime` → `navigateTo('models')`. | `navigation.ts:154,196`; `ModelGatewayPage.tsx`; `App.tsx:266` | Fold config into a reachable Settings › Runtime & Models, telemetry into Runs. |
| N3 | **Two competing approval surfaces in Review.** `review-board` and `jobs` are peers; both approve the same actions with the same gate, but **only `jobs` can promote/PR or preview artifacts**, so a user who approves on the board has no way to ship. | `navigation.ts:188-195`; `ReviewPage.tsx` "ready" column has no ship action; `JobsApprovalsPage.tsx:542-633` | Make the board the single decide-and-ship surface; fold promote/PR + artifact preview into it; retire/demote `JobsApprovalsPage`. |
| N4 | **Workbench uses a deprecated settings id.** `onOpenSettings → navigateTo('settings-projects')` works only via the backward-compat alias; if the alias is pruned the inspector's "Project settings" breaks. | `App.tsx:283`; `WorkbenchInspector.tsx:108` | Target the canonical `settings-project` directly. |
| N5 | **Runs is route-slots, not an Explorer/Inspector area.** `WorkflowsPage`/`AgentsPage` own their own headers and selection state and don't consume the shell's Explorer (run list) / Inspector (run detail) split. | `WorkflowsPage.tsx:391`; `AgentsPage.tsx:65-86` | Drive run selection from the Explorer; render detail in the Inspector; lift `selectedWorkflowId` to a hash param (deep-linkable). |
| N6 | **Run is not a first-class entity.** Workbench/Runs select by *workflow* and silently inspect the latest run; a chosen historical run can show a different run's diff/evidence. | `WorkbenchPage.tsx:220-223`; `WorkflowsPage.tsx:449,170-174` | Scope diff/evidence by `workflowRunId`; allow selecting any historical run. |
| N7 | **Four status routes instead of one filter.** `projects-active/-finished/-error/-cancelled` all render `ActiveProjectsPage` via a `statusView` prop; status switches only through 4 sibling Explorer links, not an in-page segmented control. | `App.tsx:50-55`; `navigation.ts:174-178` | One Projects surface + status segmented control + deep-linkable hashes; keep finished/error/cancelled read-only ("Audit only"). |
| N8 | **Dead palette command from the dead `connected` flag.** `refresh-runtime` is `disabled: !connected`; since `connected` is permanently `false`, "Refresh runtime health" can never run ("Control plane offline"). | `commandActions.ts:181`; `useControlPlane.ts:32` | Gate on real freshness (`lastUpdatedAt`) or remove the gate; the header refresh button already works. |
| N9 | **Review explorer is a flat 6-link junk drawer** mixing the active human-gate loop (board/jobs/evidence) with passive records (governance/policy/audit); only Settings got `EXPLORER_GROUPS`. | `navigation.ts:188-195` vs `:201-203` | Group Review into "Decide" (board, evidence) and "Govern" (governance, policy, audit). |

### 4.2 Visual density problems

| # | Problem | Evidence | Direction |
| --- | --- | --- | --- |
| D1 | **Model Gateway is the worst offender:** ~17 stacked `Surface`s on **one non-tabbed route** (`PanelShell` is a 3-line no-op), framed by a 14-tile metric strip; tables reach **19 / 16 / 15 / 12 / 11 columns** (Usage Ledger / Runtime Providers / Model Catalog / Role Assignments / Benchmarks) → guaranteed horizontal overflow. All 15 datasets eager-load via one `Promise.all` on mount behind a blocking loader. | `ModelGatewayPage.tsx:261-293,580-885`; `UsageLedgerPanel.tsx:26-47`; `PanelShell.tsx:10-12` | Split into Settings sub-tabs (Providers / Catalog / Routing & Policies / Budgets & Limits) + move telemetry (ledger/decisions/CLI sessions/benchmarks) to Runs; lazy-load per tab; consolidate columns into row drawers. |
| D2 | **Workflows inspector drawer stacks ~13 Surfaces / ~16 DataTables** for one run, plus a *structurally fake* React Flow DAG (linear `nodes[i]→nodes[i+1]`, grid-by-index). | `WorkflowsPage.tsx:33-40,588-821` | Tabbed run-detail (Overview & gates / Timeline / Evidence & tests / Artifacts / Policy & approvals / Jobs); drop or rebuild the fake DAG; use the now-existing `Disclosure`. |
| D3 | **Decision drawers are scroll-walls** in *both* review surfaces: scope grid → policy-reason `<pre>` → evidence table → artifacts table → 13-check completeness `<pre>` → full diff `<pre>` → security JSON `<pre>` → *then* the reason field + Approve/Reject. | `ReviewPage.tsx:323-414`; `JobsApprovalsPage.tsx:645-767` | Lead with a PASS/FAIL gate summary + decision controls above the fold; collapse raw diff/security/13-check behind `Disclosure`/tabs. |
| D4 | **Badge soup:** 4 boolean badges per row (detected/configured/available/executable) on Agents + Model Gateway's 16-col runtime table, asserted by E2E. | `AgentsPage.tsx:340-347`; `ModelGatewayPage.tsx:611-695` | One composite status chip + expandable detail; surface missing-config env-var inline only when not executable. |
| D5 | **Duplicate cost tiles (Model Gateway ×4):** Overview "estimated/actual cost today" + Settings "legacy model usage total" + a separate "Cost history" Surface all print the same value; `ProviderAccounts` has a dead "Cost today" column hardcoded to `'cost unavailable'`. | `ModelGatewayPage.tsx:349,600-601,869,881-884`; `ProviderAccountsPanel.tsx:35` | One cost summary; delete the dead column and the legacy "Cost history"/"Settings" metric blocks. |
| D6 | **Raw JSON as user-facing UI:** routing-profile rules, provider metadata, route-preview budget/quota dumped via `JSON.stringify`; evidence/governance JSON in `<pre>`. | `RoutingProfilesPanel.tsx:18`; `ProviderAccountsPanel.tsx:34`; `RoutePreviewPanel.tsx:128,132` | Human-framed fields; raw JSON behind a "developer details" disclosure. |
| D7 | **Workbench triple-timeline + god component:** a persistent run-timeline rail + the *same* timeline again "detailed" in the Timeline tab + a third 6-step delivery rail; `WorkbenchPage` is 558 lines deriving ~25 collections inline (whole IDE re-renders on any overview change). | `WorkbenchPage.tsx:185-276,401`; `TimelinePanel.tsx:64-78` | Keep one compact rail; extract a `useWorkbenchData` hook; gate the heuristic delivery/AI-team scaffolding behind real pipeline-stage data. |
| D8 | **No global density mode.** Density tokens exist (`--control-height-sm/-xs`) but there is no `[data-density]` switch; controls default to 2.75 rem / 44 px and table cell padding is hardcoded, so an IDE that should pack rows can't go compact without per-component edits. | `tokens.css:108-111`; `components.css:344-349,443,538` | Add a `[data-density]` token layer (comfortable/compact) mapping control-height + cell padding; keep the touch-safe default. |
| D9 | **`pages.tsx` is a 1107-line monolith** housing 7 unrelated page components (4 Review surfaces tangled with Workspaces/Memory/Integrations); `EvidencePage` alone is ~350 lines with 4 preview flows. | `pages.tsx:69,86,305,340,689,1013,1093` | Extract each into its own feature folder (mirror `features/review/`). |

### 4.3 Bureaucratic flow problems

Each is real friction; **the underlying guarantee must be kept** — the fix is presentation, not removal.

| # | Flow | Why it's bureaucratic | Fix (preserve the contract) |
| --- | --- | --- | --- |
| B1 | **Review is implemented twice.** The new board and `JobsApprovalsPage` both run the same evidence-gated approve flow over the same actions; helpers were **copied verbatim** (`model.ts:57` "copied verbatim from JobsApprovalsPage"), so the security-critical gate has two divergent sources. | ~150+ duplicated lines; the board can't ship (no promote/PR), so users bounce between two surfaces. | Make the board canonical; have any surviving job-queue import `review/model.ts`; delete the duplicate gate. |
| B2 | **Three separate reason fields** for one ship lifecycle: `decisionReason` (approve/reject), `jobReason` (retry/cancel), `workflowOperationReason` (promote *and* PR) — each its own mandatory textarea on different surfaces. | The operator re-types the same justification 3× to move one run approved → promoted → PR'd. | Keep the audited-reason requirement; carry the recorded decision reason forward as the editable default for the next step; collapse branch/title/base into an "advanced" disclosure. |
| B3 | **Multi-gate ship ceremony:** approve fires two API calls (`approveAction` then `approveIssueToPatch/Pr`); promote + PR are separate, separately-reasoned, status-gated buttons living only on the legacy page. | Shipping one patch = approve → (reason) promote → (reason) PR across 2 surfaces. | Keep the evidence-first gate + per-action isolation + status guards (E2E-locked); render as one "Ready to ship" stepper on the board's `ready` card with a carried-forward reason. |
| B4 | **13-check completeness list as a `<pre>`** the operator must read before Approve enables — rendered as a newline-joined machine string in *both* pages. | No grouping/icons/severity; reads as prerequisites, not next steps. | Structured grouped checklist (Badge/StatusDot per check); each failed check deep-links to the missing artifact; keep the single `evidence_complete` summary. |
| B5 | **Over-wizardized "open folder."** The 3-step New Project wizard (source → review → open) runs even for the zero-decision "open an existing repo" case, although discovery auto-runs on folder pick and pre-fills name/template; the review step even duplicates the "Detect project" button. | 3 confirmations for a flow with no required input; asymmetric validation (review→open doesn't re-validate). | Express 1-click "Open" for a clean single-runtime detection; reserve the wizard for `create_workspace`; validate on every forward transition. Keep both flows, the duplicate-name guard, the native-picker + manual fallback, and the contract-locked field labels. |
| B6 | **Strict-profile-first agent creation:** ~15 flat fields, Save hard-disabled until 4 catalogs all load; no edit/clone; the form lives on the Runs monitoring surface. | A single failing catalog endpoint blocks *all* agent creation; whole form blanks on partial failure. | Keep the `catalogAvailable` governance gate; move creation to Settings › Agents; group fields (Identity/Runtime/Governance/Limits); add edit/clone; degrade per-field on partial catalog failure. |
| B7 | **Model-gateway forms are paperwork.** The "strict model policy" form forces an id matching a regex + a separate name while hardcoding `role=id`, `routingProfileId`, and `allowCli/allowApi`; the manual benchmark form's own copy says "Manual reports are not objective proof"; the route-preview is a 13-input what-if form. | High-ceremony data entry on a config page where most "fields" aren't real choices. | In Settings › Runtime & Models reduce to provider+model+budget with defaults; demote manual benchmark to a drawer (move telemetry to Runs); keep the honest "operator-reported"/"insufficient data" labeling. |
| B8 | **Governance/policy creation forms gate on a selected project** and impose conditional-mandatory fields (high/critical risk ⇒ mitigation; accepted decision ⇒ context+text; sandbox ⇒ reason). | Legitimate honesty gates, but they read as form-walls inside the active Review *decide* loop. | Keep every validation (they encode real contracts); relocate the creation-heavy forms out of the decide loop (Settings / a Govern sub-area); leave the records readable in Review. |

---

## 5. Reusable components

**Keep and evolve (do not introduce a new UI framework):**

- **Shell:** `AppShell` (4-zone `.app-shell-ide` grid), `ActivityBar`, `ExplorerPanel` (contextual + dynamic scope sections + grouped Settings), `InspectorPanel`, `StatusBar`, `WorkbenchHeader`.
- **Primitives (`components/primitives.tsx`):** `PageHeader`, `Surface`, `DataTable<T>`, `Drawer`/`Modal` (Escape + `aria-modal` + scrim), `Badge`, `StatusDot`, `EmptyState` — all four tones (`ok/warn/danger/info`) present.
- **Patterns:** `CommandPalette` + `useCommandActions` (single source for palette UI *and* global keyboard dispatch — `matchesShortcut`), `Disclosure` (accessible progressive disclosure, `h3`-titled), `WorkbenchTabs` (roving-tabindex `role=tab/tabpanel` — reuse as the Studio tab primitive).
- **Domain components worth lifting out of their pages:** `WorkflowTimeline` + `buildWorkflowTimeline`/`timelineModel` (merged chronology + per-step gate overlay), `useReviewDecision` + `buildReviewItems`/`classifyReviewItem`/`evidenceCompleteness` (the evidence-gate engine — should become the *single* source the legacy queue also imports), `RuntimeSetupPanel`/`RuntimeSetupInspectorCard` (honest per-provider runtime cards), `WorkbenchDiffPanel`/`WorkbenchEvidencePanel` (token-protected, redacted diff/evidence — reuse in Review), `HomeCard`/`Band` (promote so `ActiveProjectsPage` adopts the gallery language).
- **Cross-cutting hooks/libs:** `useControlPlane` (state/polling/token/mutate/refresh), `useI18n`/`I18nProvider`, `useMotionPreference`/`usePageMotion`, the typed generated client (`api/client.ts`), `lib/format` (`toneForStatus`, `redactVisibleSecret`, `shortId`), `lib/redaction`, `lib/diff` (`hasRealPatchChanges`, `changedFilesFromPatch`), `lib/artifacts`, `lib/paths`.

**Evolve / consolidate:**

- Add a global **`[data-density]`** token mode; tokenize the hardcoded min-heights/cell padding.
- Promote `HomeCard`/`Band` so Home and `ActiveProjectsPage` share one card language.
- Add React **`Button`/`Input`/`Select`/`Tabs`** primitives (today `.button`/`.input`/`.tabs` are CSS-class-only contracts consumers hand-write).
- Unify the **two redaction implementations** (`redactVisibleText` → `[redacted]` vs `redactVisibleSecret` → `[redacted_secret]`) into one with the union of token patterns — a token caught by one currently slips the other.
- De-duplicate verbatim helpers (`mergeNewestById`/`upsertNewestById`/`recordTimestamp` across `pages.tsx`/`AgentsPage`/`ModelGatewayPage`; `money`/`text` in `ModelGatewayPage` vs `utils.tsx`).
- Make `PanelShell` a real tab/collapsible shell (it is a no-op pass-through today).

**Do not reuse (already gone, keep it gone):** `RadialNavigation` and its CSS (`.rotor-ring`, `.wheel-*`) — the Playwright contract asserts `.rotor-ring` count `0`.

---

## 6. Real API inventory — *reuse, do not reinvent*

All endpoints are the typed, generated v1 OpenAPI operations exposed through `api/client.ts` (~70 functions). The redesign must keep **every one reachable**. **No mock/demo data in product flows.**

**Reads (GET):** `getHandshake`, `getOverview` (the aggregate feeding every page), `getRetrievalStatus`, `getRuntimeProviders`, `getRuntimeProviderConfiguration`, `getEvidenceDetail`, agent statuses (`getDeveloper/DevOps/Architect/SecurityAgentStatus`), `getI18nCatalog`, `listProjects`, `listProjectTemplates`, `listProviders`, `listTeams`, `listAgents`, and the full Model Gateway read set (`getModelGatewayOverview`, `…Providers`, `…Models`, `…RoutingProfiles`, `…RolePolicies`, `…UsageLedger`, `…UsageSummary`, `…RoutingDecisions`, `…ProviderLimits`, `…BudgetRules`, `…CliRuntimes`, `…CliSessions`, `…Benchmarks`, `…BenchmarkOutcomes`).

**Writes (POST/PATCH/PUT, token-gated via `X-Local-Control-Token`):**

- Projects/workspaces: `createProject`, `discoverProject`, `selectLocalDirectory`.
- Work intake: `createSession`, `createChat`, `createPipeline`, `createWorkflow`/`createWorkflowWithBody`.
- SDLC workflows: `runIssueToPatch`, `runIssueToPr`, `approveIssueToPatch`, `approveIssueToPr`, `promotePatchToBranch`, `promoteIssueToPrBranch`, `createPullRequestFromPromotedBranch`, `createPullRequestFromIssueToPr`.
- Jobs/approvals: `approveAction`, `denyAction`, `cancelJob`, `retryJob`.
- Agents: `runDeveloper/DevOps/QA/Security/ArchitectAgent`, `createAgentProfile`.
- Governance/policy: `createRisk`, `updateRisk`, `createArchitectureDecision`, `createNextStep`, `updateSandboxProfile`.
- Integrations: `registerMcpServer`.
- Model Gateway: `createModelGatewayRolePolicy`, `previewModelRoute`, `patchModelGatewayProvider`, `healthCheckModelGatewayProvider`, `discoverModelGatewayProviderModels`, `detectModelGatewayCliRuntime`, `recordModelGatewayBenchmarkOutcome`.
- i18n: `updateI18nCatalog`.
- Evidence artifacts: `fetchEvidenceArtifact` / `downloadEvidenceArtifact` → `GET /api/v1/evidence/{id}/artifacts/{id}` (returns `X-AIDO-Artifact-Id/Hash`, `Content-Disposition`; text-only extraction for text MIME).

> **Forward-contract, not yet real:** `getProjectFiles()` / `ProjectFileNode` / `ProjectFilesResponse` (`client.ts:178-204`) are a documented stub for `/api/v1/projects/{id}/files` that **does not exist server-side** and is never called. A real IDE Workbench file tree depends on this route shipping; until then the Explorer must keep gating it behind a capability flag. The i18n catalog GET/PUT are hand-written (`apiRequest`), the only untyped seam in an otherwise generated client.

---

## 7. New AIDO Studio map — *target end-state + the gaps to close*

Five primary destinations (already in `navigation.ts`). Cross-cutting guarantees (Section 9) hold in **every** area. This section states the *target* and what remains.

### 7.1 Home — *workspace gallery & landing* (built; close 2 gaps)
- **Built:** hero ("AIDO Studio" / "Open or continue a project") + Open-folder/Create CTAs + 5 card bands (Continue/Reviews/Blockers/Runs/Evidence) on a real CSS grid; empty state; honest projections of live data.
- **Close:** collapse the 4 project-status routes into one Projects surface + a status segmented control (keep all 4 states reachable + read-only); move Home/ActiveProjects copy onto the `t()` catalog; share one card language with `ActiveProjectsPage`.
- **Preserve:** active-only mutation guard; ignore stale `aido:selectedProjectId`; no fake/demo workspace records; visibility of finished/error/cancelled for audit.

### 7.2 Workbench — *canonical IDE work surface for one workspace* (built; reduce density)
- **Built:** explorer rail + composer + inspector + 5 tabs (Task/Timeline/Diff/Evidence/Logs); dual intake; governed `issue_to_patch`; runtime honesty; token-protected diff/evidence + redaction; cross-area handoffs.
- **Close:** extract a `useWorkbenchData` hook (558-line god component); keep one timeline rail; gate AI-team/delivery-rail behind real pipeline-stage data; scope diff/evidence to the *selected* run; target the canonical `settings-project` id; (longer term) a real file tree once `getProjectFiles` ships.
- **Preserve:** chat intake creates a real linked chat+session+pipeline; governed runtime+QA+approval gate; explicit `runtime_unavailable`; the empty/no-real-patch review-blocked gate.

### 7.3 Runs — *execution observability & technical trace* (route slots only — needs the shell)
- **Target:** master run list in the Explorer + **tabbed run-detail** in the Inspector (Overview & gates / Timeline / Steps & agents / Evidence & tests / Artifacts / Policy & approvals / Jobs & runtime). Absorbs Model Gateway **telemetry** (Usage Ledger / Routing Decisions / CLI Sessions / Benchmarks), the Jobs **queue** (retry/cancel), and Audit/History as a filterable activity view.
- **Close:** make WorkflowsPage/AgentsPage consume the Explorer/Inspector split; make *run* (not workflow) the navigable entity; drop/rebuild the fake DAG; roll up the 4-boolean badges.
- **Preserve:** run-centric correlation across the 15 linked record types; per-step gate overlay; completion-gap checklist + blockers; chronological timeline; model-call cost/token with honest "unknown"; QA verdict; `internal_mock`/`manual` never selectable.

### 7.4 Review — *human gates, evidence, QA, governance decisions* (board built; collapse the duplicate)
- **Target:** the kanban **board** is the single decide-and-ship surface — pending-actions inbox + structured evidence panel + full diff/security before approval + approve/reject with reason + **promote/PR folded in** + artifact preview/download. Governance/Policy/Audit grouped as a "Govern" sub-section (records readable; creation forms relocated).
- **Close:** fold `JobsApprovalsPage`'s ship controls + artifact preview into the board, then retire/demote it; extract Evidence/Governance/Policy/Audit out of `pages.tsx`; replace the board's context-destroying `window.location.hash='evidence'` with an inline/deep-linked evidence view; structure the 13-check `<pre>`.
- **Preserve:** evidence-first gating (Approve disabled until full diff + security findings seen + `evidence_complete` + recorded reason); per-action isolation; both `issue_to_patch` and `issue_to_pr` lifecycles; honest `pr_unavailable`; policy/sandbox revision diffs; governance project-scoped CRUD; secret redaction.

### 7.5 Settings — *grouped configuration, not the default flow* (broken — fix first)
- **Target groups:** Project · Runtime & Models (host `RuntimeSetupPanel` + split Model Gateway config into Providers / Catalog / Routing & Policies / Budgets & Limits) · Agents (move agent creation here) · Security (sandbox form) · Workspaces · Integrations · Advanced (defaults, maintainers, TranslationMaintainer, Memory/Retrieval diagnostics, API/CLI diagnostics).
- **Close (BLOCKER first):** fix the routing/typecheck (Section 4.0); wire `RuntimeSetupPanel`; reconcile the `SettingsPage` tab vocabulary with `navigation.ts` and the Playwright contract (one source of truth).
- **Preserve:** strict no-raw-JSON forms (`textarea[data-json-editor]` must stay `0`) with slug/numeric validation + inline errors; runtime/provider status without secrets; sandbox update requires reason; collapsed defaults; the authoritative new-project guard.

---

## 8. Page mapping (current → area, with build status)

| Current route / component | Area (per `navigation.ts`) | Status | Notes |
| --- | --- | --- | --- |
| `home` / `HomePage` | **Home** | ✅ Built | Workspace gallery; the loop entry. |
| `projects-active/-finished/-error/-cancelled` / `ActiveProjectsPage` | **Home** | ⚠️ Legacy | 4 routes, one component + `statusView`; collapse to a segmented control. |
| `workbench` / `WorkbenchPage` | **Workbench** | ✅ Built | Core IDE surface; reduce density. |
| `workspaces` / `WorkspacesPage` (`pages.tsx`) | **Workbench** | ⚠️ Legacy | Allocation records; extract from `pages.tsx`. |
| `workflows` / `WorkflowsPage` | **Runs** | ⚠️ Legacy | Tabbed run-detail; drop fake DAG; adopt Explorer/Inspector. |
| `agents` / `AgentsPage` | **Runs** | ⚠️ Legacy | Monitoring stays in Runs; **creation → Settings › Agents**. |
| `review-board` / `ReviewPage` | **Review** | ✅ Built | The intended decide surface; absorb ship controls. |
| `jobs` / `JobsApprovalsPage` | **Review** | ⚠️ Legacy | Owns the only promote/PR + artifact preview; fold in, then retire; queue → Runs. |
| `evidence` / `EvidencePage` (`pages.tsx`) | **Review** | ⚠️ Legacy | Deep evidence/QA auditor; extract from `pages.tsx`. |
| `governance` / `GovernancePage` (`pages.tsx`) | **Review** | ⚠️ Legacy | Records readable in Review; relocate creation forms. |
| `policy` / `PolicySecurityPage` (`pages.tsx`) | **Review** (read) + **Settings** (write) | ⚠️ Legacy | Split: revision-diff/permission reads in Review, sandbox form in Settings › Security. |
| `audit` / `AuditPage` (`pages.tsx`) | **Review** | ⚠️ Legacy | Dedupe vs the inline trail in `JobsApprovalsPage`. |
| `models` / `ModelGatewayPage` (folder) | **Settings** (config) + **Runs** (telemetry) | 🟥 Orphaned | No explorer link; split the 17-surface monolith. |
| `memory` / `MemoryPage` (`pages.tsx`) | **Settings** | ⚠️ Legacy | Retrieval diagnostics; routing works. |
| `integrations` / `IntegrationsPage` (`pages.tsx`) | **Settings** | ⚠️ Legacy | Drop the dead "IDE connections" table; routing works. |
| `settings-project/-runtime/-agents/-security/-integrations/-advanced` / `SettingsPage` | **Settings** | 🟥 Broken | Fall through to `ActiveProjectsPage`; does not typecheck (Section 4.0). |
| `settings-workspaces` / `SettingsPage` | **Settings** | 🟥 Broken | Only settings id with a switch case, but `section` is undefined. |
| `RuntimeSetupPanel` (`features/runtime-setup/`) | **Settings › Runtime & Models** | 🟥 Orphaned | Imported nowhere; only the inspector-card variant is wired (Workbench). |
| `NewWorkspaceDialog` (global modal) | **Home** | ✅ Built | Globally mounted; invoked from Home/ActiveProjects/Workbench. |

✅ built to Studio target · ⚠️ legacy surface dropped into a working route slot · 🟥 broken or orphaned (finish-out required).

---

## 9. Functionalities that cannot be lost

Non-negotiable: these protect backend contracts, runtime truth, security, QA evidence, accessibility, or are pinned by the Playwright specs (`control-center.spec.js` ~2159 lines / ~40 tests; `command-palette.spec.js` 7 tests — both run against a **live FastAPI v1 backend**).

**Data & shell contracts**
- API-driven data only; **no mock/demo/fake-success** in product flows.
- Generated v1 OpenAPI client usage (Section 6); typed `mutate(op,{awaitRefresh})` + `refresh(silent)`.
- Token handshake → `X-Local-Control-Token` write header on every mutation.
- 5 s polling + post-mutation refresh; non-fatal optional fetches (timeout→null); silent-poll errors keep last-good overview; `AbortController` teardown; `lastUpdatedAt`.
- **Polling-only honesty:** `state.connected` stays `false`; never claim a live/SSE status without a real transport.
- Operational-project selection restricted to **active-only**, persisted (`aido:selectedProjectId`), stale ids dropped.
- **5-area IDE shell:** `role=navigation` "Primary navigation" with exactly **5** `.activity-bar-nav .activity-bar-item`; `aria-current="page"` on the active route; `.ide-nav`/`.workbench-layout`/`.explorer-panel`/`.console-grid`/`.main-area` present; dark theme (body & `.main-area` luminance < 70); **no `.rotor-ring`**.

**Work intake & runtime honesty**
- Workbench conversation intake creating a **real linked chat+session+pipeline** (`pipeline.chatId/sessionId/projectId`); the 5 tabs Task/Timeline/Diff/Evidence/Logs; "AI delivery team" dialog.
- Governed `issue_to_patch`: active project + executable runtime (excludes `manual`/`internal_mock`) + non-empty QA preset + title/text gating; honest `runtime_unavailable`; runtime guard `kind != test/simulation && executable === true`.
- Honest provider matrix: separate Detected/Configured/Available/Executable, `missing_config` with exact env-var; never "Ready" when not executable; `internal_mock` string never in the DOM.

**Approvals, runs, evidence**
- Evidence-first approval gating: Approve disabled until full diff + security findings seen, `evidence_complete`, **and** a recorded reason; the 13-check completeness gate; Reject stays lighter (reason-only); **per-action isolation** (approving one never approves others; two-call chain scoped to one job/action/run).
- `issue_to_patch` **and** `issue_to_pr` lifecycles: status-gated promote/PR; honest `pr_unavailable` + "AIDO_GITHUB_TOKEN is missing."
- Job queue retry/cancel with mandatory reason + lease visibility.
- Run-centric correlation + completion-gap checklist + blockers + per-step gate overlay + chronological timeline + model-call cost/token + QA verdict.
- Token-protected artifact preview/download (`X-AIDO-Artifact-Id/Hash`, content-disposition, text-only extraction, binary fallback); full evidence detail (links, verdict, patch hash, Diff viewer, Security findings, Model & tool calls).
- **Diff truthfulness:** empty/no-hunk patch → "no real changes / changed files 0"; malformed patch never counts as real changes.
- **Secret redaction everywhere** (`[redacted]`/`[redacted_secret]`): provider errors/base-URLs/metadata, evidence model/tool-call metadata, blocker reasons, route preview, runtime reasons, artifact bodies — asserted by E2E negative-body matching.

**Governance, policy, memory, integrations, agents, models**
- Governance project-scoped CRUD (severity-conditional mitigation; accepted-decision context+text) + filters + risk-status transitions.
- Policy & Security: tool-call execution state (`not_executed`/`allowlisted_diagnostic`) + viewable sandbox **policy-revision diffs** gated behind a mandatory "Sandbox update reason".
- Memory & Retrieval: backend status, "SQLite is canonical", memory items.
- MCP registration: strict id regex + shell-operator rejection + stdio-only.
- Agent governance model (role + runtimeMode + permission profile + routing/role-policy + allowed providers/runtimes/tools + token/cost limits + remote/cli/api flags); slug+numeric validation; `catalogAvailable` hard-gate (`configuration_required`); runtime detection matrix + CLI healthcheck.
- Model Gateway: provider lifecycle (enable/disable/health/discover); route preview with candidate scoring + budget/quota/unknown-cost outcome; usage ledger + routing-decisions audit (with filters); role/model policy creation; budget rules + provider limits; model catalog with pricing/capability (unknown when unpriced); **benchmark provenance honesty** ("operator-reported", "insufficient data", never fabricated objective scores).
- **Strict no-raw-JSON forms** (`textarea[data-json-editor]` count must stay `0`) with slug validation + inline errors across agents/model-policies/governance/sandbox/MCP.

**Cross-cutting UX / a11y / i18n (must hold in every area)**
- Command palette (`.command-palette-layer`, `role=dialog`, combobox + listbox/option, `aria-activedescendant` skipping disabled, `aria-keyshortcuts`, Escape restores trigger focus) + global shortcuts (Ctrl+Alt+A/E/W, Ctrl/Cmd+K) + Event/Approval drawers (filterable, Escape-to-close) — mouse-free entry. *(Verified: `commandActions` option ids `go-workbench`/`open-settings`/… and `CommandPalette` option-id scheme match the spec.)*
- ≥44×44 px nav targets (`.ide-nav .nav-item`, `.activity-bar-item`); no horizontal overflow at 375 px; `prefers-reduced-motion` (`data-motion=reduced`, `window.__aidoMotionReduced`); `:focus-visible`; `.sr-only`; ARIA roles for navigation/dialog/table/list/region; `aria-live="polite"` result regions.
- Full ES/EN i18n: runtime-editable catalog (GET/PUT token-protected), `localStorage` persistence (`aido:language`), `<html lang>` sync, `t()` fallback chain, DOM localizer with `CODE/PRE/INPUT/TEXTAREA` blocklist, `TranslationMaintainer` (PUT-without-rebuild); the ActivityBar localizes (Inicio/Ejecuciones/Revisión/Configuración).
- Design-system foundation: OKLCH dual dark/light tokens + semantic ramps + spacing/radius/shadow/type/motion/z-index scales + generic primitives + responsive `table-wrap`/`data-label` + `.app-shell-ide` grid.
- Optimistic upsert + merge-by-newest reconciliation; shared `toneForStatus` mapping.

---

## 10. Before / After

| Before (pre-migration, on `dev`) | Now (built on `feature/runtime-setup-panel`) | Remaining gap |
| --- | --- | --- |
| Radial nav + flat 25-route sidebar (28 buttons, 5 sections). | IDE shell: ActivityBar (5 areas) + Explorer + Inspector + StatusBar; `navigation.ts` `AREAS`. | — (done). |
| `RadialNavigation`/`OverviewPage`/`CommandCenterPage` components. | Removed; logic migrated verbatim into Workbench (`workbenchSelectors.ts:61` "moved from the former CommandCenterPage"). | — (done). |
| Projects = four route-level status pages. | Home = workspace-gallery (hero + bands, CSS grid); 4 status routes still render `ActiveProjectsPage`. | One Projects surface + status segmented control. |
| Command Center is its own page; 3 parallel entry systems. | Workbench owns execution; palette/Workbench invoke the same canonical actions. | Scope diff/evidence by selected run; extract data hook. |
| Approvals/runs/evidence are separate mental models. | Review **board** (kanban) built; Runs/Review areas exist. | Fold legacy `JobsApprovalsPage` into the board; make Runs use the shell. |
| Model Gateway = 18-section scroll; Agents primary. | Same 17-surface page, now extracted into 12 panels but **orphaned** (no nav link). | Split config→Settings sub-tabs, telemetry→Runs; lazy-load; wire a nav link. |
| Settings = hidden 9-item nav tree. | Grouped `SETTINGS_GROUPS` + `Disclosure` + new `RuntimeSetupPanel`. | **Broken: does not typecheck; panel orphaned; tests expect old labels.** |
| Permanently-false "SSE connected" chrome (×2). | `StatusBar` honestly shows "polling". | Remove the dead `connected` gate on `refresh-runtime`. |
| Dense tables + badge soup + raw JSON dominate. | Design system has density tokens, `info` tone, CSS-grid gallery, inert console grid. | Add a `[data-density]` mode; consolidate cards; reduce legacy-page density. |

---

## 11. Remaining gaps / finish-out checklist (for the later code phase)

Ordered by leverage. None requires changing backend contracts.

1. **Restore a green typecheck (BLOCKER).** Fix the Settings/nav id migration: drive the `App.tsx` switch off the singular `PageId`s, define the section map, drop the retired plural cases, correct the `SettingsGroupId` import, and make all `navigateTo('settings-projects')` callers use `settings-project`. Verify with `pnpm run typecheck:web`.
2. **Wire the runtime-setup feature.** Mount `RuntimeSetupPanel` in `settings-runtime` (and reconcile `SettingsPage`'s tab vocabulary with `navigation.ts`). Today it is dead code.
3. **Reconcile Settings with the test contract.** The Playwright suite still clicks `User settings`/`CLI settings`/`Workspace settings` and expects headings `CLI configuration`/`Settings Workspaces`, which `navigation.ts` no longer produces. Decide the canonical Settings IA, then update `navigation.ts` + `SettingsPage` + the spec **in one change** so there is a single source of truth.
4. **Collapse the Review duplication.** Fold promote/PR + artifact preview/download into the board's `ready` card; have any surviving queue import `review/model.ts`; retire/demote `JobsApprovalsPage`; replace the board's full-page evidence hash-jump with an inline/deep-linked view.
5. **Split Model Gateway.** Config → Settings › Runtime & Models sub-tabs (upgrade `PanelShell` into a real tab shell, lazy-load); telemetry → Runs; delete the dead "Cost today" column and the duplicate cost/"Settings" blocks; structure the raw-JSON cells.
6. **Make Runs a real area.** Drive selection from the Explorer, detail in the Inspector; make *run* the navigable entity; drop/rebuild the fake DAG; move agent creation to Settings › Agents.
7. **Reduce Workbench density.** Extract `useWorkbenchData`; keep one timeline; gate the AI-team/delivery scaffolding behind real pipeline-stage data.
8. **Design-system hygiene.** Add `[data-density]`; consolidate cards (drop residual 3px stripes); add React `Button/Input/Select/Tabs`; unify the two redactors; migrate + delete the legacy token-alias block.
9. **Home polish.** Status segmented control; move copy onto `t()`; one shared card language with `ActiveProjectsPage`; express "open folder".

---

## 12. Implementation guardrails (for the later code phase)

- Do not rewrite backend contracts for UX convenience.
- Do not weaken evidence gates, honest runtime states, secret redaction, diff-truthfulness, or per-action isolation.
- Do not replace strict forms with JSON editors (`textarea[data-json-editor]` must stay `0`).
- Do not introduce demo/mock data to fill empty states; do not flip `connected` true without a real transport.
- Keep the 5 `.activity-bar-item` structure + `aria-current` + `.rotor-ring` count `0`, or update the Playwright spec deliberately in the same change.
- Keep ≥44 px nav targets, the 375 px no-overflow guarantee, reduced-motion, dark theme, and full ES/EN i18n.
- Keep URL deep links for tabs/actions and the `routeAliases` map (many tests navigate by legacy hash).

---

## 13. Verification expected (for the later code phase)

Minimum bar:

- `corepack pnpm@10.24.0 run typecheck:web` — **currently failing (14 errors)**; must be green.
- `corepack pnpm@10.24.0 run build:control-center`.
- `corepack pnpm@10.24.0 run test:web` (live FastAPI v1 backend with seeded providers required).
- Focused Playwright updates for the Settings IA reconciliation; Review board ship-flow; Runs Explorer/Inspector; Model Gateway split; plus the negative/honesty contracts (`.rotor-ring` 0, `textarea[data-json-editor]` 0, no `internal_mock` in DOM, `manual`/`internal_mock` excluded from runtime selectors, secret redaction, honest `pr_unavailable`).
