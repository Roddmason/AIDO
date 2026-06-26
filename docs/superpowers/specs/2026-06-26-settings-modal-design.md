# Settings Modal with Inheritance & Source — Design

Date: 2026-06-26
Status: Approved direction (scope choices locked); pending spec review.

## 1. Goal & success criteria

Replace the route-based settings experience (`/#settings*` → `SettingsPage`) with a
single **settings modal** that exposes two scopes — **General** (app-wide) and
**Project** (active project) — and, for settings that genuinely inherit, shows each
value's **effective value, whether it is inherited or overridden, and its source**.

Verifiable success criteria (Phase 1):

1. Opening settings (sidebar footer, menu, `#settings` hash) shows a two-pane modal:
   a section list grouped into **General** and **Project**, and a content panel.
2. The modal renders **every** section in the agreed taxonomy (11 general + 12 project).
   Each section is one of: *wired* (real two-tier setting), *display+link* (real data,
   read-mostly, links to its deep console), or *placeholder* (honest "not configured yet").
3. Three settings are wired end-to-end through a real resolver — **Autonomy**,
   **Security (sandbox default)**, **Budget cap** — each showing `Inherited · General`
   or `Overridden · Project`, with "Set for this project" / "Revert to General" actions
   that persist and re-resolve.
4. The old `/#settings*` hashes still work: they open the modal at the mapped section.
5. All verification gates pass: `typecheck:web`, `check:web` (exit 0), `test:py`
   (i18n + architecture + new resolver tests), full `test:web`, `build:web`.

Non-goals (Phase 1): wiring the remaining inheritable settings (Routing, Default Team,
Research/Internet) as real overrides; new backend models for Goal/Quality/Research/Internet
content. These are honest displays or placeholders now, expanded in later phases.

## 2. Architecture

A dedicated **`settings` vertical slice** (deterministic backend module + typed contracts
+ API + a frontend modal), rather than scattering general/project duality across every
subsystem.

### 2.1 Backend slice (`local_control_center/settings/`)

- **Registry** — a static list of *descriptors*:
  `{ key, section, scope: 'general-only' | 'project-overridable', type, default, binding }`.
  `binding` is either `'store'` (value lives in the generic settings store) or
  `'adapter:<name>'` (value is resolved by delegating to an existing subsystem).
- **Generic store** — SQLite table `settings_value(key, scope, scope_id, value_json)`,
  unique on `(key, scope, scope_id)`; persists only explicitly-set values. `scope`
  ∈ `{general, project}`; `scope_id` is null for general, the project id for project.
- **Adapters** — the registry leaves room for a setting to delegate to a subsystem
  (`resolve(project_id)` / `set` / `clear`) instead of the generic store. **Phase 1 uses
  no adapter**: grounding showed `budget_rules.scope_type` is `{agent, workflow, provider,
  role, global}` with **no `project` scope**, so budget cannot be project-overridden by
  reusing it. All three Phase-1 settings use the generic store; a real `budget_rules`
  enforcement adapter (which requires adding a `project` scope there) is a follow-up.
- **Resolver** — pure function `resolve_settings(project_id) -> list[ResolvedSetting]`.
  For each descriptor:
  - `store` binding: precedence **project override > general value > descriptor default**.
  - `adapter` binding: calls the adapter's `resolve`.
  Result per setting: `{ key, section, scope, value, origin: 'default'|'general'|'project',
  inherited: bool, source: <human label>, editableScopes }`.
- **API** (FastAPI v1, local-token-gated like the rest):
  - `GET /api/v1/settings?projectId=<id>` → `{ general: [...], project: [...] }` resolved.
  - `PUT /api/v1/settings/{key}` body `{ scope, scopeId?, value }` → set a value.
  - `DELETE /api/v1/settings/{key}?scope=&scopeId=` → clear (revert to inherited).
  All writes are audited and validated against the descriptor's type/enum (fail-closed).

### 2.2 Frontend modal (`features/settings/`)

- `SettingsModal` — built on the existing `Dialog` primitive (Task 9 animations), a wide
  modal with two panes:
  - **Left**: section navigator with two labelled groups (General / Project), a search box.
  - **Right**: the active section's content + a `SettingRow` component that renders the
    value control plus the inheritance/source chip and the set/revert affordance.
- `useSettings(projectId)` hook — fetches the resolved settings, exposes `setValue` /
  `clearValue` that call the API and refresh.
- Section content components are small and section-scoped. *Display+link* sections reuse
  existing panels (e.g. `CredentialManagerPanel`, runtime/provider views) read-mostly and
  render a `ConsoleLink` to the deep console. *Placeholder* sections render a shared
  `SectionPlaceholder` ("Not configured yet" + what it will hold).
