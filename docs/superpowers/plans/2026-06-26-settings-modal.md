# Settings Modal with Inheritance & Source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the route-based settings page with a two-pane settings **modal** (General / Project scopes) backed by a new `settings` vertical slice whose pure resolver labels each value as inherited or overridden with its source.

**Architecture:** A deterministic backend slice (`local_control_center/settings/`) owns a descriptor **registry**, a generic two-tier SQLite store (`settings_value`), and a pure **resolver** (`project > general > default`). A FastAPI router exposes resolve/set/clear. The frontend `features/settings/` modal (built on the `Dialog` primitive) renders every section; three settings — `autonomy.level`, `security.sandboxProfileId`, `budget.maxCostUsd` — are wired end-to-end with inheritance chips; other sections reuse existing panels (display + console link) or show honest placeholders.

**Tech Stack:** Python 3 + FastAPI + SQLite (backend); React + TypeScript + Vite + the existing `Dialog`/`useControlPlane`/`useI18n` (frontend); pytest + Playwright (tests).

## Global Constraints

- **Vertical-slice + deterministic module pattern** — mirror the freshly-added `team_activity` slice: `__init__.py` (semantic docstring), `models.py` (Pydantic `_Aliased` base, `populate_by_name`, camelCase aliases), `service.py`/resolver (pure builder `fn(*, connection, ...) -> dict`), `repository.py` (`__init__(self, connection)`, `self.connection.execute`), `api.py` (`create_router(*, platform, require_write) -> APIRouter`).
- **Local-token guard** — every write route (`PUT`/`DELETE`) calls `require_write(request)` first; reads omit it. `require_write` checks header `X-Local-Control-Token` vs `platform.get_handshake()["token"]`, raising `HTTPException(403)`.
- **Migrations** — add **phase 26** in `local_control_center/shared/migrations.py`: `init_phase26_schema(connection)` with `CREATE TABLE IF NOT EXISTS` + `INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (26, utc_now())`, and call it from `initialize_platform_schema()` after phase 25.
- **i18n gate** — every visible English literal in a `.tsx` must be `t('key','English')` AND a `default_catalog.json` entry whose `en` ≠ `es`. File is **LF**. Catalog shape: `{ "translations": { "<key>": { "en": "...", "es": "..." } } }`.
- **Documentation headers** — every new `.py` module: first docstring line ≥30 chars, semantic, no generic marker. Every new `.tsx`: leading `/** ... */` whose description (after stripping `@tags`) is ≥30 chars — **no `{@link}` in the first sentence** (it truncates the scanned description).
- **Visual gate** — no Inter/cyan/purple/radial-gradient; chips use full border-color, never side-stripes (`border-left/right ≥2px`).
- **Verification gates (all must pass)** — `pnpm run typecheck:web`; `pnpm run check:web` (exit 0; warnings are a ratchet baseline, don't add errors); `pnpm run test:py`; `pnpm run build:web`; full `pnpm run test:web` (run UNPIPED — never `| tail`; read the runner's real exit). Read the *file* for pass/fail, not the notification.
- **No fabricated settings** — the three wired settings are real persisted+resolved preferences with real inheritance/source display. Their runtime *enforcement* (subsystems consuming the resolved value) is out of Phase 1 scope and is listed as a follow-up — do not claim a setting changes runtime behavior it does not yet change.
- **Commits** — format `Tipo (Ámbito): mensaje`; end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. One commit per task.

---

## File Structure

**Backend (new `local_control_center/settings/`):**
- `__init__.py` — slice docstring.
- `registry.py` — `SettingDescriptor` dataclass + `REGISTRY: list[SettingDescriptor]` (the 3 Phase-1 keys) + `validate_value(descriptor, value)`.
- `repository.py` — `SettingsRepository(connection)`: `get_value`, `set_value`, `clear_value`, `list_values`.
- `resolver.py` — `resolve_settings(*, connection, project_id) -> dict` (pure; returns `{general:[...], project:[...]}`).
- `models.py` — Pydantic contracts: `ResolvedSetting`, `SettingsResponse`, `SetSettingRequest`.
- `api.py` — `create_router(*, platform, require_write) -> APIRouter` (GET/PUT/DELETE).

