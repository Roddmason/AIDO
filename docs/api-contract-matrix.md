# AIDO API Contract Matrix

This matrix documents the active Python/FastAPI v1 surface after the clean
cutover. Removed compatibility routes are intentionally not part of the product
contract.

## Strict Schemas

OpenAPI is the source of truth for the frontend client. Runtime/workflow/evidence
contracts expose enum-backed states instead of loose strings for:

- `RuntimeProviderStatus.kind`
- `WorkflowRecord.status`, `WorkflowRunRecord.status`, `WorkflowStepRecord.status`
- `ActionRequestRecord.status`, `ActionRequestRecord.riskLevel`
- `ApprovalGrantRecord.status`
- `AgentRunRecord.status`, `AgentToolCallRecord.status`, `ModelCallRecord.status`
- `EvidencePackageRecord.qaVerdict`, `ArtifactRecord.kind`

`GET /api/v1/workflows/{id}` returns both the legacy flat arrays and
`workflowRunDetails[]`, a grouped run detail contract for new consumers.

## Platform

- `GET /healthz`
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
- `GET /api/v1/approvals` returns contextual `ActionRequest` records with action type, risk, command `argv`, workspace, runtime, diff/evidence refs, reason, and expiration
- `POST /api/v1/jobs/{job_id}/actions/{action_id}/approve` requires non-empty `reason` and returns a scoped, expiring, one-use `permissionGrant`
- `POST /api/v1/jobs/{job_id}/actions/{action_id}/deny` requires non-empty `reason` and blocks later approval
- `POST /api/v1/permissions/grants/{grant_id}/revoke` requires local token and non-empty `reason`

## Workflows

- `GET|POST /api/v1/workflows`
- `GET /api/v1/workflows/{id}`
- `POST /api/v1/workflows/{id}/start`
- `POST /api/v1/workflows/{id}/pause`
- `POST /api/v1/workflows/{id}/resume`
- `POST /api/v1/workflows/{id}/cancel`
- `POST /api/v1/workflows/issue-to-pr` executes the full DeveloperAgent -> QAAgent -> SecurityAgent -> ArchitectAgent -> DevOpsAgent DAG and returns gate evidence, completion, rework, and timeline state
- `POST /api/v1/workflows/issue-to-pr/{run_id}/approve`
- `POST /api/v1/workflows/issue-to-pr/{run_id}/promote`
- `POST /api/v1/workflows/issue-to-pr/{run_id}/pull-request`

## Agents And Runtime

- `GET /api/v1/agents`
- `GET|POST /api/v1/agent-profiles`
- `GET|POST /api/v1/agent-runs`
- `GET /api/v1/runtime/providers`
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

## Model Gateway

- `GET /api/v1/model-gateway/overview`
- `GET|POST /api/v1/model-gateway/providers`
- `PATCH /api/v1/model-gateway/providers/{provider_id}`
- `POST /api/v1/model-gateway/providers/{provider_id}/health-check`
- `POST /api/v1/model-gateway/providers/{provider_id}/discover-models`
- `GET|POST /api/v1/model-gateway/models`
- `PATCH /api/v1/model-gateway/models/{model_id}`
- `GET|POST /api/v1/model-gateway/routing-profiles`
- `PATCH /api/v1/model-gateway/routing-profiles/{profile_id}`
- `GET|POST /api/v1/model-gateway/role-policies`
- `PATCH /api/v1/model-gateway/role-policies/{policy_id}`
- `POST /api/v1/model-gateway/route/preview`
- `POST /api/v1/model-gateway/route/execute`
- `GET /api/v1/model-gateway/usage-ledger`
- `GET /api/v1/model-gateway/usage-ledger/summary`
- `GET /api/v1/model-gateway/routing-decisions`
- `GET /api/v1/model-gateway/provider-limits`
- `GET /api/v1/model-gateway/budget-rules`
- `GET /api/v1/model-gateway/cli-runtimes`
- `GET /api/v1/model-gateway/cli-sessions`

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
