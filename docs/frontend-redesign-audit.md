# AIDO Studio — Frontend Redesign Audit

> **Status:** Documentation only. No product code is changed by this document.
> **Branch reviewed:** `dev` · **Date:** 2026-06-15
> **Scope of code reviewed:** `local-control-center/web/src/**` + `tests_web/control-center.spec.js` + `tests_web/command-palette.spec.js`
> **Target:** **AIDO Studio** — a modern, professional IDE-style shell with a workspace **gallery** Home and an IDE **Workbench**, organized around the loop *open folder → detect project → work → review diff/evidence → approve*.

---

## 0. Headline: this is a *finish-the-migration* audit, and the migration moved again

The AIDO Studio structural redesign (IDE shell, 5-area model, workspace-gallery Home, IDE Workbench, evidence-first Review board, grouped Settings) is **built and on disk**. `RadialNavigation.tsx`, `OverviewPage.tsx` and `CommandCenterPage` **no longer exist** — the only residual "radial" token is a moved-from comment in `workbench/workbenchSelectors.ts`, and the Playwright contract still asserts `.rotor-ring` count `0`.

**Correction vs the previous version of this document.** The prior revision audited branch `feature/runtime-setup-panel` and flagged a hard **BLOCKER (the tree did not typecheck, 14 errors)**, an **orphaned `RuntimeSetupPanel`**, **two divergent redactors**, an **un-extracted 558-line Workbench god component**, and **Settings Playwright tests that would fail on old labels**. **On `dev`, all five are resolved.** Re-verified first-hand:

| Prior claim (on `feature/runtime-setup-panel`) | Status on `dev` (verified) |
| --- | --- |
| Tree does not typecheck (14 errors) | ✅ **Green.** `tsc --noEmit -p local-control-center/web/tsconfig.json` exits **0** (run directly, this session). |
| `RuntimeSetupPanel` imported nowhere | ✅ Mounted in the Settings *runtime* group (`SettingsPage.tsx:16,346`); `RuntimeSetupInspectorCard` is the Workbench-only variant (`WorkbenchInspector.tsx:49`). |
| Two divergent redaction impls | ✅ Unified — `redactVisibleText` and `redactVisibleSecret` both delegate to one `maskSecrets` core (`lib/redaction.ts:15-36`; `lib/format.ts:6,24-27`). Commit `9c7cc25`. |
| `WorkbenchPage` 558-line god component | ✅ Data derivation extracted into `useWorkbenchData` (`useWorkbenchData.ts:126-263`); page is now 410 lines and consumes one destructure. Commit `a410f94`. |
| Settings tests expect retired labels, would fail | ✅ Not broken — `settings-cli` and the retired hashes alias into the grouped hub (`App.tsx:67-88`); the test contract asserts the **seven** h2 group cards (`control-center.spec.js:1050-1065`). |

So the redesign is now **~90% structurally done and green**. The remaining work is of three kinds, and this audit is organized around them:

