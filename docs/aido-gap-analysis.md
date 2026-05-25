# AIDO Gap Analysis

## Ya existe

- FastAPI backend.
- SQLite local.
- Vertical slices.
- ControlCenterRuntime.
- Migraciones.
- EventBus.
- Jobs/approvals.
- Workflows.
- Workspaces.
- Git worktree support.
- Security policy.
- Command classifier.
- Tool broker.
- Restricted subprocess sandbox.
- Agent profiles.
- Agent runs.
- Agent tool calls.
- Model providers.
- Model policies.
- Model calls.
- Cost usage.
- Evidence packages.
- QA verdicts.
- Test results.
- Artifacts.
- Governance.
- MCP registry basico.
- Retrieval NumPy/FAISS opcional.
- Frontend Vite/React/TypeScript.
- OpenAPI generated client.
- Tests Python amplios.
- Unified Model & Runtime Gateway.
- Provider accounts.
- Model catalog.
- Routing profiles.
- Role model policies.
- Usage ledger.
- Provider limits.
- Budget rules.
- Routing decisions.
- CLI sessions.
- NVIDIA NIM provider mock.
- Codex CLI adapter.
- Claude Code CLI adapter.
- OpenHands and SWE-agent adapters.
- Frontend Model Gateway operativo.

## Falta crear

- Full artifact writer for CLI stdout/stderr/logs across every runtime path.
- Provider execution health discovery against real providers with safe mocks.
- Benchmark-driven router that uses statistically useful performance data.
- PR, release and retrospective control workflow.
- Deeper workflow/agent contract expansion for every SDLC phase.

## Falta ajustar

- `provider_accounts.metadata_json` added as structured provider metadata.
- `usage_ledger.usage_source` added as first-class source field.
- `faiss-cpu` removed from mandatory `requirements-python.txt`.
- Expanded executable roles: `technical_lead_shadow`, `backend_engineer`,
  `frontend_engineer`, `implementer`, `qa_reviewer`.
- Route preview now exposes `budgetResult` and `quotaResult`.
- Model Gateway frontend split into panel component shells.

## Falta endurecer

- Budget checks before real provider and CLI calls.
- Quota cooldown/window diagnostics before route selection.
- Workspace registry validation before real CLI execution.
- Security policy validation before real CLI execution.
- Redaction for provider metadata, CLI command/env policy and usage payloads.
- Real provider calls and CLI runtimes remain disabled by default.

## Falta documentar

- Current state and architecture were missing in `docs/aido-*`.
- Budget/quota output needs to be reflected in Model Gateway docs.
- CLI session persistence needs explicit docs for mock and real paths.
- Provider metadata and `usageSource` need docs/API notes.

## Falta probar

- FAISS optional requirements guardrail.
- Provider metadata migration and redaction.
- Usage source persistence.
- Expanded agent roles through API validation.
- Budget deny, approval, fallback and warn behavior.
- Quota cooldown/window behavior.
- Route preview budget/quota result output.
- CLI mock session persistence and usage linkage.
- Frontend panel structure and `usageSource` rendering.
