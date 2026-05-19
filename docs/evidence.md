# Evidence And QA

Evidence packages are required to make QA decisions auditable.

## Implemented Foundation

- `evidence_packages`
- `test_results`
- `qa_verdicts`
- `artifacts` table reserved for future file/screenshot/log references
- workspace archive snapshots stored in `diffRefs`

## QA Gate

A package cannot be marked `passed` unless it includes at least one evidence
signal:

- test results
- diff references
- screenshot references

This prevents a reviewer or agent from approving work without recorded proof.

`GET /api/v1/evidence/{id}` returns both the package and normalized
`testResultRecords` plus `artifacts` from SQLite. The package JSON is useful for
UI rendering; the table records are the queryable operational evidence.

Evidence creation also promotes large inline evidence to artifacts:

- `logs[].content` above the inline threshold becomes an `execution_log`
  artifact.
- `screenshotRefs[].contentBase64` becomes a `screenshot` artifact.
- `testResultReports[]` accepts `junit` XML and `pytest` terminal summaries,
  then normalizes them into queryable `test_results` rows. JUnit XML with
  `DOCTYPE` or entity declarations is rejected before parsing.
- The evidence package keeps artifact IDs, hashes, sizes, and display metadata,
  not the original large payload.

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
the Docker sandbox adapter. The package records the command, tool-call ID,
execution mode, return code, timeout flag, duration, and blocked reason when
applicable. The agent run output stores the evidence package ID in
`evidence_refs`, so reviewers can trace:

```text
agent run -> tool call -> policy decision -> sandbox result -> evidence package
```

Docker execution remains catalog- and policy-gated. Evidence capture is not a
permission grant; it is the audit trail produced after the policy engine has
allowed a low-risk action or after the broker has consumed a matching one-use
permission grant for a sensitive action.

Large stdout/stderr streams are promoted before the tool call is persisted.
When a stream exceeds the inline log threshold, the control plane writes it to
`.tmp/evidence-artifacts`, stores SHA-256 and size metadata, removes the raw
stream from `agent_tool_calls.payload.executionResult`, and attaches the
artifact to the generated evidence package. Small outputs remain inline for
quick operational inspection.

## Next Steps

- Require evidence package IDs in technical review outputs.