**Backend (modified):**
- `shared/migrations.py` — add `init_phase26_schema` + register.
- `api.py` (app factory) — `include_router(create_settings_router(...))`.

**Frontend (new `features/settings/`):**
- `useSettings.ts` — fetch resolved settings + `setValue`/`clearValue`.
- `SettingRow.tsx` — one setting: value control + inheritance chip + set/revert.
- `SettingsModal.tsx` — Dialog two-pane (section nav + content + search).
- `sections.ts` — section registry (id, scope, title key, kind: wired|display|placeholder, render).
- `SectionPlaceholder.tsx` — honest "not configured yet".

**Frontend (modified):**
- `api/client.ts` (+ regenerated `api/generated/openapi.ts`, `api/types.ts`) — `getSettings`/`putSetting`/`deleteSetting`.
- `app/App.tsx` — modal state + `openSettings(section?)` + render; legacy-hash → open modal.
- `app/routes.tsx`, `app/routing.ts`, `app/navigation.ts` — remove `settings-*` routes; map legacy hashes to modal open.
- `features/settings/SettingsPage.tsx` — keep its body components (`ProjectBody`, `RuntimeBody`, `AgentsBody`, `SecurityBody`, `WorkspacesBody`, `IntegrationsBody`, `AdvancedBody`, `CredentialManagerPanel`, `RuntimeSetupPanel`) for reuse inside modal sections; remove the page-level `SettingsPage` export + its route usage.
- `design-system/layout.css` — modal + section-nav + setting-row + chip styles.
- `i18n/default_catalog.json` — new keys.
- `tests_web/control-center.spec.js` — replace settings-route assertions with modal assertions.

---

## Data shapes (locked — used across tasks)

**`SettingDescriptor` (registry.py):**
```python
@dataclass(frozen=True)
class SettingDescriptor:
    key: str                 # e.g. "autonomy.level"
    section: str             # general section id, e.g. "autonomy"
    project_section: str | None  # project section id if overridable, else None
    type: str                # "enum" | "number" | "string"
    default: Any             # default when neither general nor project set
    enum: tuple[str, ...] | None = None   # for type=="enum"
    label_key: str = ""      # i18n key for the setting label
```

**Phase-1 `REGISTRY`:**
```python
REGISTRY = [
    SettingDescriptor(key="autonomy.level", section="autonomy", project_section="security",
        type="enum", default="guided", enum=("guided","recommended","autonomous"),
        label_key="app.settings.autonomy.level"),
    SettingDescriptor(key="security.sandboxProfileId", section="security", project_section="security",
        type="string", default="", label_key="app.settings.security.sandboxProfile"),
    SettingDescriptor(key="budget.maxCostUsd", section="costs", project_section="budget",
        type="number", default=0, label_key="app.settings.budget.maxCostUsd"),
]
```
> `autonomy.level`'s project override surfaces in the **Project → Security** section header group with autonomy; finalize exact placement in Task 10 (kept consistent with the section list). `enum` member labels for sandbox come from `overview.sandboxProfiles` on the frontend.

**`settings_value` table (phase 26):**
```sql
CREATE TABLE IF NOT EXISTS settings_value (
    key TEXT NOT NULL,
    scope TEXT NOT NULL,            -- 'general' | 'project'
    scope_id TEXT,                  -- NULL for general; project id for project
    value_json TEXT NOT NULL,       -- json-encoded value
    updated_at TEXT NOT NULL,
    PRIMARY KEY (key, scope, scope_id)
);
```

**`ResolvedSetting` (resolver output, camelCase via models.py):**
```
{ key, section, projectSection|null, type, enum|null, value,
  origin: 'default'|'general'|'project', inherited: bool, source: str, editableScopes: ['general'|'project'] }
```
- `source` is a stable token the frontend localizes: `'default'` | `'general'` | `'project'`.
- Precedence: project override (scope='project', scope_id=projectId) > general (scope='general') > descriptor.default.
- `inherited` = (project scope requested AND no project override exists). For general scope, `inherited=False`, `origin∈{default,general}`.

