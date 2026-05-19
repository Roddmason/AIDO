# Local Control Center API Contract Matrix

The Python FastAPI backend must cover these Node dashboard contracts before the Node backend is archived.

## Platform API

- `GET /healthz`
- `GET /api/v1/security/handshake`
- `GET /api/v1/overview`
- `GET /api/v1/events`
- `GET /api/v1/project-templates`
- `GET|POST /api/v1/projects`
- `GET /api/v1/providers`
- `GET /api/v1/teams`
- `GET /api/v1/agents`
- `GET|POST /api/v1/jobs`
- `POST /api/v1/jobs/:id/approve`
- `POST /api/v1/jobs/:id/cancel`
- `POST /api/v1/jobs/:id/retry`
- `GET /api/v1/approvals`
- `POST /api/v1/jobs/:jobId/actions/:actionId/approve`
- `POST /api/v1/jobs/:jobId/actions/:actionId/deny`
- `GET|POST /api/v1/memory`
- `GET|POST /api/v1/prompts`
- `GET|POST /api/v1/ide-connections`
- `GET /api/v1/open-design`
- `GET /api/v1/retrieval/status`
- `POST /api/v1/retrieval/search`
- `POST /api/v1/retrieval/reindex`
- `GET|POST /api/v1/workflows`
- `GET /api/v1/workflows/:id`
- `POST /api/v1/workflows/:id/start`
- `POST /api/v1/workflows/:id/pause`
- `POST /api/v1/workflows/:id/resume`
- `POST /api/v1/workflows/:id/cancel`
- `GET /api/v1/policies`
- `POST /api/v1/policies/evaluate`
- `GET|POST /api/v1/evidence`
- `GET /api/v1/evidence/:id`
- `GET|POST /api/v1/agent-profiles`
- `GET|POST /api/v1/agent-runs`
- `GET /api/v1/skills`
- `POST /api/v1/skills/sync`
- `GET|POST /api/v1/workspaces`
- `POST /api/v1/workspaces/:id/archive`
- `GET /api/v1/model-providers`
- `GET|POST /api/v1/model-policies`

## Legacy Dashboard API

- `GET /api/state`
- `GET /api/workspaces`
- `POST /api/workspaces/select`
- `POST /api/workspaces/:id/collapse`
- `POST /api/workspaces/:id/expand`
- `POST /api/workspaces/:id/pin`
- `POST /api/workspaces/:id/unpin`
- `GET /api/git`
- `POST /api/git/checkout`
- `GET|POST /api/sessions`
- `POST /api/sessions/select`
- `POST /api/sessions/:id/clone`
- `POST /api/sessions/:id/pin`
- `POST /api/sessions/:id/unpin`
- `PATCH|DELETE /api/sessions/:id`
- `GET|PATCH /api/config`
- `GET /api/config/options`
- `GET /api/extensions/catalog`
- `POST /api/extensions/marketplaces`
- `POST /api/extensions/plugins/install`
- `POST /api/extensions/plugins/sync-skills`
- `POST /api/extensions/skills/install`
- `POST /api/idea/intake`
- `POST /api/chats/send`
- `GET /api/chats/:id`
- `GET /api/pipelines/:id`
- `POST /api/pipelines/:id/start`
- `POST /api/pipelines/:id/retry`
- `POST /api/pipelines/:id/archive`
- `POST /api/pipelines/:id/stages/retry`
- `POST /api/pipelines/:id/stages/assign`
- `POST /api/pipelines/:id/stages/override`

The active Python coverage is enforced by `tests_py/test_python_control_center.py`
and `tests_py/test_phase2_control_plane_foundation.py`.
Phase 3-6 foundation coverage is enforced by
`tests_py/test_phase3_to_6_control_plane_runtime.py`.
