# ADR: isolate the control plane from productive execution

Date: 2026-09-04. Status: accepted for P0 implementation; release acceptance remains gated.

## Context

The baseline coupled a FastAPI process, a worker thread, a shared SQLite connection and a global HTTP
mutex. A slow subprocess/provider operation could monopolize control-plane access. Process cleanup
was distributed among adapters and lacked one durable owner/resource boundary.

## Decision

Use a separate local worker process with durable SQLite leadership and fencing. Productive HTTP
operations persist acceptance and return 202. Admit host capacity before launch; contain productive
trees in Windows Job Objects (POSIX process groups on other supported source targets). Persist
cancellation, lifecycle events, resource reservations and redacted artifact evidence incrementally.
Keep request/operation-scoped connections, WAL and short transactions. Do not add a distributed
queue/server for a single-host P0 workload.

SQLite's current [WAL-reset advisory](https://www.sqlite.org/wal.html#walresetbug) requires a patched
linked library; a Python version label alone does not prove that condition. Native container behavior
follows [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

## Tradeoffs

- Another local process and durable lifecycle state add complexity, but preserve operator control when
  a worker crashes or a productive operation stalls.
- One heavy job limits throughput deliberately to protect an interactive development host. More
  parallelism requires measured evidence and a later policy change, not a hidden default.
- SQLite remains a single-host writer-constrained store. This design does not promise multi-host
  scheduling, network-share WAL or distributed failover.
- Process containment controls resource/lifecycle behavior, not privilege isolation. Existing
  ToolBroker, policy, approval, workspace and credential boundaries remain mandatory.
- A 202 API requires clients to observe completion. Generated types and domain adapters expose that
  transition rather than pretending the initial response is a terminal result.

## Alternatives rejected for P0

More worker threads or a wider global lock do not isolate crashes or improve control-plane
responsiveness. Broad process-name cleanup is unsafe. Removing WAL or disabling quality checks hides
the underlying problem. A distributed broker introduces operational dependencies not justified by
the current one-host scope.

## Verification

See [P0 verification](../operational-hardening/p0-verification-report.md) for actual commands/results,
and [architecture](../operational-hardening/p0-architecture.md) for current contracts. Approval of this
ADR is not evidence of passed tests, provider compatibility, native POSIX validation or Shipping.