**API:**
- `GET /api/v1/settings?projectId=<id>` → `SettingsResponse { general: ResolvedSetting[], project: ResolvedSetting[] }`. `general` resolves each descriptor at general scope; `project` resolves each project-overridable descriptor for `projectId`.
- `PUT /api/v1/settings/{key}` body `SetSettingRequest { scope, scopeId?, value }` → `204` (re-resolve client-side). Validates against descriptor; invalid → `422`.
- `DELETE /api/v1/settings/{key}?scope=&scopeId=` → `204` (clears override; reverts to inherited).

---

### Task 1: Phase-26 migration — `settings_value` table

**Files:**
- Modify: `local_control_center/shared/migrations.py` (add `init_phase26_schema`, register in `initialize_platform_schema`)
- Test: `tests_py/test_settings_slice.py` (new)

**Interfaces:**
- Produces: table `settings_value(key, scope, scope_id, value_json, updated_at)`; phase 26 in `schema_migrations`.

- [ ] **Step 1 — failing test:** in `tests_py/test_settings_slice.py`, create a `ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path/"platform.sqlite")`, then assert the table exists: `SELECT name FROM sqlite_master WHERE type='table' AND name='settings_value'` returns a row, and `SELECT version FROM schema_migrations WHERE version=26` returns 26. Use the `_runtime(tmp_path)` helper pattern from `tests_py/test_team_activity_slice.py`.
- [ ] **Step 2 — run, expect FAIL:** `pnpm run test:py -- -k settings_slice` (or `uv run pytest tests_py/test_settings_slice.py -q`). Expect failure (table missing).
- [ ] **Step 3 — implement:** add `init_phase26_schema(connection)` with the `settings_value` DDL above + the phase-26 `schema_migrations` insert (semantic docstring ≥30 chars); call it from `initialize_platform_schema()` immediately after the phase-25 call.
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `Feature (Settings): tabla settings_value (migración fase 26) para el store de dos niveles`.

---

### Task 2: Registry + value validation

**Files:**
- Create: `local_control_center/settings/__init__.py`, `local_control_center/settings/registry.py`
- Test: `tests_py/test_settings_slice.py`

**Interfaces:**
- Produces: `SettingDescriptor`, `REGISTRY`, `descriptor_for(key) -> SettingDescriptor | None`, `validate_value(descriptor, value) -> Any` (coerces/validates; raises `ValueError` on invalid).

- [ ] **Step 1 — failing tests:** `validate_value` for `autonomy.level` accepts `"guided"`, rejects `"yolo"` (ValueError); for `budget.maxCostUsd` accepts `10.5`/`"10.5"`→`10.5`, rejects `"abc"`; `descriptor_for("autonomy.level").default == "guided"`; `descriptor_for("nope") is None`.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** `registry.py` (dataclass + `REGISTRY` above + `descriptor_for` + `validate_value` doing enum membership / numeric coercion / string passthrough). `__init__.py` gets a semantic slice docstring.
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `Feature (Settings): registro de descriptores y validación de valores`.

---

### Task 3: SettingsRepository (generic store CRUD)

**Files:**
- Create: `local_control_center/settings/repository.py`
- Test: `tests_py/test_settings_slice.py`

**Interfaces:**
- Consumes: `settings_value` table (Task 1).
- Produces: `SettingsRepository(connection)` with `set_value(key, scope, scope_id, value)`, `get_value(key, scope, scope_id) -> Any | _UNSET`, `clear_value(key, scope, scope_id) -> bool`, `list_values() -> dict[(key,scope,scope_id), Any]`. Values are json-encoded in storage, decoded on read. Use a module sentinel `UNSET` to distinguish "no row" from a stored falsy value.

