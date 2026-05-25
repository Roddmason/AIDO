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
packages carry `workflowRunId`. `GET /api/v1/workflows/{id}` aggregates these
links so the UI can inspect a workflow without inventing client-side joins.

## Testing

Run:

```powershell
uv run pytest tests_py/test_workflow_pr_release_retro_control.py tests_py/test_phase7_workspace_policy_traceability.py tests_py/test_phase8_governance.py -q
```
