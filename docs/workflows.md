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
- workflow detail includes linked workspaces and evidence packages by
  `workflow_run_id`

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

## Traceability

Workspace allocation can carry `workflowRunId` and `workflowStepId`. Evidence
packages carry `workflowRunId`. `GET /api/v1/workflows/{id}` aggregates these
links so the UI can inspect a workflow without inventing client-side joins.