- [ ] **Step 1 — failing tests:** round-trip `set_value("autonomy.level","general",None,"recommended")` then `get_value(...) == "recommended"`; `get_value` of an unset key returns `UNSET`; `set_value` again upserts (no duplicate PK error); `clear_value` returns True when a row existed, False otherwise and `get_value` returns `UNSET` after.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** repository (`INSERT ... ON CONFLICT(key,scope,scope_id) DO UPDATE`, `json.dumps`/`json.loads`, `utc_now()` for `updated_at`).
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `Feature (Settings): repositorio del store de ajustes (get/set/clear/list)`.

---

### Task 4: Pure resolver (project > general > default)

**Files:**
- Create: `local_control_center/settings/resolver.py`
- Test: `tests_py/test_settings_slice.py`

**Interfaces:**
- Consumes: `REGISTRY` (Task 2), `SettingsRepository` (Task 3).
- Produces: `resolve_settings(*, connection, project_id: str | None) -> dict` returning `{"general": [...], "project": [...]}` where each item is the `ResolvedSetting` dict (camelCase keys). General list = every descriptor resolved at general scope (origin `general` if a general value exists else `default`, `inherited=False`). Project list = every descriptor with `project_section` resolved for `project_id`: origin `project` (+`inherited=False`) if a project value exists, else origin `general`/`default` with `inherited=True`. `editableScopes` = `["general"]` for general list, `["project"]` for project list.

- [ ] **Step 1 — failing tests:** with nothing set, `general` `autonomy.level` → `{value:"guided", origin:"default", inherited:False, source:"default"}`. After `set_value("autonomy.level","general",None,"recommended")`: general → `{value:"recommended", origin:"general", source:"general"}` and project (no project override) → `{value:"recommended", origin:"general", inherited:True, source:"general"}`. After `set_value("autonomy.level","project",pid,"autonomous")`: project → `{value:"autonomous", origin:"project", inherited:False, source:"project"}`.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** resolver (pure; reads via `SettingsRepository`, applies precedence, builds dicts). Module docstring ≥30 chars.
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `Feature (Settings): resolutor puro con herencia (proyecto > general > default) y origen`.

---

### Task 5: Pydantic contracts (models.py)

**Files:**
- Create: `local_control_center/settings/models.py`
- Test: `tests_py/test_settings_slice.py`

**Interfaces:**
- Produces: `ResolvedSetting`, `SettingsResponse{ general: list[ResolvedSetting], project: list[ResolvedSetting] }`, `SetSettingRequest{ scope: str, scopeId: str | None = None, value: Any }` — all on an `_Aliased` base (`populate_by_name=True`, camelCase aliases) mirroring `team_activity/models.py`.

- [ ] **Step 1 — failing test:** `SettingsResponse(**resolve_settings(connection=conn, project_id=pid)).model_dump(by_alias=True)` round-trips without error and exposes `inherited`/`origin`/`source` camelCase keys; `SetSettingRequest.model_validate({"scope":"general","value":"guided"})` works (scopeId optional).
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** `models.py`.
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `Feature (Settings): contratos Pydantic de la API de ajustes`.

---

### Task 6: API router + registration

**Files:**
- Create: `local_control_center/settings/api.py`
- Modify: `local_control_center/api.py` (import + `include_router`)
- Test: `tests_py/test_settings_slice.py`

**Interfaces:**
- Consumes: resolver (Task 4), repository (Task 3), registry validation (Task 2), models (Task 5), `require_write`.
- Produces: routes `GET /api/v1/settings`, `PUT /api/v1/settings/{key}`, `DELETE /api/v1/settings/{key}`.

