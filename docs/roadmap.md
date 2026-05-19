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
- Git worktree allocation when the project is a Git repository, with safe
  directory fallback when it is not.

## Next Backend Work

1. Move remaining `PlatformStore` SQL into owned repositories.
1. Add command-argument allowlists for write/install/network commands.
2. Implement git worktree cleanup/archive through `git worktree remove`.
3. Link workflows to jobs and agent runs in addition to workspaces/evidence.
4. Add MCP gateway and optional OpenHands/SWE-agent adapters.
5. Add artifact ingestion and QA report export.

## Frontend Work

1. Keep current React dashboard operational.
2. Add views for workflows, evidence, policy decisions, and agent runs using
   real API data.
3. Introduce TypeScript/Vite in parallel only after smoke tests cover the new
   control-plane surfaces.