1. **Density debt** — Model Gateway, Workflows, the `pages.tsx` monolith, the Review decision drawers and the Workbench timelines still present real capability as walls of wide tables, raw JSON and stacked surfaces.
2. **Bureaucracy / duplication debt** — Review is implemented twice (the board can't ship), task intake is split in two, agent creation lives in Runs, and the evidence-gate helpers exist in two copies.
3. **i18n & honesty debt** — large surfaces (`pages.tsx`, Model Gateway, Runs, Home, parts of Settings, `StatusBar`, the shell drawers/command labels) bypass the `t()` catalog; and `state.connected` *was* hard-wired `false` (RESOLVED — now `true` on a successful overview, `false` on failure), which had left the StatusBar "polling" and one command + shortcut dead.

None of the fixes requires changing a backend contract.

---

## 1. Method

Produced by reading the current `dev` source directly **and** cross-checking with a parallel 11-agent senior audit (shell/nav · home/projects · workbench · runs · review-active · review-records · model-gateway · settings/runtime · design-system · data/i18n · test-contract). Every high-stakes claim was re-verified first-hand, and the **compile claim was verified by running the typecheck** (exit 0), not assumed.

Files read first-hand (not only via subagent): `app/navigation.ts`, `app/App.tsx`, `features/pages.tsx`, `features/settings/SettingsPage.tsx`, plus targeted reads/greps of `lib/redaction.ts`, `lib/format.ts`, `features/runtime-setup/*`, `web/tsconfig.json` and the root `package.json` scripts. Evidence is cited as `file:line`. Facts are distinguished from inference where it matters.

---

## 2. Executive summary

The frontend is **operationally complete, IDE-shaped, technically honest, and currently green**: a Vite/React/TypeScript SPA with a typed OpenAPI-generated client (~70 ops), OKLCH dual-theme tokens, an accessible WAI-ARIA command palette + drawers + dialogs (focus-trapped), evidence-first approval gates, honest runtime-state reporting, pervasive secret redaction through a single `maskSecrets` core, ES/EN runtime-editable i18n, and a Playwright contract (~40 tests + 7 palette tests) running against a **live FastAPI v1 backend**.

**The structure is built and the blockers are gone; what remains is polish, de-duplication and consistency.** Concretely, ordered by leverage:

- **Collapse the Review duplication.** `ReviewPage` (kanban board) and the 800-line `JobsApprovalsPage` both approve the *same* actions with the *same* evidence gate, but **only `jobs` can promote/PR, retry/cancel or preview artifacts** — so a user who approves on the board cannot ship without switching pages, and the security-critical evidence gate exists in **two verbatim copies** (`review/model.ts:57` "copied verbatim from JobsApprovalsPage").
- **Split Model Gateway.** One non-tabbed `models` route stacks **~18 Surfaces** with tables up to **19 columns**, eager-loads **15+ endpoints** in one `Promise.all`, and is reachable only via a Settings deep-link / palette command (no persistent Explorer link).
- **Finish i18n.** `pages.tsx` (1106 lines), the entire Model Gateway feature, both Runs pages, `HomePage`/`ActiveProjectsPage`, parts of `SettingsPage`, `StatusBar`, and the shell drawers/command labels are hardcoded English (or local `COPY` maps relying on the runtime DOM localizer) instead of the `t()` catalog — a bilingual regression and a risk against the project copy gate.
- **Fix the `connected` honesty inversion.** `state.connected` is initialized `false` and **never set `true`** (`useControlPlane.ts:32`), so the StatusBar can never read "connected" and the "Refresh runtime health" command + its `Ctrl+Alt+R` shortcut are permanently disabled (`commandActions.ts:181`; `App.tsx:178`).

The structural map you asked for already exists; the value of the next phase is **reducing density, removing duplication, and closing the i18n/honesty gaps** without weakening any guarantee.

---

## 3. Current state (verified)

### 3.1 Architecture snapshot

| Aspect | Reality (verified) |
| --- | --- |
| Stack | Vite + React + TypeScript SPA; single global CSS design system (`design-system/{tokens,base,layout,components,motion}.css`) loaded from `main.tsx` (no CSS modules). |
| Shell | `app/AppShell.tsx` = `.app-shell-ide` CSS grid (4 columns + status row): `ActivityBar` (5 area icons) · collapsible `ExplorerPanel` · `WorkbenchHeader` + content · toggleable `InspectorPanel` · `StatusBar` footer (`AppShell.tsx:67-130`; `layout.css:16-20,329-341`). |
| Navigation | One source of truth: `navigation.ts` `AREAS` (5) + `EXPLORER_LINKS`/`EXPLORER_GROUPS` + `pageIds` (24 ids). `ActivityBar` selects an area `leadPage`; `ExplorerPanel` shows contextual links + grouped Settings + dynamic project-scope sections (`navigation.ts:32-227`). |
| Routing | `window.location.hash` → `PageId` switch in `App.tsx`; `routeAliases` (`App.tsx:67-83`) fold legacy slugs (`ide`/`command`/`workspace`→`workbench`, `runs`→`workflows`, `review`→`jobs`, `settings-cli`→`settings-runtime`, etc.). **Green and consistent** — every `pageId` resolves to a renderer. |
| Data layer | `useControlPlane` does a token handshake + full `overview` fetch on mount, then **5 s silent polling**; `mutate(op,{awaitRefresh})` injects `X-Local-Control-Token` and refreshes after writes; optional reads (`retrievalStatus`/`runtimeProviders`/`runtimeProviderConfiguration`) degrade to `null` on a 4 s timeout; `AbortController` teardown (`useControlPlane.ts:37-136`). |
| "connected" | **No SSE/WebSocket.** `state.connected` now set `true` on a successful authenticated overview and `false` on failure (RESOLVED, `useControlPlane.ts:84,96`); honest *polling* posture, no live stream (Section 4.4, H1). |
| i18n | `I18nProvider` loads a runtime-editable ES/EN catalog from `/api/v1/i18n/catalog`, exposes `t(key,fallback)`, **and** layers a body-wide `MutationObserver`/`TreeWalker` DOM localizer that rewrites text by exact-string match (`I18nProvider.tsx:46-126,182`); persisted via `TranslationMaintainer` (PUT without rebuild). Two parallel i18n mechanisms coexist (Section 4.4). |
| Redaction | Single `maskSecrets` core; `redactVisibleText`→`[redacted]`, `redactVisibleSecret`→`[redacted_secret]`; covers Bearer/`sk-`/`ghp_`/`github_pat_`/`glpat-`/`xox*`/`AKIA` + key=value + URL query secret shapes (`lib/redaction.ts:15-36`). |
| Motion | `useMotionPreference`/`usePageMotion` honor `prefers-reduced-motion`; CSS clamps animations to 0.01 ms and disables card hover-lift (`motion.css:36-53`). |

### 3.2 The five-area model **as built**

`navigation.ts` `AREAS` (`:112-159`) — exactly the requested map:

| Area | Icon | Lead page | Explorer pages (current) |
| --- | --- | --- | --- |
| **Home** | `Home` | `home` | home, projects-active/-finished/-error/-cancelled |
| **Workbench** | `Code2` | `workbench` | workbench, workspaces |
| **Runs** | `Workflow` | `workflows` | workflows, agents |
| **Review** | `ClipboardCheck` | `jobs` | review-board, jobs, evidence, governance, policy, audit |
| **Settings** | `Settings` | `settings-project` | settings-project/-runtime/-agents/-security/-workspaces/-integrations/-advanced (+ `models`, `integrations`, `memory` in the area page list but **not** as Explorer links) |

`ActivityBar` is `role=navigation` "Primary navigation" with exactly 5 `.activity-bar-item` buttons + an explorer-collapse toggle (outside the nav scope), `aria-current="page"` on the active area (`ActivityBar.tsx:29-60`). `ExplorerPanel` renders the area's links, grouped Settings (Setup/Advanced), and dynamic per-project scope sections (Active runs / Approvals / Workspaces / Evidence) with collapse + `localStorage` persistence (`ExplorerPanel.tsx:173-412`).

### 3.3 What is built vs still legacy (per area)

| Area | Built to Studio target | Still legacy / not done |
| --- | --- | --- |
| **Home** | Workspace gallery: hero + Open-folder/Create CTAs + 5 card bands (Continue/Reviews/Blockers/Runs/Evidence) on a real CSS grid; distinct first-run empty state; honest projections of live data (`HomePage.tsx:259-466`) | 4 project-status routes instead of an in-page filter; Home/ActiveProjects copy is inline `COPY`/`statusCopy`, not `t()`; up to ~28 cards stacked; "Pending reviews" deep-links to `jobs`, not `review-board` |
| **Workbench** | 3-pane IDE (explorer + composer + inspector) + 5 a11y tabs; **data extracted into `useWorkbenchData`** (no longer a god component); governed `issue_to_patch`; runtime honesty; run-scoped, token-protected, redacted diff/evidence (`WorkbenchPage.tsx`, `useWorkbenchData.ts`, panels) | Triple-timeline density; split Conversation-vs-Governed intake; Explorer rail labeled like a file tree but renders only sessions/runs (`getProjectFiles` is dead); evidence panel = 7 raw-JSON surfaces; transcript silently capped at 8 |
| **Runs** | Both pages render inside the shell; runtime-honest provider matrix; real catalog-gated agent creation; evidence-first completeness; redaction (`WorkflowsPage.tsx`, `AgentsPage.tsx`) | Own `PageHeader`/`Surface`/`Drawer` stacks, do **not** use the shell Inspector; *structurally fake* React Flow DAG (linear `nodes[i]→nodes[i+1]`); ~14-Surface inspector drawer; run is not a first-class entity; 13-field agent-creation form lives here; English-only |
| **Review** | `ReviewPage` kanban board (clean `ReviewItem` model + extracted `useReviewDecision`); evidence gate verbatim; redaction; a11y cards/columns | `JobsApprovalsPage` (800 lines) still routed and owns the **only** ship controls; gate helpers **copied verbatim** into `model.ts`; `Evidence/Governance/Policy/Audit` trapped in `pages.tsx`; scroll-wall decision drawers; 13-check as `<pre>`; 4 separate reason fields |
| **Settings** | **Rewired & green:** 7 grouped `SettingsGroup` cards (Setup + Advanced) with `Disclosure`; `RuntimeSetupPanel` mounted in the runtime group; deep-links to consoles; scroll-into-view deep linking; strict no-raw-JSON (`SettingsPage.tsx`, `runtime-setup/*`) | All 7 groups render fully expanded (no group-level collapse); `.settings-deeplinks`/`.settings-readouts` classes have **no CSS**; mixed i18n (local `COPY` + raw literals + DOM localizer) |
| **Model Gateway** | Benchmark provenance honesty; unknown-cost severity; strict policy validation; defensive redaction; read-after-write (`ModelGatewayPage.tsx` + 11 panels) | One ~18-Surface long-scroll; `PanelShell` is a no-op; eager 15+ call load; widest tables 19/16/15/11 cols; duplicate cost tiles + a dead "cost unavailable" column; raw `JSON.stringify` cells; **no Explorer link**; English-only |
| **Design system** | OKLCH dual theme; IDE shell grid; responsive `table-wrap`; focus-trapped Drawer/Modal; reduced-motion; inert console grid; masonry→CSS grid; review-card inset stripe | Light theme reachable via a persisted theme toggle (RESOLVED); **no `[data-density]` mode**; ~120 lines dead card CSS; `Badge`/`StatusDot` miss the `pending` tone; duplicate `.workbench-layout`/`-ide-layout` + `.tabs`/`.panel-tabs`; legacy token-alias block; no React `Button/Input/Select/Tabs` |
| **Data/i18n** | `useControlPlane`, ~70-op generated client, unified redaction, diff truthfulness, Windows/POSIX path helpers — **keep as-is** | `getProjectFiles` dead stub (route not in OpenAPI); i18n catalog GET/PUT bypass the generated contract; DOM-walking localizer can mistranslate dynamic data; `mutate()` stale-token edge |

---

## 4. Problems

### 4.1 Navigation problems

| # | Problem | Evidence | Direction (preserve contracts) |
| --- | --- | --- | --- |
| N1 | **Two competing approval surfaces in Review.** `review-board` and `jobs` are sibling links; both approve the same actions with the same gate, but **only `jobs` can promote/PR, retry/cancel, or preview artifacts**, so a user who approves on the board cannot ship there. `jobs` is also the area `leadPage`. | `navigation.ts:136,188-195`; `ReviewPage.tsx:408-413` (no ship action) vs `JobsApprovalsPage.tsx:599-630,533-534` | Make the board the single decide-and-ship surface; fold promote/PR + retry/cancel + artifact preview into it; repoint `leadPage` to `review-board`; retire/demote `JobsApprovalsPage`. |
| N2 | **Model Gateway has no Explorer link.** `models` is in the Settings area's page list but absent from `EXPLORER_LINKS.settings`; reachable only via a Settings-hub `ConsoleLink`, the palette `configure-runtime`, and a Home shortcut. | `navigation.ts:154,196`; `SettingsPage.tsx:360`; `commandActions.ts:163-170`; `App.tsx:270` | Add an explicit Explorer entry (Settings › Runtime & data) **or** split config→Settings sub-tabs and telemetry→Runs (Section 7). |
| N3 | **Four status routes instead of one filter.** `projects-active/-finished/-error/-cancelled` all render `ActiveProjectsPage` via a `statusView` prop; status switches only through 4 sibling Explorer links. | `App.tsx:51-54`; `navigation.ts:175-178`; `ActiveProjectsPage.tsx:78-84` | One Projects surface + status segmented control (deep-linkable); keep `matchesProjectStatus` and the active-only selection guard. |
| N4 | **A run is not a first-class entity.** Explorer "Active runs" buttons call `onNavigate('workflows')` with no id; `WorkflowsPage` selects by `workflowId` and shows runs as a sub-table — so the clicked run is never focused or deep-linkable. | `ExplorerPanel.tsx:288-296`; `WorkflowsPage.tsx:449,470` | Make `run` the selectable unit (`selectedRunId` + initial-id prop); scope inspector/timeline to it; the run-filtering logic already exists. |
| N5 | **Runs uses page-owned Drawers, not the shell Inspector.** `WorkflowsPage` renders two in-page Drawers while the shell `InspectorPanel` only shows static project counts — two competing inspector mechanisms. | `WorkflowsPage.tsx:588,822`; `InspectorPanel.tsx:33,71` | Route selected run/workflow detail into the shell `InspectorPanel`; keep the artifact-preview Drawer as a focused modal. |
| N6 | **Home "Pending reviews"/"All reviews" deep-link to `jobs`, not the Review board.** `onOpenReview` → `navigateTo('jobs')` despite a dedicated `review-board`. | `App.tsx:267,348`; `HomePage.tsx:344-353` | Point review cards at `review-board` (label/destination match). |
| N7 | **Settings-owned pages (`models`/`integrations`/`memory`) have no Explorer entry.** Deep-linking highlights the Settings icon but the active page has no Explorer link / `aria-current`. | `navigation.ts:154-156` vs `:196` | Add a "Runtime & data" Explorer group, or reassign to an area that links them. |

### 4.2 Visual density problems

| # | Problem | Evidence | Direction |
| --- | --- | --- | --- |
| D1 | **Model Gateway is the worst offender:** ~18 stacked `Surface`s on **one non-tabbed route** (`PanelShell` is a no-op), framed by a 14-tile metric strip; tables reach **19 / 16 / 15 / 11 / 11 cols** (Usage Ledger / Runtime Providers / Model Catalog / Role Assignments / Benchmarks) → horizontal overflow. All 15 datasets eager-load via one `Promise.all` on mount (incl. an unused `usageSummary`). | `ModelGatewayPage.tsx:249-321,580-886`; `UsageLedgerPanel.tsx:26-47`; `PanelShell.tsx:10-12` | Split into Settings sub-tabs (Providers / Catalog / Routing & Policies / Budgets & Limits) + move telemetry (ledger/decisions/CLI sessions/benchmarks/model-calls) to Runs; lazy-load per tab; primary+detail-drawer columns; drop the unused fetch. |
| D2 | **Workflows inspector drawer stacks ~14 Surfaces / ~15 DataTables** for one run, plus a *structurally fake* React Flow DAG (linear `nodes[i]→nodes[i+1]`, grid-by-index). | `WorkflowsPage.tsx:17-40,588-821` | Tabbed run-detail (Overview & gates / Timeline / Steps & agents / Evidence & tests / Artifacts / Policy & approvals / Jobs); replace the fake DAG with the honest `WorkflowTimeline` (`WorkflowsPage.tsx:364-388`) or edges derived from real `order` metadata. |
| D3 | **Decision drawers are scroll-walls** in *both* review surfaces: scope grid → policy-reason `<pre>` → evidence table → artifacts table → 13-check `<pre>` → full diff `<pre>` → security JSON `<pre>` → *then* reason field + Approve/Reject. | `ReviewPage.tsx:326-415`; `JobsApprovalsPage.tsx:648-763` | Lead with a PASS/FAIL gate summary + decision controls in a sticky footer; collapse raw diff/security/13-check behind `Disclosure`/tabs. |
| D4 | **13-check completeness as a `<pre>`** the operator must read before Approve enables — a newline-joined machine string in *both* pages, no per-check icon/grouping. | `model.ts:211`; `ReviewPage.tsx:372`; `JobsApprovalsPage.tsx:725` | Return the structured `{ok,message}[]` (already built before the join at `model.ts:193-207`) and render a compact pass/fail checklist (failures first); keep the boolean `complete` gate. |
| D5 | **Workbench triple-timeline + dense evidence:** a persistent run-timeline rail + the same timeline "detailed" in the Timeline tab + a third 6-stage delivery rail; the Evidence panel stacks 7 Surfaces incl. 3 raw-JSON `<pre>` dumps; the rail renders an 8-node skeleton even with no run; transcript silently capped at 8 chats. | `WorkbenchPage.tsx:252-254,286`; `TimelinePanel.tsx:63-79`; `WorkbenchEvidencePanel.tsx:127-229` | One timeline component (rail vs detailed by active tab); fold delivery into it as phase grouping; collapse model/tool/policy/hash JSON behind `Disclosure`; hide the rail until a run exists; add a "view full history" affordance. |
| D6 | **Badge soup:** four boolean badges per runtime row (detected/configured/available/executable) on Agents and the 16-col Model Gateway runtime table. | `AgentsPage.tsx:342-345`; `ModelGatewayPage.tsx:611-695` | One derived status chip (`executable` / `needs config` / `not detected`) with sub-states in the existing reason/required-config columns. |
| D7 | **Duplicate cost tiles + dead column (Model Gateway).** `totalCost` printed twice (Settings "legacy total" + "Cost history"); `ProviderAccounts` has a "Cost today" column hardcoded to `'cost unavailable'`. | `ModelGatewayPage.tsx:869,882-883`; `ProviderAccountsPanel.tsx:35` | Collapse the duplicate tiles; delete the always-`cost unavailable` column until a real per-provider daily-cost field exists. |
| D8 | **Raw JSON as user-facing UI:** routing-profile rules, provider metadata, route-preview budget/quota dumped via `JSON.stringify`; evidence/governance JSON in `<pre>`. | `RoutingProfilesPanel.tsx:18`; `ProviderAccountsPanel.tsx:34`; `RoutePreviewPanel.tsx:128,132`; `pages.tsx:562,566,599,639,643,647` | Human-framed key/value fields; raw JSON behind a "developer details" disclosure. |
| D9 | **`pages.tsx` is a 1106-line monolith** housing 7 unrelated page components (`EvidencePage` ~348 lines, `GovernancePage` ~323); `PolicySecurityPage` renders 8 surfaces incl. an 8-column permission-grants table. | `pages.tsx:69,86,305,340,689,1013,1093,233-242` | Extract each into its own feature folder (mirror `features/review/`); compact tables + row drawers. |
| D10 | **No global density mode.** `--control-height/-sm/-xs` tokens exist but there is no `[data-density]` switch, so every control is locked to the touch-safe 2.75 rem default; an IDE that should pack rows can't go compact without per-component edits. | `tokens.css:108-111`; `components.css:2,995` | Add a `[data-density=compact]` token layer (control-height + spacing + cell padding); keep the touch-safe default. |
| D11 | **Explorer junk-drawer.** With a project selected, non-Settings explorers stack 5 dynamic sections (runs/approvals/workspaces/evidence) **plus a permanent dead "Project files unavailable" block** plus the flat area links, duplicating what those pages already show. | `ExplorerPanel.tsx:277-412` | Gate dynamic sections to the areas where they add context (Home/Workbench); drop or fold the never-populated "Project files" block. |

### 4.3 Bureaucratic flow problems

Each is real friction; **the underlying guarantee must be kept** — the fix is presentation, not removal.

| # | Flow | Why it's bureaucratic | Fix (preserve the contract) |
| --- | --- | --- | --- |
| B1 | **Review is implemented twice.** Board and `JobsApprovalsPage` both run the same evidence-gated approve flow over the same actions; the gate helpers and the `decide()` write path are **copied verbatim** (`model.ts:57`; `model.ts:60-213` byte-identical to `JobsApprovalsPage.tsx:35-188`), so the security-critical gate has two divergent sources. | ~150+ duplicated lines; the board can't ship (no promote/PR/retry/cancel), so users bounce between two surfaces. | Make the board canonical; have any surviving job-queue import `review/model.ts` + `useReviewDecision`; delete the duplicated gate. |
| B2 | **Four separate reason fields** across the area for one lifecycle: board decision reason; jobs decision reason; queue retry/cancel reason; workflow operation reason (promote **and** PR). | The operator re-types the same justification to move one run approved → promoted → PR'd, on two surfaces. | Keep the audited-reason requirement on every API call; carry the recorded decision reason forward as the editable default for the next step; collapse branch/title/base into an "advanced" disclosure. |
| B3 | **Split task intake.** Workbench has two disconnected modes — Conversation (`prompt` → `createChat/createPipeline`) and Governed patch (`TaskComposer` with its own `issueTitle`/`issueText`/runtime/QA) — that don't share input. | A user who writes a task in Conversation must re-type title + issue text to run a governed patch. | One composer entry; treat "governed patch" as a progressive expansion of the same prompt (carry `prompt`→`issueText`; reveal runtime/QA/approval on opt-in). Keep both API paths. |
| B4 | **Agent creation lives in Runs.** A 13-field, catalog-gated strict form is the primary left column of `AgentsPage`, even though a `settings-agents` group already exists. | Defining a governance contract (config) sits in the day-to-day observe-runs surface; 13 controls render at once; one failing catalog blocks all creation. | Move creation to Settings › Agents (or a dialog); group fields (Identity/Runtime/Model policy/Limits); keep the `catalogAvailable` hard gate and validation; degrade per-field on partial catalog failure. |
| B5 | **Over-wizardized "open folder."** The 3-step New Project wizard (source → review → open) runs even for the zero-decision "open an existing repo" case, although discovery auto-runs on folder pick and pre-fills name/template; the open step is a read-only echo of the review step. | 3 confirmations for a flow with no required input. | Collapse review+open into a single confirm panel for a confident `open_folder` detection; reserve the full wizard for `create_workspace`. Keep `validate()`/`submit()`, the duplicate-name guard, the native-picker + manual fallback, and contract-locked field labels. |
| B6 | **Read and write fused in `pages.tsx`.** Each page embeds its creation form beside its read tables (sandbox form ⟂ permission audit; governance forms ⟂ records; MCP registration ⟂ logs). | Auditors scroll past forms; operators scroll past logs; nothing maps cleanly to Review (read) vs Settings (write). | Move read-only surfaces (Evidence/Audit/Workspaces/permission & revision reads) to Review; move write forms (sandbox, governance creation, MCP registration) to Settings. Keep every validation. |
| B7 | **Conditional-validation ceremony on creation forms.** Bespoke gates: sandbox reason; severity-conditional mitigation; accepted-decision context+text; MCP id regex + shell-operator block; "Strict … form" titles signal ceremony. | High-friction data entry; the framing reads as bureaucracy. | Keep the real guarantees (the MCP shell-operator block is a genuine injection safeguard); soften presentation — inline hints, reveal conditional requirements only when the triggering option is chosen, drop the "Strict" framing. |

### 4.4 i18n & honesty / tech-debt problems

| # | Problem | Evidence | Direction |
| --- | --- | --- | --- |
| H1 | **RESOLVED — `state.connected` now reflects reachability.** Was initialized `false` and never set `true` (StatusBar stuck "polling"; "Refresh runtime health" command + `Ctrl+Alt+R` dead). Now set `true` in the success `setState` and `false` in the catch, so the StatusBar reads "API connected" and the refresh command works — an honest reachability signal, not an SSE claim ("polling" still means "no live stream"). | `useControlPlane.ts:84,96`; `StatusBar.tsx:41-42` | Done (fixed this redesign). |
| H2 | **Pervasive i18n bypass.** Large surfaces are hardcoded English or use local bilingual `COPY` maps that depend on the runtime DOM localizer (exact-string match) rather than `t()`: `pages.tsx` (entire 1106 lines), all of `features/model-gateway`, both Runs pages, `HomePage`/`ActiveProjectsPage`, `SettingsPage` body literals, `StatusBar`, and the shell drawers + `commandActions` + CommandPalette empty state. | `pages.tsx` (no `t()` anywhere); `model-gateway/*` (no `t()`); `WorkflowsPage.tsx:524-528`; `AgentsPage.tsx:206-210`; `HomePage.tsx:63-142`; `ActiveProjectsPage.tsx:15-76`; `SettingsPage.tsx:287-549`; `StatusBar.tsx:36-58`; `App.tsx:416-449`; `commandActions.ts:99-187` | Route all visible copy through `t(key, fallback)` and register bilingual catalog keys (LF), per the project copy gate. Presentation-only; bilingual coverage must survive the move. *(Note: whether `pnpm run quality` currently fails was not run in this audit — treat as a risk to verify, since some literals may already be catalog-registered for the DOM localizer.)* |
| H3 | **Runtime DOM localizer can mistranslate live data.** The `MutationObserver`/`TreeWalker` rewrites any text node / `aria-label`/`title`/`placeholder` whose trimmed value exactly equals a catalog source string — so a project name, status or evidence label that collides with a catalog key is silently replaced, and the whole body is re-walked on each mutation. | `I18nProvider.tsx:46-126` | Standardize on render-time `t()` (already exposed at `:182`) and retire/tightly-scope the localizer once H2 lands. Data-corruption and a11y risk, plus a perf cost. |
| H4 | **`getProjectFiles` is a dead forward-contract stub.** ~27 lines + a fetch to `/api/v1/projects/{id}/files`, a route **not in the OpenAPI spec** and called by nobody; the Workbench Explorer implies a file tree it cannot render. | `client.ts:178-204`; zero callers; `WorkbenchExplorer.tsx:55` | Until the route ships, keep an honest "not available" state; rename the rail to its real role (Sessions & Runs) or wire behind a real capability check. Don't assume the stub works. |
| H5 | **i18n catalog GET/PUT bypass the generated contract.** `getI18nCatalog`/`updateI18nCatalog` use a hand-rolled `apiRequest` with a divergent local type, while every other call goes through `requestGeneratedOperation`; the generated `get/put_i18n_catalog` ops exist. | `client.ts:63-73,166-176` vs `openapi.ts:314-315` | Route both through `requestGeneratedOperation` so catalog shape drift is compile-checked. |
| H6 | **`mutate()` can fire with an empty token.** `token` starts `''`; a write fired before the handshake resolves omits `X-Local-Control-Token` and yields a confusing 401/403 instead of "still connecting." | `useControlPlane.ts:25,111-134`; `client.ts:91` | Read the token from a ref at call time, or gate write affordances on `token !== ''`. |
| H7 | **Unstyled hub classes.** `.settings-deeplinks` and `.settings-readouts` are referenced by `SettingsPage` but have **no CSS rule**, so deep-link groups and runtime readouts render as unstyled blocks. | `SettingsPage.tsx:352-459`; no rule in `design-system/*.css` | Add the two rules next to `.settings-console-link`. Pure CSS. |
| H8 | **Dead/duplicate styling (light theme RESOLVED).** Light theme is now reachable via a persisted theme toggle (`useTheme` sets `data-theme`, applied before paint in `main.tsx`); remaining: ~120 lines of `task-/run-/setting-/workspace-card` CSS have no consumer; duplicate `.workbench-layout`/`-ide-layout` and `.tabs`/`.panel-tabs`; a legacy token-alias block persists; `Badge`/`StatusDot` types omit the `pending` tone the palette defines. | `hooks/useTheme.ts`; `app/WorkbenchHeader.tsx`; `main.tsx`; `components.css:155-257,387-397`; `layout.css:528-534`; `primitives.tsx:9,17` | Theme toggle wired; remaining: delete dead card CSS; consolidate duplicate classes; add the `pending` tone; migrate + delete the alias block. |
| H9 | **`DataTable` uses array index as React key** across 15+ live, frequently-refetched gateway tables. | `primitives.tsx:75-76` | Add a `rowKey` accessor; pass a stable id from the gateway tables. |

---

## 5. Reusable components

**Keep and evolve (do not introduce a new UI framework):**

- **Shell:** `AppShell` (4-zone `.app-shell-ide` grid), `ActivityBar`, `ExplorerPanel` (+ reusable `ProjectSection`: collapsible, count badge, `localStorage`, show-all cap), `InspectorPanel`, `StatusBar`, `WorkbenchHeader`.
- **Navigation as data:** the `navigation.ts` module (`AREAS`/`EXPLORER_LINKS`/`EXPLORER_GROUPS`/`areaForPage`/`titleForPage`) — restructure nav by editing data here, not components.
- **Primitives (`components/primitives.tsx`):** `PageHeader`, `Surface`, `DataTable<T>` (responsive `data-label`, `sr-only` caption, `th scope`), `Drawer`/`Modal` (focus-trapped via `useDialogFocus`, Escape, `aria-modal`), `Badge`, `StatusDot`, `EmptyState`; plus `Disclosure` (accessible, `h3`-titled, `role=region`, `hidden` when closed).
- **Command/keyboard:** `CommandPalette` (WAI-ARIA combobox/listbox) + `useCommandActions`/`matchesShortcut` — single source for palette UI *and* global dispatch. Keep them unified.
- **Workbench engine:** `useWorkbenchData` (the data seam), `WorkbenchTabs` (roving-tabindex tablist), `WorkflowTimeline` + `timelineModel` (one component for rail *and* detailed), `WorkbenchDiffPanel`/`WorkbenchEvidencePanel` (token-protected, redacted), `deriveBlockers`, the runtime-honesty predicates (`runtimeIsExecutableIssueRuntime`).
- **Review engine:** `useReviewDecision`, `buildReviewItems`/`classifyReviewItem`/`evidenceCompleteness` (the evidence-gate; should become the *single* source the legacy queue imports), `ReviewCard`.
- **Runtime-setup:** `RuntimeSetupPanel` (Settings) + `RuntimeSetupInspectorCard` (Workbench) over the pure `runtimeSetup.ts` helpers (`mergeProviders`/`deriveRuntimeState`/`STATE_META`).
- **Home gallery:** `HomeCard`/`CardHead`/`Band` (promote so `ActiveProjectsPage` shares one card language).
- **Cross-cutting:** `useControlPlane`, `useI18n`/`I18nProvider` `t()`, `useMotionPreference`/`usePageMotion`, the generated client (`api/client.ts`), `lib/format` (`toneForStatus`/`redactVisibleSecret`/`shortId`/`countByStatus`), `lib/redaction` (`maskSecrets`), `lib/diff` (`hasRealPatchChanges`/`changedFilesFromPatch`), `lib/artifacts`, `lib/paths`.

**Evolve / consolidate:**

- Add a global **`[data-density]`** mode; tokenize control min-heights/cell padding (D10).
- Add React **`Button`/`Input`/`Select`/`Tabs`** primitives (today `.button`/`.input`/`.tabs`/`.select` are CSS-class-only contracts consumers hand-write).
- Add a `pending` tone to `Badge`/`StatusDot` and a `rowKey` to `DataTable`.
- Make `PanelShell` a real tab/collapsible shell or delete it (it is a no-op alias of `Surface`).
- De-duplicate verbatim helpers: the review gate (`model.ts` ↔ `JobsApprovalsPage`); `upsertNewestById`/`mergeNewestById` across `pages.tsx`/`AgentsPage`/`ModelGatewayPage`; `money`/`text` in `ModelGatewayPage` vs `utils.tsx`; `numericLabel`/`moneyLabel` across the Runs pages → promote into `lib/format`.

**Do not reuse (already gone, keep it gone):** `RadialNavigation` and its CSS (`.rotor-ring`, `.wheel-*`) — the Playwright contract asserts `.rotor-ring` count `0`.

---

## 6. Real API inventory — *reuse, do not reinvent*

All endpoints are the typed, generated v1 OpenAPI operations exposed through `api/client.ts` (~70 functions; `requestGeneratedOperation`). The redesign must keep **every one reachable**. **No mock/demo data in product flows.**

**Reads (GET):** `getHandshake`, `getOverview` (the aggregate feeding every page), `getRetrievalStatus`, `getRuntimeProviders`, `getRuntimeProviderConfiguration`, `getEvidenceDetail`, agent statuses (`getDeveloper/DevOps/Architect/Security/QAAgentStatus`), `getI18nCatalog`, `listProjects`, `listProjectTemplates`, `listProviders`, `listTeams`, `listAgents`, and the full Model Gateway read set (`getModelGatewayOverview`, `…Providers`, `…Models`, `…RoutingProfiles`, `…RolePolicies`, `…UsageLedger`, `…UsageSummary`, `…RoutingDecisions`, `…ProviderLimits`, `…BudgetRules`, `…CliRuntimes`, `…CliSessions`, `…Benchmarks`, `…BenchmarkOutcomes`).

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

> **Not the generated contract (two seams):** `getI18nCatalog`/`updateI18nCatalog` are hand-written (`apiRequest`) — fold into `requestGeneratedOperation` (H5). **Forward-contract, not yet real:** `getProjectFiles()` / `ProjectFileNode` / `ProjectFilesResponse` (`client.ts:178-204`) target `/api/v1/projects/{id}/files`, which **does not exist server-side** and is never called (H4). A real IDE Workbench file tree depends on that route shipping; until then keep an honest empty state.

---

## 7. New AIDO Studio map — *target end-state + the gaps to close*

Five primary destinations (already in `navigation.ts`). Cross-cutting guarantees (Section 9) hold in **every** area. This section states the *target* and what remains.

### 7.1 Home — *workspace gallery & landing* (built; close 3 gaps)
- **Built:** hero ("AIDO Studio" / "Open or continue a project") + Open-folder/Create CTAs + 5 card bands on a real CSS grid; distinct first-run empty state; honest projections of live data.
- **Close:** collapse the 4 project-status routes into one Projects surface + a status segmented control (keep all 4 states reachable + read-only); move Home/ActiveProjects copy onto the `t()` catalog; point review cards at `review-board`; de-emphasize secondary bands and cap the active-projects map.
- **Preserve:** active-only mutation guard; ignore stale `aido:selectedProjectId`; evidence-first detection from real manifest markers; no fake/demo workspace records.

### 7.2 Workbench — *canonical IDE work surface for one workspace* (built; reduce density)
- **Built:** explorer rail + composer + inspector + 5 tabs (Task/Timeline/Diff/Evidence/Logs); `useWorkbenchData` data seam; governed `issue_to_patch`; runtime honesty; run-scoped, token-protected, redacted diff/evidence.
- **Close:** one timeline (rail vs detailed by active tab); merge the two intake modes into a progressive composer; collapse the 7-surface evidence panel behind `Disclosure`; hide the rail until a run exists; surface the truncated transcript; rename the Explorer rail (or wire a real file tree once `getProjectFiles` ships).
- **Preserve:** chat intake creates a real linked chat+session+pipeline; executable-runtime gating (excludes `manual`/`internal_mock`/test/simulation); the empty/no-real-patch review-blocked gate.

### 7.3 Runs — *execution observability & technical trace* (in the shell; needs run-centricity)
- **Target:** master run list in the Explorer + **tabbed run-detail** in the shell Inspector (Overview & gates / Timeline / Steps & agents / Evidence & tests / Artifacts / Policy & approvals / Jobs & runtime). Absorbs Model Gateway **telemetry** (Usage Ledger / Routing Decisions / CLI Sessions / Benchmarks / model-calls) and Audit/History as a filterable activity view.
- **Close:** make `run` (not workflow) the navigable, deep-linkable entity; drive selection from the Explorer; render detail in the shell Inspector (retire the page-owned inspector Drawer); replace the fake DAG; roll up the 4-boolean badges; move agent creation to Settings; translate the pages.
- **Preserve:** evidence-first `completionMissing` (real prerequisites, not a status field); run-centric correlation across the linked record types; per-step gate overlay; chronological timeline; model-call cost/token with honest "unknown"; QA verdict; catalog-gated agent creation with no mocks.

### 7.4 Review — *human gates, evidence, QA, governance decisions* (board built; collapse the duplicate)
- **Target:** the kanban **board** is the single decide-and-ship surface — pending-actions inbox + structured evidence panel + full diff/security before approval + approve/reject with reason + **promote/PR + retry/cancel folded in** + artifact preview/download. Governance/Policy/Audit grouped as a "Govern" sub-section (records readable; creation forms relocated to Settings).
- **Close:** fold `JobsApprovalsPage`'s ship controls + artifact preview into the board, then retire/demote it; make it import the single `review/model.ts` gate + `useReviewDecision`; extract Evidence/Governance/Policy/Audit out of `pages.tsx`; structure the 13-check; sticky decision footer; carry the decision reason forward.
- **Preserve:** evidence-first gating (Approve disabled until full diff + security findings seen + `evidence_complete` + recorded reason; real-diff via `hasRealPatchChanges`; non-blocking-security); **per-action isolation**; both `issue_to_patch` and `issue_to_pr` lifecycles; honest `pr_unavailable`; the no-double-count claim invariant; bounded Done feed.

### 7.5 Settings — *grouped configuration, not the default flow* (rewired & green; polish)
- **Target groups (as built):** Project · Runtime & Models (hosts `RuntimeSetupPanel`; absorb the Model Gateway **config** split into Providers / Catalog / Routing & Policies / Budgets & Limits) · Agents (move agent creation here) · Security (sandbox form) · Workspaces · Integrations · Advanced (defaults, maintainers, `TranslationMaintainer`, Memory/Retrieval + API/CLI diagnostics).
- **Close:** add the missing `.settings-deeplinks`/`.settings-readouts` CSS (H7); migrate the body literals + `COPY` map to `t()` (H2); consider group-level collapse / a left rail so deep-linking focuses one group; add an Explorer entry for Model Gateway (N2/N7).
- **Preserve:** strict no-raw-JSON forms (`textarea[data-json-editor]` must stay `0`) with slug/numeric validation + inline errors; runtime/provider status without secrets; sandbox update requires reason; the authoritative active-only project selection.

---

## 8. Page mapping (current → area, with build status)

| Current route / component | Area (per `navigation.ts`) | Status | Notes |
| --- | --- | --- | --- |
| `home` / `HomePage` | **Home** | ✅ Built | Workspace gallery; the loop entry. |
| `projects-active/-finished/-error/-cancelled` / `ActiveProjectsPage` | **Home** | ⚠️ Legacy | 4 routes, one component + `statusView`; collapse to a segmented control. |
| `workbench` / `WorkbenchPage` | **Workbench** | ✅ Built | Core IDE surface (data extracted to `useWorkbenchData`); reduce density. |
| `workspaces` / `WorkspacesPage` (`pages.tsx`) | **Workbench** | ⚠️ Legacy | Allocation records; extract from `pages.tsx`; translate. |
| `workflows` / `WorkflowsPage` | **Runs** | ⚠️ Legacy | Tabbed run-detail; drop fake DAG; adopt shell Inspector; run-centric; translate. |
| `agents` / `AgentsPage` | **Runs** | ⚠️ Legacy | Monitoring stays in Runs; **creation → Settings › Agents**; translate. |
| `review-board` / `ReviewPage` | **Review** | ✅ Built | The intended decide surface; absorb ship controls. |
| `jobs` / `JobsApprovalsPage` | **Review** | ⚠️ Legacy | Owns the only promote/PR + retry/cancel + artifact preview; fold in, then retire; share the single gate. |
| `evidence` / `EvidencePage` (`pages.tsx`) | **Review** | ⚠️ Legacy | Deep evidence/QA auditor; extract from `pages.tsx`; structure JSON; translate. |
| `governance` / `GovernancePage` (`pages.tsx`) | **Review** (read) + **Settings** (write) | ⚠️ Legacy | Records readable in Review; relocate creation forms to Settings. |
| `policy` / `PolicySecurityPage` (`pages.tsx`) | **Review** (read) + **Settings** (write) | ⚠️ Legacy | Split: revision-diff/permission reads in Review, sandbox form in Settings › Security. |
| `audit` / `AuditPage` (`pages.tsx`) | **Review** | ⚠️ Legacy | Dedupe vs the mislabeled "IDE connections" audit-filter in `IntegrationsPage`. |
| `models` / `ModelGatewayPage` (folder) | **Settings** (config) + **Runs** (telemetry) | 🟡 No Explorer link | Reachable via Settings deep-link / palette / Home shortcut; add a nav entry and split the ~18-surface monolith. |
| `memory` / `MemoryPage` (`pages.tsx`) | **Settings** | 🟡 No Explorer link | Retrieval diagnostics; reachable via deep-link only; translate. |
| `integrations` / `IntegrationsPage` (`pages.tsx`) | **Settings** | 🟡 No Explorer link | Drop the dead "IDE connections" table; reachable via deep-link only; translate. |
| `settings-project/-runtime/-agents/-security/-workspaces/-integrations/-advanced` / `SettingsPage` | **Settings** | ✅ Built | Grouped 7-card hub; `RuntimeSetupPanel` mounted; add missing CSS + `t()`. |
| `RuntimeSetupPanel` (`features/runtime-setup/`) | **Settings › Runtime & Models** | ✅ Built | Mounted in the runtime group; `RuntimeSetupInspectorCard` is the Workbench variant. |
| `NewWorkspaceDialog` (global modal) | **Home** | ✅ Built | Globally mounted; focus-trapped; invoked from Home/ActiveProjects/Workbench. |

✅ built to Studio target · ⚠️ legacy surface in a working route slot · 🟡 reachable but not via a persistent Explorer link.

---

## 9. Functionalities that cannot be lost

Non-negotiable: these protect backend contracts, runtime truth, security, QA evidence, accessibility, or are pinned by the Playwright specs (`control-center.spec.js` ~40 tests / `command-palette.spec.js` 7 tests — both run against a **live FastAPI v1 backend** with per-process SQLite seeded via the real handshake).

**Data & shell contracts**
- API-driven data only; **no mock/demo/fake-success** in product flows.
- Generated v1 OpenAPI client usage (Section 6); typed `mutate(op,{awaitRefresh})` + `refresh(silent)`; token handshake → `X-Local-Control-Token` on every mutation.
- 5 s polling + post-mutation refresh; non-fatal optional fetches (timeout→null); silent-poll errors keep last-good overview; `AbortController` teardown.
- **Polling-only honesty:** keep `connected` reflecting reality without claiming a live/SSE transport that doesn't exist (do not flip `true` on an SSE pretense; H1 fix only marks the *successful authenticated overview* as connected).
- Operational-project selection restricted to **active-only**, persisted (`aido:selectedProjectId`), stale ids dropped; explorer state persisted (`aido:explorer:sections`).
- **5-area IDE shell:** `role=navigation` "Primary navigation" with exactly **5** `.activity-bar-item`; `aria-current="page"`; dark theme; **no `.rotor-ring`** (count `0`). `routeAliases` keep legacy hashes resolving.
- Single source of truth in `navigation.ts`; `CommandPalette` + global dispatch share one `CommandAction[]` (disabled actions skipped by both).

**Work intake & runtime honesty**
- Workbench intake creating a **real linked chat+session+pipeline**; the 5 tabs; governed `issue_to_patch` (active project + executable runtime excluding `manual`/`internal_mock`/test/simulation + non-empty QA preset + title/text gating); honest `runtime_unavailable`.
- Honest provider matrix: separate Detected/Configured/Available/Executable with `missing_config` + exact env-var; never "Ready" when not executable; `internal_mock` string never in the DOM; the 5-state `deriveRuntimeState` invariant (a reachable provider isn't downgraded by a stale `lastError`); Refresh re-probes stored-health providers.

**Approvals, runs, evidence**
- Evidence-first approval gating: Approve disabled until full diff + security findings seen, `evidence_complete`, **and** a recorded reason; the 13-check completeness gate; real-diff via `hasRealPatchChanges` (empty/header-only patch → "no real changes / changed files 0"); non-blocking-security; Reject stays lighter (reason-only); **per-action isolation** (approving one never approves others; two-call chain scoped to one job/action/run); no-double-count claim invariant; bounded Done feed.
- `issue_to_patch` **and** `issue_to_pr` lifecycles: status-gated promote/PR each with its own recorded reason; honest `pr_unavailable` + "AIDO_GITHUB_TOKEN is missing."
- Job queue retry/cancel with mandatory reason + lease visibility.
- Run-centric correlation + completion-gap checklist + blockers + per-step gate overlay + chronological timeline + model-call cost/token with honest "unknown" + QA verdict.
- Token-protected artifact preview/download (`X-AIDO-Artifact-Id/Hash`, content-disposition, text-only extraction, binary fallback); full evidence detail; artifact-metadata guards (refuse when ids missing).
- **Secret redaction everywhere** through `maskSecrets` — both pinned markers `[redacted]`/`[redacted_secret]`; asserted by E2E negative-body matching.

**Governance, policy, memory, integrations, agents, models**
- Governance project-scoped CRUD (severity-conditional mitigation; accepted-decision context+text) + filters + risk-status transitions; project-required guard before any governance write.
- Policy & Security: tool-call execution state (`not_recorded`/`allowlisted_diagnostic`) + viewable sandbox **policy-revision diffs** gated behind a mandatory "Sandbox update reason"; sandbox timeout 1..900 + image-without-spaces validation; explicit Before/After revision records.
- Memory & Retrieval: backend status, "SQLite is canonical", memory items.
- MCP registration: strict id regex + **shell-operator rejection (`[;&|<>\`\r\n]`) — a real injection safeguard** + stdio-only.
- Agent governance model (role + runtimeMode + permission profile + routing/role-policy + allowed providers/runtimes/tools + token/cost limits + remote/cli/api flags); id regex + integer bounds; `catalogAvailable` hard-gate (`configuration_required`); runtime detection matrix + CLI healthcheck.
- Model Gateway: provider lifecycle (enable/disable/health/discover); route preview with candidate scoring + budget/quota/unknown-cost outcome; usage ledger + routing-decisions audit; role/model policy creation; budget rules + provider limits; model catalog with pricing/capability (unknown when unpriced); **benchmark provenance honesty** ("operator-reported", "insufficient data", never fabricated objective scores); read-after-write refresh.
- **Strict no-raw-JSON forms** (`textarea[data-json-editor]` count must stay `0`) with slug validation + inline errors across agents/model-policies/governance/sandbox/MCP.

**Cross-cutting UX / a11y / i18n (must hold in every area)**
- Command palette (`.command-palette-layer`, top-centered modal not a drawer, `role=dialog`, combobox + listbox/option, `aria-activedescendant` skipping disabled, stable option ids, Escape restores trigger focus) + global shortcuts (Ctrl+Alt+A/E/W…, Ctrl/Cmd+K) + Event/Approval drawers (filterable, Escape-to-close, focus-trapped) — mouse-free entry.
- ≥44 px nav targets; no horizontal overflow at 375 px; `prefers-reduced-motion` (`data-motion=reduced`, `window.__aidoMotionReduced`); `:focus-visible`; `.sr-only`; focus-trapped Drawer/Modal (`useDialogFocus`); ARIA roles for navigation/dialog/table/list/region; `aria-live="polite"` result regions.
- Full ES/EN i18n: runtime-editable catalog (GET/PUT token-protected), `localStorage` persistence (`aido:language`), `<html lang>` sync, `t()` fallback chain, `TranslationMaintainer` (PUT-without-rebuild); the ActivityBar localizes (Inicio/Workbench/Ejecuciones/Revisión/Configuración). **The redesign must move bypassed copy onto `t()` without losing any existing bilingual string.**
- Design-system foundation: OKLCH dual tokens + semantic ramps + spacing/radius/shadow/type/motion/z-index scales + responsive `table-wrap`/`data-label` + the `.app-shell-ide` grid + grid collapse variants + full single-column collapse at 980 px.
- Optimistic upsert + merge-by-newest reconciliation; shared `toneForStatus` mapping; Windows/POSIX-aware path helpers.

---

## 10. Before / After

| Before (pre-migration, on `dev` history) | Now (built & green on `dev`) | Remaining gap |
| --- | --- | --- |
| Radial nav + flat ~25-route sidebar. | IDE shell: ActivityBar (5 areas) + Explorer + Inspector + StatusBar; `navigation.ts` `AREAS`. | — (done). |
| `RadialNavigation`/`OverviewPage`/`CommandCenterPage` components. | Removed; logic migrated into Workbench (`workbenchSelectors.ts` "moved from the former CommandCenterPage"); `.rotor-ring` count `0`. | — (done). |
| Tree did not typecheck (14 errors, prior branch). | **Typecheck green** (`tsc --noEmit` exits 0). | — (done). |
| `RuntimeSetupPanel` orphaned; Settings a flat tab vocabulary. | 7 grouped `SettingsGroup` cards; `RuntimeSetupPanel` mounted; legacy hashes aliased. | Add missing CSS + `t()`; group-level collapse. |
| Two divergent redactors; Workbench 558-line god component. | Unified `maskSecrets`; `useWorkbenchData` extracted (page 410 lines). | — (done). |
| Projects = four route-level status pages. | Same 4 routes render `ActiveProjectsPage` via `statusView`. | One Projects surface + status segmented control. |
| Approvals/runs/evidence are separate mental models. | Review **board** (kanban) built; Runs/Review areas exist. | Fold legacy `JobsApprovalsPage` into the board; make Runs run-centric + use the shell Inspector. |
| Model Gateway = 18-section scroll; Agents primary. | Same ~18 surfaces, extracted into 11 panels; reachable via deep-link/palette (no Explorer link). | Split config→Settings sub-tabs, telemetry→Runs; lazy-load; add a nav link. |
| Dense tables + badge soup + raw JSON dominate. | Design system has density tokens, `info` tone, CSS-grid gallery, inert console grid, focus-trapped dialogs. | Add a `[data-density]` mode; consolidate cards; reduce legacy-page density. |
| Copy partially bilingual. | Shell/Workbench/Review-board/NewWorkspaceDialog on `t()`. | Move `pages.tsx`/Model Gateway/Runs/Home/Settings literals + StatusBar + drawers onto `t()`. |

---

## 11. Remaining gaps / finish-out checklist (for the later code phase)

Ordered by leverage. None requires changing backend contracts. The previous BLOCKER (typecheck) and several items (redaction unification, `useWorkbenchData`, Settings/runtime wiring, dialog focus-trap) are **already done**.

1. **Collapse the Review duplication.** Make `ReviewPage` canonical: fold promote/PR + retry/cancel + artifact preview/download into the board's `ready` card; have any surviving queue import `review/model.ts` + `useReviewDecision`; delete the verbatim gate copy in `JobsApprovalsPage`; repoint the Review `leadPage` to `review-board`; replace the board's full-page evidence hash-jump with an inline/deep-linked view.
2. **Split Model Gateway.** Config → Settings › Runtime & Models sub-tabs (upgrade or remove the no-op `PanelShell`, lazy-load per tab); telemetry → Runs; delete the dead "Cost today" column + duplicate cost tiles; structure the raw-JSON cells; add an Explorer link.
3. **Finish i18n (copy gate).** Route `pages.tsx`, `features/model-gateway/*`, both Runs pages, `HomePage`/`ActiveProjectsPage`, `SettingsPage` body literals, `StatusBar`, and the shell drawers/`commandActions`/CommandPalette empty state through `t()` with registered bilingual keys; then retire/scope the DOM localizer. Verify with the project copy gate (`pnpm run quality`).
4. **Fix the `connected` honesty inversion.** Set `connected:true` on successful overview load (and `false` on failure) so the StatusBar is accurate and the refresh command/shortcut work; keep "polling" semantics meaning "no live stream."
5. **Make Runs a real area.** Drive run selection from the Explorer; render detail in the shell Inspector; make `run` the navigable, deep-linkable entity; replace the fake DAG; roll up the 4-boolean badges; move agent creation to Settings › Agents.
6. **Reduce Workbench density.** One timeline (rail vs detailed by tab); merge the two intake modes into a progressive composer; collapse the evidence panel behind `Disclosure`; hide the rail until a run exists; surface the truncated transcript.
7. **Home polish.** Status segmented control; copy onto `t()`; one shared card language with `ActiveProjectsPage`; review cards → `review-board`; de-emphasize secondary bands.
8. **Extract `pages.tsx`.** One feature folder per page (mirror `features/review/`); separate read (Review) from write (Settings); structure JSON; drop the dead "IDE connections" table.
9. **Design-system hygiene.** Add `[data-density]`; add `.settings-deeplinks`/`.settings-readouts` CSS; add React `Button/Input/Select/Tabs`; add the `pending` tone + `DataTable rowKey`; consolidate duplicate layout/tab classes; wire or drop the light theme; delete dead card CSS; migrate + delete the legacy token-alias block.
10. **Data-layer hardening.** Route i18n catalog GET/PUT through the generated contract; guard `mutate()` against an empty token; keep `getProjectFiles` honest (no wiring until the route ships).

---

## 12. Implementation guardrails (for the later code phase)

- Do not rewrite backend contracts for UX convenience.
- Do not weaken evidence gates, honest runtime states, secret redaction (`maskSecrets`, both markers), diff-truthfulness, per-action isolation, or the no-double-count claim invariant.
- Do not replace strict forms with JSON editors (`textarea[data-json-editor]` must stay `0`); keep the MCP shell-operator block.
- Do not introduce demo/mock data to fill empty states; do not flip `connected` true on an SSE pretense (a successful authenticated overview is the only honest signal).
- Keep the 5 `.activity-bar-item` structure + `aria-current` + `.rotor-ring` count `0` + the command-palette-as-modal (not drawer), or update the Playwright spec deliberately in the same change.
- Keep ≥44 px nav targets, the 375 px no-overflow guarantee, reduced-motion, dark theme, focus-trapped dialogs, and full ES/EN i18n; when moving copy onto `t()`, preserve every existing bilingual string.
- Keep URL deep links for tabs/actions and the `routeAliases` map (many tests navigate by legacy hash).

---

## 13. Verification expected (for the later code phase)

Minimum bar (root `package.json` scripts; `packageManager` = `pnpm@10.24.0`):

- `corepack pnpm@10.24.0 run typecheck:web` — `tsc --noEmit -p local-control-center/web/tsconfig.json`. **Currently green; must stay green.**
- `corepack pnpm@10.24.0 run build:control-center` (`vite build`). *Note: esbuild strips types and does not typecheck — run the typecheck separately.*
- `corepack pnpm@10.24.0 run test:web` (`node scripts/run-web-tests.mjs`; live FastAPI v1 backend with seeded providers required).
- The project copy/quality gate for the i18n migration (item 3).
- Focused Playwright updates for: the Review board ship-flow; Runs Explorer/Inspector + run-centric routing; the Model Gateway split; plus the negative/honesty contracts (`.rotor-ring` 0, `textarea[data-json-editor]` 0, no `internal_mock` in DOM, `manual`/`internal_mock` excluded from runtime selectors, secret redaction negative-body, honest `pr_unavailable`, palette-modal-not-drawer, retired settings hashes resolve to a group).
