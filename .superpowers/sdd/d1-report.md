# Deliverable 1 Report — Settings Modal Backend Slice

## Task Status

| Task | Description | Status | Commit |
|------|-------------|--------|--------|
| T1 | Phase-26 migration (`settings_value` table) | DONE | `9ee55a3` |
| T2 | Registry + descriptor validation | DONE | `6472082` |
| T3 | SettingsRepository (get/set/clear/list) | DONE | `826a338` |
| T4 | Pure resolver (project > general > default) | DONE | `803823d` |
| T5 | Pydantic contracts (models.py) | DONE | `229cfa6` |
| T6 | API router + registration in api.py | DONE | `e813a14` |
| T7 | OpenAPI client wrappers | DONE | `c0348c8` |

## Commits (first..last)

`9ee55a3`..`c0348c8` — 7 commits on branch `dev`.

## Final test:py summary

**684 passed in 415.54s (0:06:55)**

All gates passed, including:
- `test_source_documentation_headers.py` — all 5 tests green (including the transaction-keyword gate on repository.py)
- `test_vertical_slices_architecture.py` — 28 tests green
- `test_ci_and_openapi_client.py` — green
- `test_i18n_platform.py` — 10 tests green
- `test_settings_slice.py` — 23 tests green

## typecheck:web result

`pnpm run typecheck:web` → 0 errors.

## Deviations from the plan

### SQLite NULL PK behavior (T3 - critical)
**Issue:** The plan's DDL uses `scope_id TEXT` (nullable) in the PRIMARY KEY, but SQLite does not enforce uniqueness on NULL values within a composite PK — each NULL is treated as distinct, so two rows with `scope_id=NULL` for the same `(key, scope)` can coexist. This makes both `ON CONFLICT(key, scope, scope_id)` and `INSERT OR REPLACE` ineffective for the general-scope case.

**Fix applied:** Repository normalizes `scope_id=None` to `''` (empty string) on write/read via `_encode_scope_id`/`_decode_scope_id`. The public API continues to accept `None`; only the storage layer uses `''`. This keeps the DDL exactly as the plan specifies (no schema change) while making the PK conflict resolution deterministic.

**Rationale:** Changing the DDL (e.g., `scope_id TEXT NOT NULL DEFAULT ''`) would deviate from the locked data shape. The encode/decode approach is transparent to callers and reversible.

### Documentation gate for repository.py (T7 - minor)
**Issue:** The `test_source_documentation_headers.py::test_repository_modules_document_transactions` gate requires any `repository.py` docstring to mention "transaction", "transacc", "commit", "atómic", or "atomic". The initial docstring lacked these keywords.

**Fix applied:** Added a sentence to the `repository.py` module docstring explaining that writes are atomic upserts (single-statement transactions). The fix was identified by running `pnpm run test:py` before the final commit.

## Concerns / Risks

1. **settings_value NULL scope_id encoding:** Existing code paths that might directly query `settings_value` with `WHERE scope_id IS NULL` (e.g. a future raw SQL query or external migration tool) would miss rows stored as `''`. The encode/decode is contained in `SettingsRepository` only — any code that bypasses the repository must use `scope_id=''`. This is documented in the repository module.

2. **No explicit transaction wrapping in API routes:** The `put_setting` and `delete_setting` routes each do a single repository write, which is atomic by SQLite's single-statement semantics. If a future route needs multi-step writes (e.g., audit log + set), a transaction boundary must be added explicitly.

3. **scope_id validation:** The PUT handler accepts any `scope_id` string for project-scope writes; it does not verify the `scope_id` references an existing project in the `projects` table. This is consistent with the plan (no FK enforcement in the generic store) but means stale project IDs can accumulate.

## Pending / Next steps

- Tasks 8–14 (frontend: `useSettings` hook, `SettingRow`, `SettingsModal`, section registry, App integration, i18n catalog, Playwright) — out of scope for Deliverable 1.
- Enforcement wiring (listed in plan §Self-review as follow-ups): agent-run path reading resolved `autonomy.level`; sandbox runner consuming `security.sandboxProfileId`; `budget_rules` gaining project scope.