- The `SettingsPage` route and `settings-*` route entries are removed; `routes.tsx`/`App`
  gain modal open state (like the command palette / drawers). `routing.ts` maps the legacy
  `#settings*` hashes to "open modal at section X" for backward-compat and deep-linking.

## 3. The three wired settings (Phase 1, concrete)

| Setting key | Section | Type | Default | Binding | Inheritance source |
|---|---|---|---|---|---|
| `autonomy.level` | General→Autonomy / Project→(override) | enum `guided\|recommended\|autonomous` | `guided` | store | Project override → General → default |
| `security.sandboxProfileId` | General→Security / Project→Security | enum (ids from `sandboxProfiles`) | unset (runtime default; no override) | store | Project override → General → default |
| `budget.maxCostUsd` (Phase 1: cap only) | General→Costs / Project→Budget | number | unset | store | Project override → General → default |

The `budget.maxCostUsd` key is surfaced at two scopes: the **General default** in the
**Costs** section, the **Project override** in the **Budget** section. Both persist to the
generic two-tier store and resolve via the same precedence as the other settings. Wiring
this cap into actual `budget_rules` enforcement (which needs a new `project` scope_type
there) is a deliberate follow-up, not Phase 1.

Notes:
- **Autonomy**: the setting feeds the existing `AutonomyProfile` (`{level, overrides}`);
  Phase 1 wires the top-level `level`. Category overrides remain in the engine, surfaced later.
- **Security**: `SecurityPosture` (`loopbackOnly`, `writeTokenRequired`) is shown as a
  read-only "runtime guarantees" panel next to the configurable sandbox-profile setting.
- **Budget**: `budget.maxCostUsd` is a generic-store cap (general default, project override)
  resolved like the others; it does not yet feed `budget_rules` enforcement (follow-up).

## 4. Section taxonomy & rendering

**General**: General (display), Appearance (device prefs — theme/density/language),
Providers & CLI (display+link), Credentials (display), Default Team (placeholder→later),
**Autonomy (wired)**, **Security (wired)**, Research (placeholder→later),
**Costs (wired: budget cap)**, Integrations (display+link), Advanced (display).

**Project**: Project (display), Goal (`metadata.goal` small editable field),
Team (display+link), Routing (display+link→later override), Quality (display+link),
**Security (wired override)**, Workspaces (display), Internet (placeholder→later),
**Budget (wired override)**, Credentials (display), Integrations (display), Advanced (display).

Appearance stays device-scoped (theme/density/language are local prefs, not project data),
shown with source "This device".

## 5. Inheritance / source UX

- Project setting inheriting the general value → chip **"Inherited · General"** (muted),
  with a **"Set for this project"** affordance that reveals the override control.
- Project setting with an override → chip **"Overridden · Project"**, with **"Revert to
  General"** that calls `DELETE` and re-resolves.
- General setting → **"Default"** vs **"Custom"** chip.
- Chips use full-color borders/badges only (no side-stripes), per the visual gate.

## 6. i18n

Every new visible string routes through `t()` with bilingual EN≠ES catalog keys (LF),
per the i18n gate. Section titles, chip labels, placeholder copy, and affordances are
all registered. Settings *values* (enum members) are localized via existing patterns.

## 7. Testing

- **pytest** (`tests_py/`): resolver determinism (project>general>default), enum/type
  validation (fail-closed), adapter:budget resolution vs `budget_rules`, API
  set/clear/round-trip, audit emission. Source documentation headers for new modules.
- **playwright** (`tests_web/`): open modal; section nav for General/Project; the three
  wired settings show the correct chip; set a project override → chip flips to
  "Overridden · Project" + value persists; revert → back to "Inherited · General";
  legacy `#settings-security` hash opens the modal at Security. Desktop + mobile.
- Visual/architecture gate: no banned tokens/side-stripes; modules carry semantic headers.

## 8. Risks & assumptions

- **Assumption**: per-project override semantics are acceptable for Autonomy and Security
  via the generic store; Budget reuses `budget_rules` scope. If a subsystem rejects a
  project-scoped value at runtime, the resolver still reports it (fail-closed on write).
- **Risk**: removing the `/#settings` route can break deep links/tests — mitigated by
  mapping legacy hashes to modal-open + updating specs.
- **Risk**: the modal is large; section components must stay small and isolated to avoid a
  monolith. Placeholders keep Phase 1 honest and bounded.
- **Out of scope**: a full unified settings service (every subsystem flowing through one
  layer with env>project>general>default precedence). The registry + binding design leaves
  room to grow into it without rework.
