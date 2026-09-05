# P0 operational architecture

This document describes the implemented Windows-first boundary, not a claim that all release
gates have passed. Current evidence and remaining gates are in [verification](p0-verification-report.md).

## Ownership and flow

```text
Operator / React console
  -> FastAPI process: authenticate, validate, persist acceptance, return HTTP 202
     -> SQLite: execution + job + ordered events; sealed input reference
        -> Separate worker process: leader lease + fencing token + host admission
           -> Contained execution child: registered operation handler
              -> ToolBroker / policy / approval -> supervised productive process or remote call
                 -> terminal state + redacted artifacts + SHA-256 + resource evidence
```

The local launcher owns the API and worker children. API lifespan does not start a worker.
A worker crash therefore does not take down the API. Additional workers are standby contenders,
not extra execution capacity. P0 accepts one active job, one heavy workload and at most two
independently admitted light branches. The worker starts paused unless the operator has explicitly
changed its durable configuration/control state.

The worker CLI does not import HTTP routers or numerical retrieval backends. The API factory is
loaded only in API mode. Lightweight registered operations take priority over queued conversations
so configuration/health repairs cannot be blocked by the conversation they must unblock. Remaining
jobs retain FIFO order; this is not multi-project fairness. Conversation preflight remains mandatory,
while setup operations do not require an already executable model. All still require host admission.

`workers/leadership.py` owns leader acquisition, renewal, expiry and monotonically increasing fencing.
Job claims, renewals and completion verify ownership. Expiry is not permission to overlap an old
native tree: resource recovery retains capacity until process cleanup is resolved.
The OS loop passes its acquired token explicitly to the batch: a heartbeat clearing mutable state
cannot downgrade a fenced claim to an unfenced compatibility call.

## Short HTTP and database boundaries

`ControlCenterRuntime.operation_connection()` supplies a connection scoped to the request or operation;
there is no process-wide HTTP mutex or shared active transaction across requests. `platform.connection`
inside a bound slice resolves the contextual connection. Bootstrap owns a separate connection.
`check_same_thread=False` is driver configuration, not authorization to share a connection concurrently.

WAL, foreign keys, explicit short transactions and bounded busy waits remain enabled. External calls,
Git, MCP, subprocesses and provider waits must occur outside transactions; executable boundary guards
and architecture tests enforce this. Low-priority metrics skip contended writes instead of cancelling
a healthy productive child. Passive WAL checkpoints do not force readers out.

SQLite must include the [WAL-reset fix](https://www.sqlite.org/wal.html#walresetbug): 3.51.3+,
or patched 3.50.7/3.44.6. Withdrawn 3.52 is rejected. The checked project interpreter is Python
3.13.15 with SQLite 3.53.1. Startup checks the actual linked SQLite library, not the Python label.
Use a local filesystem on one host; WAL is not a distributed database or a network-share protocol.

## Native execution boundary

`process_supervision/` centralizes productive process creation and capture. On Windows the process is
created suspended, assigned to a Job Object, then resumed. The job applies kill-on-close, process,
memory and CPU limits; productive work runs at lower priority. Cancellation is durable, observed by
the owner/watchdog and followed by native cleanup. Recovery compares PID **and creation time** before
acting. A PID alone is not ownership evidence.

The POSIX implementation uses process groups; Linux/macOS native behavior still requires platform
validation. A Windows Job Object is a lifecycle/resource container, **not** a security sandbox or an
assurance that a privileged external broker cannot escape it. See
[Microsoft's Job Objects contract](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
Unrelated Unreal, browser, Node and Python processes are never cleanup targets.

Each managed tree records execution/lease IDs, command fingerprint, root identity, start/end,
exit/cancellation reason, CPU time and peak memory. Redacted stdout/stderr stream to files; bounded
inline excerpts do not replace complete artifact evidence. Capture has a 64 MiB limit per stream;
overflow terminates the execution and marks evidence truncated rather than claiming complete output.

## Async contracts and readiness

`executions/` supplies acceptance, status, incremental events and cancellation. Productive endpoint
responses are `202` envelopes, not domain results. The generated client preserves that distinction;
the domain adapter observes completion explicitly. Browser observation timeout/abort does not cancel
server work. Operations UI cancellation requires a human reason and backend `canCancel` permission.

Queued sensitive inputs are encrypted separately (Windows DPAPI; native keyring on supported POSIX
hosts), with no plaintext fallback. Native credentials stay in their provider/native store.
Artifact/input retention limitations are listed in [recovery](p0-recovery.md).

Readiness independently reports installation, authentication, configuration, global/project flags,
policy, recent health and host admission. `enabled` never means `executable`. Read-only status uses
cached/persisted evidence; explicit productive validation is queued. Codex compatibility binds
required capabilities and an approved real smoke receipt to the executable and contract fingerprints.
Changing the binary invalidates that evidence. Model aliases resolve against the enabled catalog;
legacy seeds do not prove installed model availability.

## Additive schema

| Migration | Boundary |
| --- | --- |
| 59 | Worker leadership, fencing, control and heartbeats |
| 60 | Host samples, leases, resource usage and violations |
| 61–63 | Managed process lifecycle, cancellation and native identity |
| 64 | Durable executions and ordered events |
| 65 | Codex capability/smoke evidence |
| 66 | Branch-owned resource leases |
| 67 | Runtime/model compatibility migration |

The dispatcher advertises schema 67. Upgrade verification creates schema 58 using the actual
baseline commit, backs it up, restores to a new directory and applies current initialization twice.
It checks schema, integrity, foreign keys and preservation of existing project rows. This is stronger
than a fresh-database test, but does not prove every possible historical operator dataset.
