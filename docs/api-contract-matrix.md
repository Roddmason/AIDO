# AIDO API Contract Matrix

This matrix documents the active Python/FastAPI v1 surface after the clean
cutover. Removed compatibility routes are intentionally not part of the product
contract.

The matrix below is regenerated from the generated frontend client
`local-control-center/web/src/api/generated/openapi.ts` (`API_ENDPOINTS` /
`OPERATIONS_BY_ID`), which is itself produced from the live OpenAPI schema by
`pnpm run openapi:generate`
(`local-control-center/scripts/generate_openapi_client.py`). Do not edit the
route tables by hand: re-parse `API_ENDPOINTS` after backend changes. Current
total: 238 operations (237 under `/api/v1` plus `GET /healthz`).

## Strict Schemas

OpenAPI is the source of truth for the frontend client. Runtime/workflow/evidence
contracts expose enum-backed states instead of loose strings for:

- `RuntimeProviderStatus.kind`
- `WorkflowRecord.status`, `WorkflowRunRecord.status`, `WorkflowStepRecord.status`
- `ActionRequestRecord.status`, `ActionRequestRecord.riskLevel`
- `ApprovalGrantRecord.status`
- `AgentRunRecord.status`, `AgentToolCallRecord.status`, `ModelCallRecord.status`
- `EvidencePackageRecord.qaVerdict`, `ArtifactRecord.kind`

`GET /api/v1/workflows/{workflow_id}` returns both the legacy flat arrays and
`workflowRunDetails[]`, a grouped run detail contract for new consumers. The
detail contract includes runs, steps, workspaces, jobs, job runs/leases, action
requests, agent runs, tool calls, model calls, permission decisions, evidence
packages, artifacts, test results, and workflow events.

## Platform (control_plane)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/healthz` | Healthz |
| `GET` | `/api/v1/security/handshake` | Handshake |
| `GET` | `/api/v1/overview` | Overview |
| `GET` | `/api/v1/events` | Events |
| `GET` | `/api/v1/telemetry/status` | Telemetry Status |
| `POST` | `/api/v1/local-paths/select-directory` | Select Directory |

## Projects (projects)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/project-templates` | Project Templates |
| `GET` | `/api/v1/projects` | Projects |
| `POST` | `/api/v1/projects` | Create Project |
| `POST` | `/api/v1/projects/discover` | Discover Project |
| `PUT` | `/api/v1/projects/{project_id}/agent-profile-overrides/{profile_id}` | Agent Profile Override |
| `GET` | `/api/v1/projects/{project_id}/artifacts/{artifact_id}` | Get Project Generated Image |
| `POST` | `/api/v1/projects/{project_id}/assessment` | Run Assessment |
| `GET` | `/api/v1/projects/{project_id}/assessments` | List Assessments |
| `GET` | `/api/v1/projects/{project_id}/findings` | List Findings |
| `GET` | `/api/v1/projects/{project_id}/functionality` | Project Functionality |
| `GET` | `/api/v1/providers` | Providers |
| `GET` | `/api/v1/teams` | Teams |

## Git Workspace (git_workspace)

| Method | Path | Summary |
| --- | --- | --- |
| `POST` | `/api/v1/projects/{project_id}/git/branch-policy/apply` | Apply Git Branch Policy |
| `GET` | `/api/v1/projects/{project_id}/git/branches` | Git Branches |
| `POST` | `/api/v1/projects/{project_id}/git/branches` | Create Git Branch |
| `POST` | `/api/v1/projects/{project_id}/git/checkout` | Checkout Git Branch |
| `GET` | `/api/v1/projects/{project_id}/git/diff` | Git Diff |
| `POST` | `/api/v1/projects/{project_id}/git/gitleaks/scan` | Git Gitleaks Scan |
| `POST` | `/api/v1/projects/{project_id}/git/init` | Init Git Repository |
| `POST` | `/api/v1/projects/{project_id}/git/remotes` | Add Git Remote |
| `POST` | `/api/v1/projects/{project_id}/git/remotes/{name}/test` | Test Git Remote |
| `GET` | `/api/v1/projects/{project_id}/git/status` | Git Status |