- [ ] **Step 1 — failing tests** (TestClient pattern from `test_team_activity_slice.py`):
  - `GET /api/v1/settings?projectId=<pid>` → 200, body has `general`/`project` arrays; `general` contains `autonomy.level` with `value=="guided"`.
  - `PUT /api/v1/settings/autonomy.level` with header token, body `{"scope":"general","value":"recommended"}` → 204; subsequent GET shows general `autonomy.level` value `recommended`, project `inherited==true`.
  - `PUT` body `{"scope":"project","scopeId":pid,"value":"autonomous"}` → 204; GET project `autonomy.level` `origin=="project"`.
  - `DELETE /api/v1/settings/autonomy.level?scope=project&scopeId=<pid>` (with token) → 204; GET project back to `inherited==true`.
  - `PUT` invalid value `{"scope":"general","value":"yolo"}` → 422.
  - `PUT` without token header → 403.
  - `PUT` unknown key `/api/v1/settings/nope.key` → 404.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** `create_router(*, platform, require_write)`: GET calls `resolve_settings`; PUT validates `descriptor_for(key)` (404 if none), `validate_value` (422 on ValueError), `require_write(request)`, then `SettingsRepository.set_value`; DELETE calls `require_write` then `clear_value`. Register in `api.py` after the team_activity include.
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `Feature (Settings): API resolve/set/clear con guard de token y validación`.

---

### Task 7: OpenAPI client wrappers

**Files:**
- Modify (regenerate): `local-control-center/web/src/api/generated/openapi.ts`, `api/types.ts`
- Modify: `local-control-center/web/src/api/client.ts`

**Interfaces:**
- Produces: `getSettings(projectId, signal?) : Promise<SettingsResponse>`, `putSetting(key, body, token) : Promise<void>`, `deleteSetting(key, params, token) : Promise<void>` plus `SettingsResponse`/`ResolvedSetting` types — following the `getProjectTeamActivity` wrapper pattern.

- [ ] **Step 1 — regenerate:** run the OpenAPI generator (`uv run python local-control-center/scripts/generate_openapi_client.py`); confirm the three new operations appear in `api/generated/openapi.ts`.
- [ ] **Step 2 — add wrappers** in `client.ts` (typed via `requestGeneratedOperation<operationId, T>`); for PUT/DELETE pass the local token header the same way other write wrappers do (inspect an existing write wrapper to match header injection).
- [ ] **Step 3 — typecheck:** `pnpm run typecheck:web` → 0 errors.
- [ ] **Step 4 — commit:** `Feature (Settings): cliente tipado generado + wrappers getSettings/putSetting/deleteSetting`.

---

### Task 8: `useSettings` hook

**Files:**
- Create: `local-control-center/web/src/features/settings/useSettings.ts`
- Test: covered by the Playwright flow (Task 14); no unit harness for hooks here.

**Interfaces:**
- Consumes: `getSettings`/`putSetting`/`deleteSetting` (Task 7), `useControlPlane` token.
- Produces: `useSettings(projectId, enabled) -> { general, project, loading, error, refresh, setValue(key,scope,scopeId,value), clearValue(key,scope,scopeId) }` (mirror `useTeamActivity` fetch/abort pattern; `setValue`/`clearValue` call the API then `refresh()`).

- [ ] **Step 1 — implement** the hook (fetch on `enabled`, AbortController, `setValue`/`clearValue` → API → `refresh`). Module header ≥30 chars, no `{@link}` first sentence.
- [ ] **Step 2 — typecheck:** `pnpm run typecheck:web` → 0 errors.
- [ ] **Step 3 — commit:** `Feature (Settings): hook useSettings (resolved + set/clear)`.

---

### Task 9: `SettingRow` + inheritance chip + styles

**Files:**
- Create: `local-control-center/web/src/features/settings/SettingRow.tsx`
- Modify: `local-control-center/web/src/design-system/layout.css`
- Test: Playwright (Task 14).

**Interfaces:**
- Consumes: a `ResolvedSetting` + `onSet(value)` + `onRevert()`; for enum/sandbox, an options list.
- Produces: `SettingRow` rendering the value control (select for enum, number input, text), the chip — `Inherited · General` / `Overridden · Project` (project scope) or `Default` / `Custom` (general scope) — and the affordance (`Set for this project` reveals control; `Revert to General` calls `onRevert`).

- [ ] **Step 1 — implement** `SettingRow` + CSS (`.setting-row`, `.setting-chip[data-origin]`, `.setting-control`). Chip uses full border-color (no side-stripe). All copy via `t()`.
- [ ] **Step 2 — typecheck + check:web** → 0 errors / exit 0.
- [ ] **Step 3 — commit:** `Feature (Settings): SettingRow con chip de herencia/origen y acciones set/revert`.

