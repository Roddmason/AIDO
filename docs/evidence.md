# Evidence And QA

Evidence packages are required to make QA decisions auditable.

## Implemented Foundation

- `evidence_packages`
- `test_results`
- `qa_verdicts`
- `artifacts` rows for verified file, screenshot, report, patch, status, log,
  security, manifest, and model-call evidence
- workspace archive snapshots stored in `diffRefs`
- direct linkage fields for `workflowStepId`, `jobId`, `agentRunId`,
  `workspaceId`, `runtimeId`, `artifactIds`, and `diffSummary`

## Evidence Package Contract

Every `EvidencePackageRecord` returned by the API includes the operational
closure fields `workflowRunId`, `jobId`, `agentRunId`, `workspaceId`,
`runtimeId`, `runtimeHealth`, `modelCalls`, `toolCalls`, `policyDecisions`,
`approvals`, `qaVerdict`, `evidenceSource`, `artifacts`, `diffSummary`, `hashes`, and
`createdAt`. Workflow completion requires a completion-grade package: real
runtime health, linked job/agent/workspace/runtime records, artifact refs, and
SHA-256 hashes. Direct agent runs may have `workflowRunId=null`, but they still
must include the concrete executor identity, artifacts, and hashes before
reporting `completed`.

`qaVerdict` is an enum in OpenAPI: `not_started`, `passed`, `failed`,
`blocked`, `needs_human_review`, `evidence_collected`,
`architecture_reviewed`, `devops_risk`, `devops_blocked`,
`security_passed`, `security_blocked`, and `skipped_with_reason`.

`evidenceSource` separates how a package was produced:

- `operator_attested`: a human/operator statement. This can document review,
  but it cannot create a QA pass.
- `evidence_collected`: artifacts, diffs, screenshots, reports, or logs were
  collected without proving a fresh command execution pass.
- `qa_passed_by_command`: QA passed because command execution produced a real
  result with policy, tool-call, exit-code, and hash evidence.
- `verified_completion`: a workflow completed with real QA command evidence
  plus the required runtime/workspace/artifact closure.

`DeveloperAgentRunner` is the canonical implementation evidence producer.
`issue_to_patch` consumes the DeveloperAgent evidence package, including the
`developer-agent.diff` patch artifact, DeveloperAgent manifest, QA command
evidence, runtime logs, tool calls, and model calls. The workflow may enrich the
same package with gate-specific artifacts such as generated security findings,
promotion evidence, or pull-request evidence, but it must not recreate a second
implementation patch or QA summary. Missing credentials, runtimes, tools,
endpoints, or artifact integrity do not produce a simulated success; they keep
the run blocked, unavailable, or configuration-required with a technical reason.

## QA Gate

A package cannot be marked `passed` through `POST /api/v1/evidence` unless it
uses `evidenceSource=qa_passed_by_command` or `verified_completion` and includes
real command result evidence:

- at least one `testResults[]` item with `status=passed`;
- `execution` recorded as `restricted_subprocess` or `docker`;
- `toolCallId` linked to package `toolCalls[]`;
- `exitCode=0`;
- `artifactHashes.stdoutHash`, `artifactHashes.stderrHash`, and
  `artifactHashes.outputArtifactHash`;
- package-level artifact `hashes`;
- a referenced policy decision with `decision=allow`.

Diff refs, screenshots, artifact refs, parsed JUnit XML, or pasted pytest
summaries can be stored as `evidence_collected`, but they are not sufficient for
`qaVerdict=passed`.

`GET /api/v1/evidence/{id}` returns both the package and normalized
`testResultRecords` plus `artifacts` from SQLite. The package JSON is useful for
UI rendering; the table records are the queryable operational evidence.

Evidence creation also promotes large inline evidence to artifacts:

- `logs[].content` is redacted for secrets, and content above the inline
  threshold becomes an `execution_log` artifact.
- `screenshotRefs[].contentBase64` becomes a `screenshot` artifact.
- `testResultReports[]` accepts `junit` XML and `pytest` terminal summaries,
  then normalizes them into queryable `test_results` rows. JUnit XML with
  `DOCTYPE` or entity declarations is rejected before parsing.
- The evidence package keeps artifact IDs, hashes, sizes, and display metadata,
  not the original large payload.