## Product Loop (product_loop, product_discovery, backlog)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/projects/{project_id}/product-loop` | Get Product Loop State |
| `POST` | `/api/v1/projects/{project_id}/product-loop` | Start Product Loop |
| `POST` | `/api/v1/projects/{project_id}/product-loop/brief/{brief_id}/approve` | Approve Product Brief |
| `GET` | `/api/v1/projects/{project_id}/product-loop/stories/{story_id}/spec` | Get Story Spec |
| `POST` | `/api/v1/projects/{project_id}/product-loop/{loop_id}/aido-decide` | Aido Decide Product Loop |
| `POST` | `/api/v1/projects/{project_id}/product-loop/{loop_id}/backlog/approve` | Approve Product Loop Backlog |
| `POST` | `/api/v1/projects/{project_id}/product-loop/{loop_id}/feedback` | Apply Product Loop Feedback |
| `POST` | `/api/v1/projects/{project_id}/product-loop/{loop_id}/transition` | Transition Product Loop |

## Team Activity (team_activity)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/projects/{project_id}/team-activity` | Team Activity |

## Threads (threads)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/threads` | List Threads |
| `POST` | `/api/v1/threads` | Create Thread |
| `GET` | `/api/v1/threads/similar` | Find Similar Threads |
| `GET` | `/api/v1/threads/{thread_id}` | Get Thread |
| `PATCH` | `/api/v1/threads/{thread_id}` | Update Thread |
| `DELETE` | `/api/v1/threads/{thread_id}` | Delete Thread |
| `POST` | `/api/v1/threads/{thread_id}/archive` | Archive Thread |
| `POST` | `/api/v1/threads/{thread_id}/cancel` | Cancel Execution |
| `GET` | `/api/v1/threads/{thread_id}/cost-performance` | Thread Cost Performance |
| `POST` | `/api/v1/threads/{thread_id}/decisions/{decision_id}/resolve` | Resolve Decision |
| `GET` | `/api/v1/threads/{thread_id}/events` | Thread Events |
| `GET` | `/api/v1/threads/{thread_id}/memory` | Thread Memory |
| `POST` | `/api/v1/threads/{thread_id}/memory/reindex` | Reindex Thread Memory |
| `POST` | `/api/v1/threads/{thread_id}/messages` | Post Message |
| `POST` | `/api/v1/threads/{thread_id}/notes` | Post Note |
| `GET` | `/api/v1/threads/{thread_id}/remediations` | List Thread Remediations |
| `GET` | `/api/v1/threads/{thread_id}/similar` | Find Similar To Thread |
| `POST` | `/api/v1/threads/{thread_id}/similar/{candidate_id}/mark` | Mark Similar Thread |
| `POST` | `/api/v1/threads/{thread_id}/unarchive` | Unarchive Thread |

## Jobs And Approvals (jobs_approvals)

`GET /api/v1/approvals` returns contextual `ActionRequest` records with action
type, risk, command `argv`, workspace, runtime, diff/evidence refs, reason, and
expiration. Action approve/deny and grant revocation require a non-empty
`reason`; approval returns a scoped, expiring, one-use `permissionGrant`.

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/jobs` | List Jobs |
| `POST` | `/api/v1/jobs` | Create Job |
| `POST` | `/api/v1/jobs/{job_id}/actions/{action_id}/approve` | Approve Action |
| `POST` | `/api/v1/jobs/{job_id}/actions/{action_id}/deny` | Deny Action |
| `POST` | `/api/v1/jobs/{job_id}/approve` | Approve Job |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | Cancel Job |
| `POST` | `/api/v1/jobs/{job_id}/retry` | Retry Job |
| `GET` | `/api/v1/approvals` | Approvals |
| `POST` | `/api/v1/permissions/grants/{grant_id}/revoke` | Revoke Permission Grant |

## Workers (workers)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/workers/status` | Worker Status |
| `POST` | `/api/v1/workers/pause` | Worker Pause |
| `POST` | `/api/v1/workers/resume` | Worker Resume |
| `POST` | `/api/v1/workers/run-once` | Worker Run Once |

## Workflows (workflows)