---

### Task 10: Section registry + content (wired / display / placeholder)

**Files:**
- Create: `local-control-center/web/src/features/settings/sections.ts`, `features/settings/SectionPlaceholder.tsx`
- Modify: `features/settings/SettingsPage.tsx` (export its body components for reuse; keep them; remove only the page wrapper/route export in Task 12)
- Test: Playwright (Task 14).

**Interfaces:**
- Produces: `GENERAL_SECTIONS` and `PROJECT_SECTIONS` arrays of `{ id, titleKey, kind: 'wired'|'display'|'placeholder', render(ctx) }` where ctx gives `{ resolved, overview, selectedProject, setValue, clearValue, t }`.
- Section taxonomy: **General** = General, Appearance, Providers & CLI, Credentials, Default Team, Autonomy (wired), Security (wired), Research, Costs (wired: budget cap), Integrations, Advanced. **Project** = Project, Goal, Team, Routing, Quality, Security (wired override), Workspaces, Internet, Budget (wired override), Credentials, Integrations, Advanced.
- *wired* sections render `SettingRow`s for their `resolved` settings. *display* sections reuse existing `SettingsPage` bodies (`ProjectBody`, `RuntimeBody`/`RuntimeSetupPanel`, `AgentsBody`, `WorkspacesBody`, `IntegrationsBody`, `CredentialManagerPanel`, `AdvancedBody`) + a `ConsoleLink`. *placeholder* sections render `SectionPlaceholder` (Goal, Research, Internet, Default Team).

- [ ] **Step 1 — implement** `sections.ts` + `SectionPlaceholder.tsx`; reuse existing bodies for display sections; wire Autonomy/Security/Costs (general) and Security/Budget (project) to `SettingRow`. Appearance reuses the existing theme/density/language controls (source label "This device"). All titles/labels via `t()`.
- [ ] **Step 2 — typecheck + check:web.**
- [ ] **Step 3 — commit:** `Feature (Settings): registro de secciones (cableadas / display+link / placeholder)`.

---

### Task 11: `SettingsModal` (two-pane Dialog)

**Files:**
- Create: `local-control-center/web/src/features/settings/SettingsModal.tsx`
- Modify: `design-system/layout.css`
- Test: Playwright (Task 14).

**Interfaces:**
- Consumes: `Dialog` (`{open,onClose,label,children,className}`), `useSettings`, `sections.ts`, overview/selectedProject.
- Produces: `SettingsModal({ open, onClose, projectId, initialSection })` — a wide modal (`className="settings-modal"`) with a left **section navigator** (two labelled groups General/Project + a search `<input>` filtering section titles) and a right content panel rendering the active section. Active section is local state seeded from `initialSection`.

- [ ] **Step 1 — implement** `SettingsModal` + CSS (`.settings-modal` width ~min(64rem,92vw), `.settings-nav`, `.settings-content`, grid two-pane; collapses to stacked under the mobile breakpoint). `aria` labels via `t()`; the Dialog `label` = `t('app.settings.title','Settings')`.
- [ ] **Step 2 — typecheck + check:web + build:web.**
- [ ] **Step 3 — commit:** `Feature (Settings): SettingsModal de dos paneles (navegador de secciones + contenido + búsqueda)`.

---

### Task 12: Wire modal into App; remove settings route

**Files:**
- Modify: `app/App.tsx`, `app/routes.tsx`, `app/routing.ts`, `app/navigation.ts`, `features/settings/SettingsPage.tsx` (drop the page/route export, keep bodies)
- Test: Playwright (Task 14).

**Interfaces:**
- Consumes: `SettingsModal` (Task 11).
- Produces: App owns `settingsModalOpen` + `settingsSection` + `openSettings(section?)`; renders `<SettingsModal open={settingsModalOpen} ... />`; every `navigateTo('settings-*')`/`onOpenSettings` caller now calls `openSettings(section)`; sidebar footer "Settings" + menu "Settings" open the modal; legacy `#settings`/`#settings-*` hashes resolve to opening the modal at the mapped section (handle in the hashchange effect, then clear the hash or route to `home`).

