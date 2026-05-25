# AIDO Implementation Plan

Branch: `codex/aido-control-plane-hardening`

## Principle

No reconstruir el gateway. The repo already has the core Unified Model &
Runtime Gateway, providers, CLI runtimes, DB tables, API endpoints, frontend
page and tests. This iteration hardens the real gaps without deleting existing
behavior.

## Current Iteration

1. Create real-state AIDO docs:
   - `docs/aido-current-state-analysis.md`
   - `docs/aido-architecture-diagram.md`
   - `docs/aido-gap-analysis.md`
   - `docs/aido-implementation-plan.md`
2. Make FAISS optional in `requirements-python.txt` and test it.
3. Expand executable agent role catalog end to end.
4. Add provider metadata and usage source columns through additive migrations.
5. Expose provider `metadata` and usage `usageSource` through API and UI.
6. Add `BudgetRuleEvaluator` and explicit budget results.
7. Extend `QuotaManager` with explicit result dictionaries and window checks.
8. Persist CLI runtime sessions for mock and real attempts with redacted command
   and env policy plus linked usage ledger rows.
9. Harden real CLI execution with workspace registry and security policy checks.
10. Split Model Gateway frontend into operational panel components.
11. Regenerate OpenAPI client.
12. Run Python, typecheck, build, web, lint and security verification.

## Jira Scope

Current iteration epic:

- `AIDO-1` - AIDO Control Plane Hardening - Current Iteration

Current children:

- `AIDO-2` - Document current AIDO architecture and gap state
- `AIDO-3` - Make FAISS optional across requirements and docs
- `AIDO-4` - Complete executable agent role catalog
- `AIDO-5` - Enforce budget and quota rules in route preview and execution
- `AIDO-6` - Persist CLI runtime sessions with usage and artifacts
- `AIDO-7` - Expose usageSource and provider metadata in Model Gateway
- `AIDO-8` - Split Model Gateway frontend into operational panels
- `AIDO-9` - Run full quality, security and OpenAPI verification

Future epics left for later iterations:

- `AIDO-10` - Provider Execution And Health Discovery
- `AIDO-11` - Benchmark-Driven Model Router
- `AIDO-12` - PR, Release And Retrospective Control
- `AIDO-13` - Workflow And Agent Contract Expansion

## Verification Plan

- `uv run pytest tests_py -q`
- `corepack pnpm@10.24.0 run openapi:generate`
- `corepack pnpm@10.24.0 run typecheck:web`
- `corepack pnpm@10.24.0 run build:web`
- `corepack pnpm@10.24.0 run test:web`
- `corepack pnpm@10.24.0 run lint`
- `corepack pnpm@10.24.0 run security:secrets`
- `corepack pnpm@10.24.0 run security:sast`

## Non Goals

- No real provider calls in tests.
- No mandatory n8n or source-available core dependencies.
- No raw API key persistence.
- No direct main edits, production deploys or force pushes.
- No automatic premium/max-performance default routing.