- Patch contents are not redacted by default because rewriting patches can
  invalidate implementation evidence. Metadata around patch references is
  redacted, and large patch files are hash-verified like other artifacts.

When evidence is linked to model/runtime usage, creation also feeds the Model
Gateway benchmark outcomes table. `POST /api/v1/evidence` accepts
`usageLedgerId` or explicit `providerId`, `model`, `runtimeType`, `role`,
`workflowStepId`, `jobId`, cost and latency fields. If enough model identity is
present, the control plane records a `model_benchmark_outcomes` row with
success, QA pass and rework inferred from real QA evidence, `qaVerdict`, and
test result status. A manual or artifact-only package with `qaVerdict=passed`
does not count as a QA pass.
Evidence-ingested benchmark outcomes are marked `provenance=automated_run`.
Only `automated_run` and `release_validation` benchmark outcomes count as
objective samples for routing; operator-reported rows stay audit-visible but do
not prove model quality.
Prompts and raw secrets are not copied into benchmark metadata.

## Artifact Retrieval

Artifacts can be read through:

```text
GET /api/v1/evidence/{evidenceId}/artifacts/{artifactId}
```

The endpoint requires the local control token, verifies that the artifact belongs
to the requested evidence package, confines reads to `.tmp/evidence-artifacts`,
and validates the SHA-256 hash before returning a file. Paths outside the
artifact root are rejected; missing files and hash mismatches are treated as
integrity failures, not silent degraded reads.

Artifacts produced after evidence package creation can be ingested through:

```text
POST /api/v1/evidence/{evidenceId}/artifacts
```

The endpoint requires the local control token and accepts bounded `content` or
`contentBase64` payloads for allowlisted artifact kinds. It writes under
`.tmp/evidence-artifacts`, computes SHA-256, records event/audit entries, and
does not accept client-supplied filesystem paths.

## UI Evidence Viewer

The Evidence & QA page must use `GET /api/v1/evidence/{evidenceId}` for package
detail. The viewer shows package metadata, workflow/job/agent/workspace/runtime
links, QA verdict, evidence source, artifacts with full SHA-256 hashes, and
download actions.
Diff, security findings, model-call, and tool-call evidence are rendered from
linked artifacts or package fields after frontend redaction of secret-like text.

The patch artifact is not treated as success merely because it exists. If the
DeveloperAgent `git_patch` artifact is missing or has no real hunk
additions/deletions, the UI shows `no real changes` and must not present the
patch as proof of completed implementation.

## Artifact Cleanup

Artifact cleanup is intentionally conservative. The control plane only removes
physical files that are inside `.tmp/evidence-artifacts` and are not referenced
by SQLite `artifacts` rows.

```text
POST /api/v1/evidence/artifacts/cleanup
```

The endpoint requires the local control token and accepts:

```json
{ "dryRun": true }
```

`dryRun` defaults to `true`. Dry-run returns detected orphan files without
deleting them. When `dryRun=false`, only orphan files are deleted; referenced
artifacts are counted and preserved. Each cleanup records an operational event
and audit entry.

Referenced artifacts use governance-first retention. Artifacts with
`metadata.expiresAt` at or before the reviewed timestamp are surfaced through:

```text
POST /api/v1/evidence/artifacts/retention
```

The endpoint requires the local control token, returns the expired referenced
artifacts, records event/audit entries, and creates governance risks per
project. It does not delete referenced files automatically; deletion requires a
separate explicit audited action after the evidence owner reviews the package.

Reviewed expired artifacts can be resolved through:

```text
POST /api/v1/evidence/artifacts/retention/actions
```

The endpoint requires the local control token, a non-empty `reason`,
`artifactIds`, and `action=export|delete`. `export` records an audit manifest
without touching the file. `delete` is deliberately narrower: the artifact must
be referenced, expired, root-confined under `.tmp/evidence-artifacts`, and hash
verified before the physical file is removed. The SQLite `artifacts` row stays
in place with `metadata.retentionAction` so evidence packages remain auditable.

## QA Report Export

Evidence packages can be exported as local Markdown QA reports through:

```text
GET /api/v1/evidence/{evidenceId}/report
```

The endpoint requires the local control token and records an audit entry. The
report includes verdict, test plan, checklist, normalized test result records,
diff reference counts, artifact IDs/hashes, and risk notes. It deliberately
does not print local artifact filesystem paths; reviewers can fetch artifacts
through the authenticated artifact endpoint when needed.