`POST /api/v1/workflows/issue-to-patch` executes the real issue-to-patch
workflow and returns runtime, QA, diff, evidence, approval, and timeline state
without simulated success. `POST /api/v1/workflows/issue-to-pr` executes the
full DeveloperAgent -> QAAgent -> SecurityAgent -> ArchitectAgent -> DevOpsAgent
DAG and returns gate evidence, completion, rework, and timeline state.
`GET /api/v1/workflows/{workflow_id}` returns the workflow audit detail;
missing linked execution records remain absent instead of being synthesized by
the API.

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/workflows` | List Workflows |
| `POST` | `/api/v1/workflows` | Create Workflow |
| `POST` | `/api/v1/workflows/issue-to-patch` | Run Issue To Patch |
| `POST` | `/api/v1/workflows/issue-to-patch/{run_id}/approve` | Approve Issue To Patch |
| `POST` | `/api/v1/workflows/issue-to-patch/{run_id}/promote` | Promote Patch To Branch |
| `POST` | `/api/v1/workflows/issue-to-patch/{run_id}/pull-request` | Create Pull Request From Promoted Branch |
| `POST` | `/api/v1/workflows/issue-to-pr` | Run Issue To Pr |
| `POST` | `/api/v1/workflows/issue-to-pr/{run_id}/approve` | Approve Issue To Pr |
| `POST` | `/api/v1/workflows/issue-to-pr/{run_id}/promote` | Promote Issue To Pr Branch |
| `POST` | `/api/v1/workflows/issue-to-pr/{run_id}/pull-request` | Create Pull Request From Issue To Pr |
| `GET` | `/api/v1/workflows/{workflow_id}` | Get Workflow |
| `POST` | `/api/v1/workflows/{workflow_id}/cancel` | Cancel Workflow |
| `POST` | `/api/v1/workflows/{workflow_id}/pause` | Pause Workflow |
| `POST` | `/api/v1/workflows/{workflow_id}/resume` | Resume Workflow |
| `POST` | `/api/v1/workflows/{workflow_id}/start` | Start Workflow |
| `POST` | `/api/v1/workflows/{workflow_id}/steps/{step_id}/advance` | Advance Workflow Gate |

## Agents, Profiles, Runs, Skills (agents)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/agents` | Agents |
| `POST` | `/api/v1/agents/architect/runs` | Run Architect Agent |
| `GET` | `/api/v1/agents/architect/status` | Architect Agent Status |
| `POST` | `/api/v1/agents/developer/runs` | Run Developer Agent |
| `GET` | `/api/v1/agents/developer/status` | Developer Agent Status |
| `POST` | `/api/v1/agents/devops/runs` | Run Devops Agent |
| `GET` | `/api/v1/agents/devops/status` | Devops Agent Status |
| `POST` | `/api/v1/agents/product-owner/runs` | Run Product Owner Agent |
| `GET` | `/api/v1/agents/product-owner/status` | Product Owner Agent Status |
| `POST` | `/api/v1/agents/qa/runs` | Run Qa Agent |
| `POST` | `/api/v1/agents/research/runs` | Run Research Agent |
| `GET` | `/api/v1/agents/research/status` | Research Agent Status |
| `POST` | `/api/v1/agents/security/runs` | Run Security Agent |
| `GET` | `/api/v1/agents/security/status` | Security Agent Status |
| `GET` | `/api/v1/agent-profiles` | List Agent Profiles |
| `POST` | `/api/v1/agent-profiles` | Upsert Agent Profile |
| `GET` | `/api/v1/agent-runs` | List Agent Runs |
| `POST` | `/api/v1/agent-runs` | Create Agent Run |
| `GET` | `/api/v1/skills` | List Skills |
| `POST` | `/api/v1/skills/sync` | Sync Skills |

## Runtime And CLI Sessions (agents, runtime_integrations)

