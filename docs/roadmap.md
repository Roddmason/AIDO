# Roadmap

## Completed Foundation

- Audit and private license baseline.
- PNPM/uv quality scripts.
- Optional FAISS with NumPy fallback.
- Workflow, policy, evidence, agents, model-policy, workspace, and skill tables.
- Internal deterministic policy engine.
- Internal mock agent runtime.
- Evidence QA gate.

## Next Backend Work

1. Move remaining `PlatformStore` SQL into owned repositories.
2. Add path-aware workspace policy enforcement.
3. Implement real git worktree creation/cleanup.
4. Link workflows to jobs, workspaces, agents, and evidence packages.
5. Add MCP gateway and optional OpenHands/SWE-agent adapters.
6. Add artifact ingestion and QA report export.

## Frontend Work

1. Keep current React dashboard operational.
2. Add views for workflows, evidence, policy decisions, and agent runs using
   real API data.
3. Introduce TypeScript/Vite in parallel only after smoke tests cover the new
   control-plane surfaces.