## Workspace Archive Evidence

`POST /api/v1/workspaces/{id}/archive` now creates an evidence package with
`qaVerdict=evidence_collected`. The package includes a `workspace_snapshot`
diff reference containing relative file paths, sizes, SHA-256 hashes, and a
truncation flag. Runtime artifacts such as `node_modules`, `dist`, caches,
`.tmp`, `.venv`, and `__pycache__` are excluded from the snapshot.

For Git worktrees, archive captures `git_diff` before cleanup. The reference
includes porcelain status records, name-only changed files, a bounded diff stat,
and patch evidence. Small patches can stay inline. Large patches are promoted
to `artifacts` with `kind=git_patch`, SHA-256 hash, local path, and size
metadata; the `git_diff` reference keeps only `patchArtifactId`, hash, and size.
This must happen before `git worktree remove`; capturing after cleanup would
silently lose the most important evidence.

## Agent Tool Execution Evidence

Agent tool calls that execute through the control plane create an evidence
package automatically. This covers both the restricted subprocess fallback and
the Docker sandbox adapter, plus `runtime_adapter:mcp` calls. The package
records the command or MCP operation, tool-call ID, execution mode, return code,
timeout flag, duration, and blocked/unavailable reason when applicable. MCP
`unavailable` or `configuration_required` tool-call states are stored in
test-results as `skipped_with_reason` with the original runtime status in
metadata, so diagnostic evidence is not converted into passed QA. The agent run
output stores the evidence package ID in `evidence_refs`, so reviewers can
trace:

```text
agent run -> tool call -> policy decision -> sandbox result -> evidence package
```

Docker execution remains catalog- and policy-gated. Evidence capture is not a
permission grant; it is the audit trail produced after the policy engine has
allowed a low-risk action or after the broker has consumed a matching one-use
permission grant for a sensitive action.

Large stdout/stderr streams are redacted and promoted before the tool call is persisted.
When a stream exceeds the inline log threshold, the control plane writes it to
`.tmp/evidence-artifacts`, stores SHA-256 and size metadata, removes the raw
stream from `agent_tool_calls.payload.executionResult`, and attaches the
artifact to the generated evidence package. Small outputs remain inline for
quick operational inspection.

## Technical Review Gate

Technical review agent runs must include at least one evidence package ID in
`input.evidenceRefs` before they can produce an approving verdict. If a
`technical_lead` run for a `technical_review` step omits those refs, the run is
recorded as `failed` with `verdict=blocked` and an explicit mitigation. This
prevents architecture or code-review approval from drifting away from the QA
evidence ledger.

## Real Capability Table

| Capability | Real state | Endpoint/UI | Tests | Limitations |
| --- | --- | --- | --- | --- |
| Evidence package ledger | Implemented with package JSON plus normalized test result and artifact rows. | `GET /api/v1/evidence`, `GET /api/v1/evidence/{id}`, Evidence & QA UI. | Evidence repository/API tests and web evidence tests. | A package with no evidence signal cannot be treated as QA passed. |
| `issue_to_patch` evidence | Implemented for runtime health, diff/status artifacts, QA results, policy/security findings, manifest, and hashes. | Workflow response, Evidence & QA UI. | `tests_py/test_aido_real_runtime_slice.py`. | Missing runtime or failed QA still creates diagnostic evidence; it does not imply completion. |
| Artifact download | Implemented with token, evidence ownership, root confinement, and SHA-256 validation. | `GET /api/v1/evidence/{evidenceId}/artifacts/{artifactId}`. | Artifact retrieval tests. | A missing or hash-mismatched file is an integrity failure. |
| Artifact cleanup/retention | Implemented for orphan cleanup dry-run/delete and governance-first expired referenced artifact review. | `/api/v1/evidence/artifacts/cleanup`, `/retention`, `/retention/actions`. | Evidence retention tests. | Referenced expired artifacts are not automatically deleted; delete requires explicit audited action. |
| QA report export | Implemented as local Markdown report generation. | `GET /api/v1/evidence/{evidenceId}/report`. | Evidence report tests. | Reports omit local filesystem paths; artifacts must be fetched through the authenticated endpoint. |
