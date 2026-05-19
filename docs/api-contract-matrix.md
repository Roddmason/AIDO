# AIDO API Contract Matrix

This matrix documents the active Python/FastAPI v1 surface after the clean
cutover. Removed compatibility routes are intentionally not part of the product
contract.

## Platform

- `GET /healthz`
- `GET /api/v1/health`
- `GET /api/v1/security/handshake`
- `GET /api/v1/overview`
- `GET /api/v1/events`

## Projects, Workspaces, Sessions

- `GET /api/v1/project-templates`
- `GET|POST /api/v1/projects`
- `GET /api/v1/projects/{id}`
- `PATCH /api/v1/projects/{id}`
- `POST /api/v1/projects/{id}/discover`
- `GET|POST /api/v1/workspaces`
- `POST /api/v1/workspaces/{id}/archive`
- `GET /api/v1/sessions`
- `GET /api/v1/chats`
- `GET /api/v1/pipelines`

## Jobs And Approvals

- `GET|POST /api/v1/jobs`
- `POST /api/v1/jobs/{id}/approve`
- `POST /api/v1/jobs/{id}/cancel`
- `POST /api/v1/jobs/{id}/retry`
- `GET /api/v1/approvals`
- `POST /api/v1/jobs/{job_id}/actions/{action_id}/approve` requires non-empty `reason` and returns `permissionGrant`
- `POST /api/v1/jobs/{job_id}/actions/{action_id}/deny`
- `POST /api/v1/permissions/grants/{grant_id}/revoke` requires local token and non-empty `reason`

## Workflows

- `GET|POST /api/v1/workflows`
- `GET /api/v1/workflows/{id}`
- `POST /api/v1/workflows/{id}/start`
- `POST /api/v1/workflows/{id}/pause`
- `POST /api/v1/workflows/{id}/resume`
- `POST /api/v1/workflows/{id}/cancel`

## Agents And Runtime

- `GET /api/v1/agents`
- `GET|POST /api/v1/agent-profiles`
- `GET|POST /api/v1/agent-runs`
- `GET /api/v1/runtime/providers`
- `GET /api/v1/model-providers`
- `GET|POST /api/v1/model-policies`
- `GET /api/v1/skills`
- `POST /api/v1/skills/sync`

## Policy, Evidence, Governance

- `GET /api/v1/policies` returns policies, permission decisions, one-use permission grants, and sandbox profiles
- `POST /api/v1/policies/evaluate`
- `GET /api/v1/sandbox/status` returns Docker availability and the active Docker sandbox profile
- `PATCH /api/v1/sandbox/profiles/{profile_id}` requires local token and non-empty `reason`, validates resource/network limits, and records event/audit entries
- `POST /api/v1/sandbox/profiles/{profile_id}/revoke` requires local token and non-empty `reason`
- `GET|POST /api/v1/evidence`
- `POST /api/v1/evidence` accepts `testResultReports` in `junit` and `pytest` formats and persists normalized `test_results` records
- `POST /api/v1/evidence/artifacts/cleanup` requires local token and deletes only unreferenced artifact files when `dryRun=false`
- `POST /api/v1/evidence/artifacts/retention` requires local token and surfaces expired referenced artifacts as governance risks
- `POST /api/v1/evidence/artifacts/retention/actions` requires local token and non-empty `reason`; `export` records an audit manifest and `delete` removes only verified expired physical files while preserving SQLite artifact rows
- `GET /api/v1/evidence/{id}` returns package, test result records, and artifacts
- `GET /api/v1/evidence/{id}/report` requires local token and exports a Markdown QA report without leaking local artifact paths
- `POST /api/v1/evidence/{id}/artifacts` requires local token and ingests bounded text/base64 artifacts without accepting client-supplied filesystem paths
- `GET /api/v1/evidence/{id}/artifacts/{artifactId}` requires local token and returns a verified artifact file
- `GET|POST /api/v1/architecture-decisions`
- `GET|POST /api/v1/risks`
- `PATCH /api/v1/risks/{id}`
- `GET|POST /api/v1/next-steps`
- `PATCH /api/v1/next-steps/{id}`

## Memory And Retrieval

- `GET|POST /api/v1/memory`
- `GET|POST /api/v1/prompts`
- `GET /api/v1/retrieval/status`
- `POST /api/v1/retrieval/search`
- `POST /api/v1/retrieval/reindex`

## Integrations

- `GET /api/v1/providers`
- `GET /api/v1/teams`
- `GET /api/v1/integrations`
- `POST /api/v1/integrations/mcp/register`
- `GET|POST /api/v1/ide-connections`
- `GET /api/v1/open-design`

Coverage is enforced by `tests_py/test_python_control_center.py`,
`tests_py/test_phase2_control_plane_foundation.py`,
`tests_py/test_phase3_to_6_control_plane_runtime.py`,
`tests_py/test_hard_cutover_no_removed_compat.py`, and Playwright smoke tests under
`tests_web/`.