`GET /api/v1/runtime/providers` is the runtime truth source (`detected`,
`configured`, `available`, `executable`, capabilities, and safety metadata).

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/runtime/providers` | List Runtime Providers |
| `GET` | `/api/v1/runtime/provider-configuration` | List Runtime Provider Configuration |
| `POST` | `/api/v1/cli-sessions` | Start Session |
| `POST` | `/api/v1/cli-sessions/{session_id}/cancel` | Cancel Session |
| `GET` | `/api/v1/cli-sessions/{session_id}/events` | Session Events |

## Model Gateway (agents/model_gateway, provider catalog, provider accounts)

| Method | Path | Summary |
| --- | --- | --- |
| `POST` | `/api/v1/model-gateway/ai-executions` | Execute Ai Execution |
| `GET` | `/api/v1/model-gateway/benchmark-outcomes` | List Benchmark Outcomes |
| `POST` | `/api/v1/model-gateway/benchmark-outcomes` | Create Benchmark Outcome |
| `GET` | `/api/v1/model-gateway/benchmarks` | List Benchmarks |
| `GET` | `/api/v1/model-gateway/budget-rules` | List Budget Rules |
| `POST` | `/api/v1/model-gateway/budget-rules` | Create Budget Rule |
| `PATCH` | `/api/v1/model-gateway/budget-rules/{rule_id}` | Patch Budget Rule |
| `GET` | `/api/v1/model-gateway/cli-runtimes` | List Cli Runtimes |
| `POST` | `/api/v1/model-gateway/cli-runtimes/{runtime_id}/detect` | Detect Cli Runtime |
| `POST` | `/api/v1/model-gateway/cli-runtimes/{runtime_id}/health-check` | Health Cli Runtime |
| `GET` | `/api/v1/model-gateway/cli-sessions` | List Cli Sessions |
| `GET` | `/api/v1/model-gateway/cli-sessions/{session_id}` | Get Cli Session |
| `GET` | `/api/v1/model-gateway/models` | List Models |
| `POST` | `/api/v1/model-gateway/models` | Create Model |
| `PATCH` | `/api/v1/model-gateway/models/{model_id}` | Patch Model |
| `GET` | `/api/v1/model-gateway/overview` | Overview |
| `GET` | `/api/v1/model-gateway/pricing-snapshots` | List Pricing Snapshots |
| `POST` | `/api/v1/model-gateway/pricing-snapshots` | Create Pricing Snapshot |
| `GET` | `/api/v1/model-gateway/provider-limits` | List Provider Limits |
| `POST` | `/api/v1/model-gateway/provider-limits` | Create Provider Limit |
| `GET` | `/api/v1/model-gateway/provider-limits/status` | Provider Limit Status |
| `PATCH` | `/api/v1/model-gateway/provider-limits/{limit_id}` | Patch Provider Limit |
| `GET` | `/api/v1/model-gateway/providers` | List Providers |
| `POST` | `/api/v1/model-gateway/providers` | Create Provider |
| `GET` | `/api/v1/model-gateway/providers/{provider_id}` | Get Provider |
| `PATCH` | `/api/v1/model-gateway/providers/{provider_id}` | Patch Provider |
| `POST` | `/api/v1/model-gateway/providers/{provider_id}/discover-models` | Discover Models |
| `POST` | `/api/v1/model-gateway/providers/{provider_id}/embeddings` | Execute Provider Embedding |
| `POST` | `/api/v1/model-gateway/providers/{provider_id}/health-check` | Provider Health Check |
| `POST` | `/api/v1/model-gateway/providers/{provider_id}/images/edits` | Execute Provider Image Editing |
| `POST` | `/api/v1/model-gateway/providers/{provider_id}/images/generations` | Execute Provider Image Generation |
| `POST` | `/api/v1/model-gateway/providers/{provider_id}/rerank` | Execute Provider Rerank |
| `POST` | `/api/v1/model-gateway/providers/{provider_id}/test-prompt` | Test Prompt |
| `GET` | `/api/v1/model-gateway/role-policies` | List Role Policies |
| `POST` | `/api/v1/model-gateway/role-policies` | Create Role Policy |
| `PATCH` | `/api/v1/model-gateway/role-policies/{policy_id}` | Patch Role Policy |
| `POST` | `/api/v1/model-gateway/route/execute` | Route Execute |
| `POST` | `/api/v1/model-gateway/route/preview` | Route Preview |
| `GET` | `/api/v1/model-gateway/routing-decisions` | List Routing Decisions |
| `GET` | `/api/v1/model-gateway/routing-profiles` | List Routing Profiles |
| `POST` | `/api/v1/model-gateway/routing-profiles` | Create Routing Profile |
| `PATCH` | `/api/v1/model-gateway/routing-profiles/{profile_id}` | Patch Routing Profile |
| `GET` | `/api/v1/model-gateway/usage-ledger` | List Usage Ledger |
| `GET` | `/api/v1/model-gateway/usage-ledger/summary` | Usage Summary |
| `GET` | `/api/v1/providers/catalog` | List Catalog |
| `POST` | `/api/v1/provider-accounts/from-catalog` | Create Account From Catalog |
| `POST` | `/api/v1/provider-accounts/{account_id}/sync-models` | Sync Provider Account Models |

## NVIDIA NIM (nvidia_nim)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/nvidia-nim/preflight` | Local Preflight |

