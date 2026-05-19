# Roadmap

## Completed Foundation

- Audit and private license baseline.
- PNPM/uv quality scripts.
- Optional FAISS with NumPy fallback.
- Workflow, policy, evidence, agents, model-policy, workspace, and skill tables.
- Internal deterministic policy engine.
- Workspace-aware path gating for policy decisions.
- Internal mock agent runtime.
- Evidence QA gate.
- Git worktree allocation and cleanup when the project is a Git repository,
  with safe directory fallback when it is not.
- Governance ledger for architecture decisions, mitigated risks, and
  prioritized next steps exposed through API and dashboard.

## Next Backend Work

1. Move remaining `PlatformStore` SQL into owned repositories.
2. Link policy denials, workflow failures, and QA verdicts to governance risks
   automatically.
3. Add command-argument allowlists for write/install/network commands.
4. Capture dirty state and diff refs before workspace archive.
5. Link workflows to jobs and agent runs in addition to workspaces/evidence.
6. Add MCP gateway and optional OpenHands/SWE-agent adapters.
7. Add artifact ingestion and QA report export.

## Frontend Work

1. Keep current React dashboard operational.
2. Add create/edit forms for governance, workflows, policy decisions, and agent runs using
   real API data.
3. Introduce TypeScript/Vite in parallel only after smoke tests cover the new
   control-plane surfaces.
