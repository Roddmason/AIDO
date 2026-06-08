# Workflows

Workflows are first-class control-plane entities, not informal chat threads.

## Current Foundation

SQLite tables exist for:

- workflow definitions
- workflow runs
- workflow steps
- workflow edges
- workflow events

API coverage:

- create/list workflows
- get a workflow
- start, pause, resume, and cancel workflow runs
- advance governed workflow gates through
  `POST /api/v1/workflows/{workflowId}/steps/{stepId}/advance`
- workflow detail includes linked workspaces and evidence packages by
  `workflow_run_id`
- workflows can declare controlled steps through `metadata.steps`
- `issue_to_patch` can run as a real runtime slice only through provider
  truth, workspace allocation, policy, QA evidence, and approval gates

## Target Lifecycle

```text
idea_intake
-> project_discovery
-> backlog_generation
-> architecture_review
-> sprint_plan
-> workspace_create
-> implementation
-> local_tests
-> qa_validation
-> technical_review
-> pr_creation
-> release_candidate
```

Workflow execution should use the existing job queue until distributed durable
execution is justified. Temporal is not needed for the local MVP.

The job worker is deliberately fail-closed. Queueing, leasing, approval, and
run records are real control-plane state, but a job may become `completed` only
after a configured executor actually performs the work. Built-in job kinds with
no executor return `failed` with `configuration_required`; unknown kinds return
`failed` with `unsupported_job_kind`.

## Issue To Patch Completion Contract

`issue_to_patch` is intentionally fail-closed. The workflow runner may create
jobs, workspaces, evidence packages, artifacts, and approval requests, but it
can report `completed` only when all of these are true:

- an executable runtime provider with `issue_to_patch` capability was used;
- the implementation ran inside an allocated Git worktree workspace;
- runtime execution entered through `ToolBroker` with structured `argv`;
- real git status/diff evidence and a non-empty patch artifact were captured;
- QAAgent executed real allowlisted commands through `ToolBroker` and produced
  a passed verdict from exit codes plus artifact hashes;
- an evidence package was persisted and linked to the agent run;
- `requireApproval=false`, or the workflow stops at `evidence_ready` with a
  review action request.

Unavailable providers, missing executable commands, missing QA commands,
missing diff evidence, missing patch artifacts, skipped QA, failed QA, or
pending approval produce `runtime_unavailable`, `qa_failed`, or
`evidence_ready`, never a productive success state. `requireApproval` defaults
to true for sensitive patch output.

## PR, Release And Retro Gates

The `pr_release_retro` kind and declared `metadata.steps` support controlled
steps named `pr_review`, `release_gate` and `retro`.

- `pr_review` is marked as blocked pending QA evidence.
- production `release_gate` creates a `release.production` job in
  `approval_required` state with a pending action request.
- `retro` seeds governance decision, risk and next-step records so retrospective
  work remains auditable.
- force push, direct push to `main`/`master` and direct main edit metadata are
  rejected at workflow creation.

These gates do not perform deploys. They create auditable control points that
must be resolved by jobs, approvals, evidence and governance records.

Gate advancement is explicit:

- `pr_review` advances only when the workflow run has a passed QA evidence
  package. The optional `evidencePackageId` request field can bind the gate to a
  specific package.
- production `release_gate` advances only after all associated
  `release.production` action requests are approved.
- `retro` advances only when the seeded governance decision, risk or next-step
  records exist for that workflow step.
- every blocked or advanced attempt writes a `workflow.gate.*` workflow event
  and audit event. Blocked attempts return `409`; they do not execute deploys or
  mutate the main branch.

## Traceability

Workspace allocation can carry `workflowRunId` and `workflowStepId`. Evidence
packages carry `workflowRunId`, `workflowStepId`, `jobId`, `agentRunId`,
`workspaceId`, `runtimeId`, `artifactIds`, and `diffSummary`. `GET
/api/v1/workflows/{id}` aggregates these links so the UI can inspect a workflow
without inventing client-side joins.

## Testing

Run:

```powershell
uv run pytest tests_py/test_workflow_pr_release_retro_control.py tests_py/test_phase7_workspace_policy_traceability.py tests_py/test_phase8_governance.py -q
```

## Real Capability Table

| Capability | Real state | Endpoint/UI | Tests | Limitations |
| --- | --- | --- | --- | --- |
| Workflow CRUD/run control | Implemented for create/list/get/start/pause/resume/cancel and step advance. | `/api/v1/workflows`, `/api/v1/workflows/{id}`, Workflows UI. | Workflow control tests. | Workflows are local control-plane records; distributed durable execution is not part of the local MVP. |
| `issue_to_patch` | Implemented as real fail-closed workflow execution. | `POST /api/v1/workflows/issue-to-patch`, Command Center. | `tests_py/test_aido_real_runtime_slice.py`, web Command Center tests. | Completion requires executable runtime, Git worktree, real diff, passed QA evidence, evidence package, and no pending approval. |
| PR/release/retro gates | Implemented as auditable control gates. | Workflow step advance endpoint, Workflows UI. | `tests_py/test_workflow_pr_release_retro_control.py`. | These gates do not deploy or mutate protected branches. |
| Workflow traceability | Implemented by joining workflow runs with workspaces, jobs, agent runs, evidence, tool calls, policy decisions, and approvals. | `GET /api/v1/workflows/{id}`, overview/workflow inspectors. | Traceability and frontend tests. | Traceability depends on linked records produced by actual executions; missing execution remains visible as blocked state. |