## Ollama (ollama)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/ollama/endpoints` | List Endpoints |
| `POST` | `/api/v1/ollama/endpoints` | Create Endpoint |
| `POST` | `/api/v1/ollama/endpoints/{endpoint_id}/health` | Health Endpoint |
| `POST` | `/api/v1/ollama/endpoints/{endpoint_id}/sync-models` | Sync Models |

## Evidence (evidence)

`POST /api/v1/evidence` accepts `testResultReports` in `junit` and `pytest`
formats and persists normalized `test_results` records. Artifact reads are
token-protected, package-owned, root-confined, and SHA-256 verified.

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/evidence` | List Evidence |
| `POST` | `/api/v1/evidence` | Create Evidence |
| `POST` | `/api/v1/evidence/artifacts/cleanup` | Cleanup Artifacts |
| `POST` | `/api/v1/evidence/artifacts/retention` | Plan Artifact Retention |
| `POST` | `/api/v1/evidence/artifacts/retention/actions` | Apply Artifact Retention Action |
| `GET` | `/api/v1/evidence/{evidence_id}` | Get Evidence |
| `POST` | `/api/v1/evidence/{evidence_id}/artifacts` | Ingest Artifact |
| `GET` | `/api/v1/evidence/{evidence_id}/artifacts/{artifact_id}` | Get Artifact |
| `GET` | `/api/v1/evidence/{evidence_id}/report` | Export Evidence Report |

## Governance (governance)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/governance` | Governance |
| `GET` | `/api/v1/architecture-decisions` | List Architecture Decisions |
| `POST` | `/api/v1/architecture-decisions` | Create Architecture Decision |
| `GET` | `/api/v1/risks` | List Risks |
| `POST` | `/api/v1/risks` | Create Risk |
| `PATCH` | `/api/v1/risks/{risk_id}` | Update Risk |
| `GET` | `/api/v1/next-steps` | List Next Steps |
| `POST` | `/api/v1/next-steps` | Create Next Step |
| `PATCH` | `/api/v1/next-steps/{step_id}` | Update Next Step |

## Policy And Sandbox (security_policy)

`GET /api/v1/policies` returns policies, permission decisions, one-use
permission grants, and sandbox profiles. Sandbox profile mutation requires the
local token and a non-empty `reason`, and records event/audit entries.

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/policies` | List Policies |
| `POST` | `/api/v1/policies/evaluate` | Evaluate Policy |
| `GET` | `/api/v1/sandbox/status` | Sandbox Status |
| `PATCH` | `/api/v1/sandbox/profiles/{profile_id}` | Update Sandbox Profile |
| `POST` | `/api/v1/sandbox/profiles/{profile_id}/revoke` | Revoke Sandbox Profile |

## Memory And Retrieval (memory_retrieval)

`POST /api/v1/retrieval/reindex` requires `projectId`; without persisted real
embeddings it returns `configuration_required` with `indexed=0`.

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/memory` | List Memory |
| `POST` | `/api/v1/memory` | Create Memory |
| `DELETE` | `/api/v1/memory/{memory_id}` | Delete Memory |
| `GET` | `/api/v1/retrieval/status` | Retrieval Status |
| `POST` | `/api/v1/retrieval/search` | Retrieval Search |
| `POST` | `/api/v1/retrieval/reindex` | Retrieval Reindex |

## Prompts (prompts)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/prompts` | List Prompts |
| `POST` | `/api/v1/prompts` | Upsert Prompt |

## Workspaces (workspaces_projects)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/workspaces` | List Workspaces |
| `POST` | `/api/v1/workspaces` | Allocate Workspace |
| `POST` | `/api/v1/workspaces/{workspace_id}/archive` | Archive Workspace |

## Remediations (remediations)

