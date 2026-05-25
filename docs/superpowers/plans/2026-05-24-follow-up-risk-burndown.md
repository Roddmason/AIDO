# Follow-up Risk Burndown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining actionable risks after the operational hardening sprint: gate advancement, governed real provider discovery, and auditable pricing snapshots.

**Architecture:** Keep the work additive and local-first. Workflow gates advance through explicit API calls that validate evidence/approvals and write events/audit rows. Provider discovery remains mock by default and real only when the provider is enabled, credentials are valid, and `AIDO_ENABLE_REAL_PROVIDER_CALLS=true`. Pricing snapshots are append-only audit records that can update `model_catalog` prices without treating seed prices as truth.

**Tech Stack:** Python/FastAPI, SQLite migrations, existing repository slices, Pydantic OpenAPI models, React generated client only after API changes.

---

### Task 1: Workflow Gate Advancement

**Files:**
- Modify: `local_control_center/workflows/models.py`
- Modify: `local_control_center/workflows/repository.py`
- Modify: `local_control_center/workflows/api.py`
- Test: `tests_py/test_workflow_pr_release_retro_control.py`
- Docs: `docs/workflows.md`

- [x] Write failing tests proving `pr_review` cannot advance without passed QA evidence, advances with passed QA evidence, production `release_gate` cannot advance until approval is resolved, and `retro` can complete after governance records exist.
- [x] Run the focused workflow test and verify the expected failure.
- [x] Add repository methods to fetch/update workflow steps and evaluate gate advancement.
- [x] Add `POST /api/v1/workflows/{workflow_id}/steps/{step_id}/advance`.
- [x] Record `workflow.gate.*.advanced` or `workflow.gate.*.blocked` events and audit rows.
- [x] Run the focused workflow tests and update docs.

### Task 2: Governed Provider Model Discovery

**Files:**
- Modify: `local_control_center/agents/model_gateway_api.py`
- Test: `tests_py/test_model_runtime_gateway.py`
- Docs: `docs/provider-accounts.md`, `docs/model-gateway.md`

- [x] Write failing tests proving `/discover-models` uses mock by default, blocks real discovery when real calls are disabled, rejects missing credentials before network, and stores provider-sourced models when a provider adapter mock returns real-mode model info.
- [x] Run focused tests and verify the expected failure.
- [x] Change discovery to use real mode only when provider is enabled and `AIDO_ENABLE_REAL_PROVIDER_CALLS=true`.
- [x] Validate credential refs before API/gateway discovery; never fetch raw keys for status checks.
- [x] Record redacted audit metadata including `mock`, `source`, and count.
- [x] Run focused tests and update docs.

### Task 3: Auditable Pricing Snapshots

**Files:**
- Modify: `local_control_center/shared/migrations.py`
- Modify: `local_control_center/agents/provider_accounts.py`
- Modify: `local_control_center/agents/model_gateway_models.py`
- Modify: `local_control_center/agents/model_gateway_api.py`
- Test: `tests_py/test_model_runtime_gateway.py`
- Docs: `docs/model-gateway.md`, `docs/model-routing.md`

- [x] Write failing tests proving pricing snapshots are append-only, redacted, update `model_catalog` prices/source when requested, and are listed by API.
- [x] Run focused tests and verify the expected failure.
- [x] Add `pricing_snapshots` table and repository methods.
- [x] Add `POST /api/v1/model-gateway/pricing-snapshots` and `GET /api/v1/model-gateway/pricing-snapshots`.
- [x] Keep unknown prices unknown; do not infer free pricing unless `freeTier=true`.
- [x] Regenerate OpenAPI, run focused tests and update docs.

### Final QA

- [ ] Run `uv run pytest tests_py -q`.
- [ ] Run `corepack pnpm@10.24.0 run typecheck:web`.
- [ ] Run `corepack pnpm@10.24.0 run build:web`.
- [ ] Run `corepack pnpm@10.24.0 run test:web`.
- [ ] Run `corepack pnpm@10.24.0 run lint`.
- [ ] Run `corepack pnpm@10.24.0 run security:secrets`.
- [ ] Run `corepack pnpm@10.24.0 run security:sast`.
- [ ] Update Jira comments and transition generated HUs to Done only after verification.