- [ ] **Step 1 — implement:** add modal state + `openSettings`; render modal; in the `hashchange`/initial-resolve path, detect `settings*` hashes → `openSettings(mappedSection)` and fall back the page to `home`/`threads`; remove the 7 `settings-*` entries from `routeTable`/`pageIds`/`AREAS`; update `areaForPage`/`titleForPage`; repoint `routing.ts` aliases; replace `SettingsPage` route render with nothing (delete `settingsEntry` + `importSettings` route usage) while keeping the body components importable by `sections.ts`.
- [ ] **Step 2 — typecheck + check:web + build:web** → all clean.
- [ ] **Step 3 — commit:** `Feature (Settings): el modal reemplaza la ruta /#settings; hashes heredados lo abren en su sección`.

---

### Task 13: i18n catalog keys

**Files:**
- Modify: `local_control_center/i18n/default_catalog.json`
- Test: `pnpm run test:py` (i18n gate).

**Interfaces:**
- Produces: every new `t('key', 'English')` used in Tasks 9–12 has a catalog entry with distinct ES (modal title, section titles for all 23 sections, chip labels — inherited/overridden/default/custom, set/revert affordances, placeholder copy, source labels default/general/project, the 3 setting labels).

- [ ] **Step 1 — add** all new keys (LF, EN≠ES). Cross-check against the `t(...)` calls introduced in Tasks 9–12 (grep the new files for `t('`).
- [ ] **Step 2 — run** `pnpm run test:py` → green (i18n + architecture + doc-header gates).
- [ ] **Step 3 — commit:** `Feature (Settings): catálogo i18n bilingüe para el modal de ajustes`.

---

### Task 14: Playwright coverage + spec cleanup

**Files:**
- Modify: `tests_web/control-center.spec.js`
- Test: full `pnpm run test:web` (UNPIPED).

**Interfaces:**
- Consumes: the whole feature.

- [ ] **Step 1 — update specs:** replace assertions that navigate to `#settings*` routes / expect the `SettingsPage` heading with modal-based flows. Add a test: open settings (via the sidebar/menu entry) → modal dialog visible (`getByRole('dialog', { name: 'Settings' })`); click **Autonomy** in the nav → the `autonomy.level` control shows chip; set a **Project** override → chip flips to "Overridden · Project"; **Revert to General** → chip back to "Inherited · General". Add: legacy `#settings-security` opens the modal at Security. Keep desktop + mobile.
- [ ] **Step 2 — run gates UNPIPED:** `pnpm run typecheck:web`, `pnpm run check:web`, `pnpm run test:py`, `pnpm run build:web`, then `rm -rf local-control-center/dist/web && pnpm run test:web` — read the output file for `failed`/`passed`, not the notification.
- [ ] **Step 3 — commit:** `Test (Settings): cobertura e2e del modal (navegación, override, revert, hash heredado)`.

---

## Self-review notes
- **Spec correction:** the spec's "Budget reuses `budget_rules`' global-vs-project scope" is infeasible (`scope_type` has no `project`); Phase 1 stores `budget.*` in the generic two-tier store like the other two. Update the spec's §3 budget row + the `adapter:budget` mention to match (generic store; `budget_rules` enforcement integration is a follow-up).
- **Enforcement follow-ups (out of Phase 1, list in the PR):** agent-run path reads resolved `autonomy.level`; sandbox runner consumes `security.sandboxProfileId`; `budget_rules` gains a `project` scope so `budget.maxCostUsd` enforces. These are deliberately deferred — Phase 1 ships the settings plane + inheritance UI, not the enforcement wiring.
- **Coverage:** every spec section maps to a task (slice→T1–7, modal→T8–12, i18n→T13, tests→T14). No placeholders left in steps; types (`ResolvedSetting`, `SettingDescriptor`, `useSettings` return) are consistent across tasks.