Thread-scoped remediation listing lives under
`GET /api/v1/threads/{thread_id}/remediations` (threads slice).

| Method | Path | Summary |
| --- | --- | --- |
| `POST` | `/api/v1/remediations/{remediation_id}/dismiss` | Dismiss Remediation |
| `POST` | `/api/v1/remediations/{remediation_id}/execute` | Execute Remediation |

## Self-Improvement (self_improvement)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/self-improvement` | Get Self Improvement State |
| `POST` | `/api/v1/self-improvement/lessons` | Record Self Improvement Lesson |
| `POST` | `/api/v1/self-improvement/lessons/{lesson_id}/promote` | Promote Self Improvement Lesson |
| `POST` | `/api/v1/self-improvement/performance-records` | Record Self Improvement Performance |
| `POST` | `/api/v1/self-improvement/proposals` | Create Self Improvement Proposal |

## Credentials (credentials)

Secret values are never persisted or returned by these endpoints; the slice
stores backend references, fingerprints, and audit records only.

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/credentials` | List Credentials |
| `POST` | `/api/v1/credentials` | Create Credential |
| `GET` | `/api/v1/credentials/audit` | List Credential Audit |
| `POST` | `/api/v1/credentials/migrate` | Migrate Credentials |
| `DELETE` | `/api/v1/credentials/{credential_id}` | Delete Credential |
| `POST` | `/api/v1/credentials/{credential_id}/rotate` | Rotate Credential |
| `POST` | `/api/v1/credentials/{credential_id}/validate` | Validate Credential |

## Settings (settings)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/settings` | Get Settings |
| `PUT` | `/api/v1/settings/{key}` | Put Setting |
| `DELETE` | `/api/v1/settings/{key}` | Delete Setting |

## I18n (i18n)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/i18n/catalog` | Get I18N Catalog |
| `PUT` | `/api/v1/i18n/catalog` | Put I18N Catalog |

## Plugins (plugins)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/plugins` | List Plugins |
| `GET` | `/api/v1/plugins/install-events` | List Plugin Install Events |
| `POST` | `/api/v1/plugins/install-local` | Install Local Plugin |
| `POST` | `/api/v1/plugins/scan-local` | Scan Local Plugins |
| `POST` | `/api/v1/plugins/{plugin_id}/disable` | Disable Plugin |
| `POST` | `/api/v1/plugins/{plugin_id}/enable` | Enable Plugin |
| `POST` | `/api/v1/plugins/{plugin_id}/validate` | Validate Plugin |

## Integrations (integrations)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/integrations` | List Integrations |
| `POST` | `/api/v1/integrations/mcp/register` | Register Mcp Server |
| `POST` | `/api/v1/integrations/n8n/configure` | Configure N8N |
| `POST` | `/api/v1/integrations/n8n/emit` | Emit N8N |
| `POST` | `/api/v1/integrations/n8n/emit-event` | Emit N8N Event |
| `POST` | `/api/v1/integrations/n8n/inbound/{token}` | Receive N8N Inbound |
| `GET` | `/api/v1/integrations/n8n/status` | Get N8N Status |
| `POST` | `/api/v1/integrations/n8n/test` | Test N8N Target |
| `POST` | `/api/v1/integrations/n8n/webhook` | Receive N8N Webhook |
| `POST` | `/api/v1/integrations/n8n/webhook-targets` | Upsert N8N Webhook Target |
| `GET` | `/api/v1/ide-connections` | List Ide Connections |
| `POST` | `/api/v1/ide-connections` | Upsert Ide Connection |
| `GET` | `/api/v1/open-design` | Open Design |

## Legacy Read Models (sessions_chats, pipelines)

| Method | Path | Summary |
| --- | --- | --- |
| `GET` | `/api/v1/legacy/sessions` | List Sessions |
| `GET` | `/api/v1/legacy/chats` | List Chats |
| `GET` | `/api/v1/legacy/pipelines` | List Pipelines |

Coverage is enforced by `tests_py/test_python_control_center.py`,
`tests_py/test_phase2_control_plane_foundation.py`,
`tests_py/test_phase3_to_6_control_plane_runtime.py`,
`tests_py/test_hard_cutover_no_removed_compat.py`, and Playwright smoke tests under
`tests_web/`.
